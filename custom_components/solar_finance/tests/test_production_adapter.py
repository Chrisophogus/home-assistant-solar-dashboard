"""Runnable pure adapter tests against the frozen, unmodified engine."""
import copy
import sys
import unittest
from pathlib import Path
from decimal import Decimal as D
from test_engine import e
from solar_finance.production_adapter import replay_once,window_view,cell_to_engine,attach_review_output,hourly_report

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'usable_capacity_kwh':'10','reserve_percent':'0',
                  'charge_efficiency':'.95','discharge_efficiency':'.94',
                  'charge_limit_kw':'5.5','discharge_limit_kw':'5.5',
                  'battery_charge_dc_limit_kw':'8.8','battery_discharge_dc_limit_kw':'9.6',
                  'inverter_ac_limit_kw':'5.5','grid_limit_unreported':True,
                  'grid_import_limit_kw':'20','export_limit_kw':'5.5',
                  'standby_loss_kw':'0','pv_ac_factor':'1',
                  'baseline_import_gbp_kwh':'.3','baseline_standing_gbp_day':'.4'}
        self.tariffs=[{'id':k,'kind':k,'effective_from':'2026-01-01T00:00:00Z',
                       'standing':'.4' if k=='import' else '0',
                       'bands':[[0,1440,'.2' if k=='import' else '.1']]} for k in ('import','export')]
    def part(self,lo,hi,load='1',**flags):
        return {'part':{'start':lo,'end':hi,'load':load,'import':load,
                        'export':'0','pv':'0','charge':'0','discharge':'0'},
                'physical':copy.deepcopy(self.cfg),**flags}
    def run_parts(self,parts,**kw):return replay_once(parts,self.tariffs,50,e,**kw)
    def test_known_zero_gap_join_preserves_inventory_and_cash(self):
        whole=self.run_parts([self.part('2026-11-04T12:00Z','2026-11-04T14:00Z','2')])
        split=self.run_parts([self.part('2026-11-04T12:00Z','2026-11-04T13:00Z'),self.part('2026-11-04T13:00Z','2026-11-04T14:00Z')])
        for a,b in zip(whole['terminal_stored'],split['terminal_stored']):
            self.assertLess(abs(a-b),D('1e-24'))
        self.assertLess(abs(whole['rows'][0]['cost_lower']-sum(r['cost_lower'] for r in split['rows'])),D('1e-24'))
    def test_unknown_gap_widens_instead_of_actual_SOC_reset(self):
        out=self.run_parts([self.part('2026-11-04T12:00Z','2026-11-04T13:00Z'),self.part('2026-11-04T13:03Z','2026-11-04T14:00Z')])
        self.assertEqual(len(out['gaps']),1)
        lo,hi=out['rows'][1]['stored_before'];self.assertLess(lo,hi)
        self.assertNotEqual(lo,D(5));self.assertNotEqual(hi,D(5))
    def test_window_is_reduction_not_measured_reanchor(self):
        out=self.run_parts([self.part('2026-10-31T23:00Z','2026-11-01T00:00Z'),self.part('2026-11-01T00:00Z','2026-11-01T01:00Z')])
        v=window_view(out,'2026-11-01T00:00Z','2026-11-01T01:00Z',e)
        self.assertEqual(D(v['simulated_initial_stored_range'][0]),out['rows'][0]['stored_after'][0]);self.assertEqual(v['soc_resets'],[])
    def test_overlapping_replacement_rejected(self):
        with self.assertRaises(ValueError):self.run_parts([self.part('2026-11-04T12:00Z','2026-11-04T13:00Z'),self.part('2026-11-04T12:30Z','2026-11-04T13:30Z')])
    def test_reconstruction_requires_provenance(self):
        with self.assertRaises(ValueError):self.run_parts([self.part('2026-11-04T12:00Z','2026-11-04T13:00Z',reconstructed=True)])
    def test_actual_gross_cash_never_replaced_by_net_clipping(self):
        p=self.part('2026-11-04T12:00Z','2026-11-04T13:00Z');p['part'].update({'import':'2','export':'1'})
        r=self.run_parts([p])['rows'][0]
        self.assertAlmostEqual(r['actual'],D('.3')+D('.4')/24,places=24)
        self.assertEqual(r['actual_import'],2);self.assertEqual(r['actual_export'],1)
    def test_cell_layer_preserves_port_power_limit(self):
        c=cell_to_engine(self.cfg,'10','.9','.8','.005')
        self.assertEqual(D(c['charge_efficiency']),D('.95')*D('.9'))
        self.assertEqual(D(c['discharge_efficiency']),D('.94')*D('.8'))
        self.assertEqual(D(c['battery_charge_dc_limit_kw'])/D('.9'),D('8.8'))
        self.assertEqual(D(c['battery_discharge_dc_limit_kw'])*D('.8'),D('9.6'))
        self.assertEqual(self.cfg['standby_loss_kw'],'0')
    def test_model_change_does_not_reset_inventory(self):
        parts=[self.part('2026-11-04T12:00Z','2026-11-04T13:00Z'),self.part('2026-11-04T13:00Z','2026-11-04T14:00Z')]
        parts[1]['physical']['usable_capacity_kwh']='11'
        with self.assertRaises(ValueError):self.run_parts(parts)
    def test_unknown_controls_enclose_dense_interior_samples(self):
        p=self.part('2026-11-04T12:00Z','2026-11-04T13:00Z','4',uncertain=True)
        enclosing=self.run_parts([p])['rows'][0]
        for reserve in range(0,101,10):
            for power in ('0','.5','1','2.5','4','5.5'):
                c=copy.deepcopy(self.cfg);c.update(reserve_percent=str(reserve),discharge_limit_kw=power)
                result=e.simulate(p['part'],D(5),c,policy='cheap-window-self-consumption-v2')
                self.assertLessEqual(enclosing['stored_after'][0],result['stored']);self.assertLessEqual(result['stored'],enclosing['stored_after'][1])
                cash=result['import']*D('.2')+D('.4')/24
                self.assertLessEqual(enclosing['cost_lower'],cash+D('1e-24'));self.assertLessEqual(cash,enclosing['cost_upper']+D('1e-24'))
    def test_boundary_crossing_is_not_interpolated(self):
        with self.assertRaises(e.Pending):self.run_parts([self.part('2026-11-04T04:59Z','2026-11-04T05:01Z')])
    def test_report_attachment_preserves_legacy_and_input(self):
        original={'periods':{'month':{'values':{'schedule':'42'},'ordinary_schedule_scenario':{'old':True}}}}
        revised=attach_review_output(original,{'month':{'schedule_saving_range':['1','2']}})
        self.assertEqual(revised['periods']['month']['values'],original['periods']['month']['values'])
        self.assertEqual(original['periods']['month']['ordinary_schedule_scenario'],{'old':True})
    def test_coarsening_preserves_observed_cash_on_fixed_tariff(self):
        parts=[self.part('2026-11-04T12:00Z','2026-11-04T12:05Z','.1'),self.part('2026-11-04T12:05Z','2026-11-04T12:15Z','.2')]
        fine=self.run_parts(parts);coarse=self.run_parts([self.part('2026-11-04T12:00Z','2026-11-04T12:15Z','.3')])
        self.assertAlmostEqual(sum(r['actual'] for r in fine['rows']),coarse['rows'][0]['actual'],places=24)
    def test_hourly_hook_uses_one_trajectory(self):
        parts=[self.part('2026-10-31T23:00Z','2026-11-01T00:00Z'),self.part('2026-11-01T00:00Z','2026-11-01T01:00Z')]
        report={'periods':{'lifetime':{},'month':{}}}
        out=hourly_report(report,parts,self.tariffs,50,e)
        v=out['periods']['month']['ordinary_schedule_scenario']
        self.assertLess(D(v['simulated_initial_stored_range'][0]),D(5))
        self.assertEqual(report,{'periods':{'lifetime':{},'month':{}}})
    def test_deadline_never_publishes_partial_report(self):
        with self.assertRaises(TimeoutError):self.run_parts([self.part('2026-11-04T12:00Z','2026-11-04T13:00Z')],max_seconds=1e-12)

if __name__=='__main__':unittest.main()
