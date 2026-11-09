"""Synthetic Ledger fixtures for the real hourly preparation entry point."""
import importlib,json,sqlite3,sys,types,unittest
from contextlib import contextmanager
from pathlib import Path
from decimal import Decimal as D
from copy import deepcopy
package=types.ModuleType('continuous_fixture');package.__path__=[str(Path(__file__).resolve().parents[1])];sys.modules[package.__name__]=package
r=importlib.import_module(package.__name__+'.runtime_continuity')
e=importlib.import_module(package.__name__+'.engine')
Timeline=importlib.import_module(package.__name__+'.settings').Timeline

class MemoryLedger:
    def __init__(self,samples):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE samples(timestamp TEXT,payload TEXT)')
        self.db.executemany('INSERT INTO samples VALUES (?,?)',[(s['timestamp'],json.dumps(s)) for s in samples])
    @contextmanager
    def connect(self):yield self.db

class RuntimePreparationTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'id':'TEST','usable_capacity_kwh':'17.52','reserve_percent':'25','charge_efficiency':'.956','discharge_efficiency':'.9409','standby_loss_kw':'0','pv_ac_factor':'1','load_boundary':'counter_balance','charge_limit_kw':'5.5','discharge_limit_kw':'5.5','inverter_ac_limit_kw':'5.5','battery_charge_dc_limit_kw':'8.8','battery_discharge_dc_limit_kw':'9.6','grid_import_limit_kw':'20','grid_limit_unreported':True,'export_limit_kw':'5.5','baseline_import_gbp_kwh':'.25','baseline_standing_gbp_day':'.40'}
        self.tariffs=[{'kind':k,'id':k,'effective_from':'2026-01-01T00:00:00Z','standing':'0','bands':[[0,1440,'.2']]} for k in ('import','export')]
        self.settings=Timeline([{'key':'reserve_percent','status':'observed','value':'25','timestamp':'2026-11-04T00:00:00Z'}])
        self.left={'start':'2026-11-04T12:00:00+00:00','end':'2026-11-04T12:01:00+00:00','initial_soc':'50','terminal_soc':'50',**{k:'.1' if k in ('load','import') else '0' for k in e.KEYS}}
        self.right={**self.left,'start':'2026-11-04T12:03:00+00:00','end':'2026-11-04T12:04:00+00:00'}
        self.comparison=[(self.left,self.cfg,{}),(self.right,self.cfg,{})]
    def sample(self,t,value,session='a'):
        return {'timestamp':t,'energy':{k:str(value) if k in ('load','import') else '0' for k in e.KEYS},'sources':{'plant':'same'},'source_timestamps':{k:t for k in e.KEYS},'soc':'50','collection_session':session}
    def samples(self):return [self.sample(self.left['end'],'.1'),self.sample(self.right['start'],'.3','restarted')]
    def ledger(self,samples):
        ledger=MemoryLedger(samples)
        self.addCleanup(ledger.db.close)
        return ledger
    def prepare(self,samples):return r.prepare(self.ledger(samples),self.comparison,self.tariffs,self.settings,[],[],e.timestamp(self.right['end']),r.monotonic()+10)
    def test_runtime_reads_retained_endpoints_and_replaces_gap(self):
        parts,evidence=self.prepare(self.samples())
        self.assertEqual(evidence['recovered_gap_count'],1)
        self.assertEqual(sum(D(p['part']['import']) for p in parts),D('.4'))
        self.assertTrue(next(p for p in parts if p['reconstructed'])['provenance']['collector_session_changed'])
    def test_missing_packet_does_not_destroy_endpoint_energy(self):
        samples=self.samples();middle=self.sample('2026-11-04T12:02:00+00:00',0);middle['energy']={}
        parts,evidence=self.prepare([samples[0],middle,samples[1]])
        self.assertEqual(evidence['recovered_gap_count'],1)
    def test_meter_change_is_unrecoverable_not_free_inventory(self):
        samples=self.samples();samples[-1]['sources']={'plant':'other'}
        _,evidence=self.prepare(samples)
        self.assertEqual(evidence['recovered_gap_count'],0);self.assertTrue(evidence['unrecovered'])
    def test_missing_exact_endpoint_is_not_invented_from_history(self):
        _,evidence=self.prepare(self.samples()[:1]);self.assertEqual(evidence['recovered_gap_count'],0)
    def test_runtime_fixed_scenarios_share_actual_and_coverage(self):
        out=r.corrected_view(self.ledger(self.samples()),self.comparison,self.tariffs,self.settings,[],[],e.timestamp(self.right['end']))
        main=out['periods']['month']
        for scenario in out['fixed_scenarios'].values():
            v=scenario['periods']['month'];self.assertEqual(v['values']['actual'],main['values']['actual']);self.assertEqual(v['coverage_fingerprint'],main['coverage_fingerprint'])
        self.assertEqual(main['soc_resets'],[])
    def test_deadline_rejects_work_before_partial_publication(self):
        with self.assertRaises(TimeoutError):r.prepare(self.ledger(self.samples()),self.comparison,self.tariffs,self.settings,[],[],e.timestamp(self.right['end']),0)
    def test_below_reserve_initial_state_cannot_materialise_energy(self):
        p={**self.left,'load':'1','pv':'0'};cfg={**self.cfg,'reserve_percent':'75'}
        out=e.simulate(p,D('2'),cfg,policy='cheap-window-self-consumption-v2')
        self.assertEqual(out['stored'],D('2'));self.assertEqual(out['charge'],0);self.assertEqual(out['discharge'],0)
    def test_reserve_25_to_75_zero_charge_does_not_raise_stock(self):
        p={**self.left,'load':'0','pv':'0'};initial=D('4.38')
        a=e.simulate(p,initial,{**self.cfg,'reserve_percent':'25'},policy='cheap-window-self-consumption-v2')
        b=e.simulate(p,a['stored'],{**self.cfg,'reserve_percent':'75'},policy='cheap-window-self-consumption-v2')
        self.assertEqual(b['stored'],initial);self.assertEqual(a['charge']+b['charge'],0)
    def test_100_percent_reserve_corner_has_no_hidden_topup(self):
        adapter=importlib.import_module(package.__name__+'.production_adapter')
        part={**self.left,'load':'0','pv':'0'}
        for physical in adapter.controls(self.cfg,True):
            out=e.simulate(part,D('2'),physical,policy='cheap-window-self-consumption-v2')
            self.assertEqual(out['stored'],D('2'));self.assertEqual(out['charge'],0)
    def test_uncertain_gap_then_reserve_transition_only_explicit_gap_changes_state(self):
        adapter=importlib.import_module(package.__name__+'.production_adapter')
        a={**self.left,'load':'0','pv':'0'}
        b={**self.right,'load':'0','pv':'0'}
        parts=[{'part':a,'physical':self.cfg},
               {'part':b,'physical':{**self.cfg,'reserve_percent':'100'},'uncertain':True}]
        trajectory=adapter.replay_once(parts,self.tariffs,'10',e)
        row=trajectory['rows'][-1]
        self.assertEqual(row['stored_before'],row['stored_after'])
        gap=trajectory['gaps'][0]
        self.assertEqual(tuple(map(D,gap['after'])),row['stored_before'])
        self.assertLess(row['stored_after'][1],D(self.cfg['usable_capacity_kwh']))

if __name__=='__main__':unittest.main()
