"""Standard-library tests of the repair kernel; no HA or production writes."""
from decimal import Decimal as D
import copy
import unittest
from test_engine import e
from solar_finance.continuity import Trajectory,UnrecoveredGap,recover_cumulative


class ConservationTests(unittest.TestCase):
    def test_telemetry_segmentation_cannot_create_inventory(self):
        whole=Trajectory(D('8.76'),'a');whole.advance('a','z',D(0),D(6)/D('.9409'))
        split=Trajectory(D('8.76'),'a')
        split.advance('a','b',D(0),D(3)/D('.9409'))
        split.advance('b','c',D(0),D(0)) # Recovered measured zero-energy interval.
        split.advance('c','z',D(0),D(3)/D('.9409'))
        self.assertLess(abs(whole.stored-split.stored),D('1e-24'))
        self.assertEqual(split.external_transfer,0)

    def test_unknown_gap_cannot_advance_or_reset_for_free(self):
        t=Trajectory(D(5),'a')
        with self.assertRaises(UnrecoveredGap):t.advance('missing','z',0,1)
        self.assertEqual(t.stored,D(5));self.assertEqual(t.net_charge,0)

    def test_every_settlement_transfer_is_explicit_and_balanced(self):
        t=Trajectory(D(5),'a');t.advance('a','b',1,2)
        s=t.settle(D(7),'.956','.9409','.10','.40','.30')
        self.assertEqual(t.stored-D(5),t.net_charge+t.external_transfer)
        self.assertEqual(s['stored_transfer_kwh'],D(3))
        self.assertGreater(s['cost_lower'],0)
        self.assertLessEqual(s['cost_lower'],s['reference_cost']);self.assertLessEqual(s['reference_cost'],s['cost_upper'])

    def test_surplus_is_not_realised_supplier_credit(self):
        t=Trajectory(D(9),'a');s=t.settle(7,'.956','.9409','.10','.40','.30')
        self.assertEqual(s['cost_upper'],0);self.assertLess(s['reference_cost'],0)

class ReconstructionTests(unittest.TestCase):
    def pair(self):
        a={'timestamp':'2026-11-05T12:00:00+00:00','collection_session':'one','sources':{'plant':'same-meter'},'normalised_unit':'kWh','energy':{k:'10' for k in ('import','export','load','pv','charge','discharge')},'source_timestamps':{k:'2026-11-05T12:00:00+00:00' for k in ('import','export','load','pv','charge','discharge')}}
        b=copy.deepcopy(a);b.update(timestamp='2026-11-05T12:03:00+00:00',collection_session='two');b['source_timestamps']={k:b['timestamp'] for k in b['energy']};b['energy']['load']='10.1';b['energy']['import']='10.1'
        return a,b
    def test_reboot_uuid_does_not_reset_physical_counters(self):
        a,b=self.pair();result=recover_cumulative(a,b,[],True,True)
        self.assertEqual(result['load'],'0.1');self.assertTrue(result['collector_session_changed'])
    def test_missing_intermediate_packet_still_has_measured_endpoint_total(self):
        a,b=self.pair();middle={**copy.deepcopy(a),'timestamp':'2026-11-05T12:01:00+00:00','energy':{},'key_errors':{'pv':['Unavailable']}}
        result=recover_cumulative(a,b,[middle],True,True);self.assertEqual(result['import'],'0.1');self.assertTrue(result['intermediate_missing_counters'])
    def test_invalid_endpoint_is_not_reconstructed(self):
        a,b=self.pair();b['energy'].pop('pv')
        with self.assertRaises(UnrecoveredGap):recover_cumulative(a,b,[],True,True)
    def test_counter_decrease_blocks_reconstruction(self):
        a,b=self.pair();middle=copy.deepcopy(a);middle['energy']['import']='1'
        with self.assertRaises(UnrecoveredGap):recover_cumulative(a,b,[middle],True,True)
    def test_physical_source_change_blocks_reconstruction(self):
        a,b=self.pair();b['sources']={'plant':'different-meter'}
        with self.assertRaises(UnrecoveredGap):recover_cumulative(a,b,[],True,True)
    def test_stale_endpoint_blocks_reconstruction(self):
        a,b=self.pair();b['source_timestamps']['pv']=a['timestamp']
        with self.assertRaises(UnrecoveredGap):recover_cumulative(a,b,[],True,True)
    def test_model_or_rate_change_needs_separate_treatment(self):
        a,b=self.pair()
        for stable_cfg,stable_tariff in ((False,True),(True,False)):
            with self.assertRaises(UnrecoveredGap):recover_cumulative(a,b,[],stable_cfg,stable_tariff)

if __name__=='__main__':unittest.main()
