import json
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from archive import Archive
from acquire import acquire
from matched_replay import MatchedReplay
from fourth_card import reduce_rows
from forecast_inputs import pv_minutes


CFG={'usable_capacity_kwh':'17.52','reserve_percent':'25','charge_efficiency':'0.93','discharge_efficiency':'0.93','standby_loss_kw':'0','pv_ac_factor':'1','grid_import_limit_kw':'20','charge_limit_kw':'5.5','discharge_limit_kw':'5.5','inverter_ac_limit_kw':'5.5','export_limit_kw':'5.5','battery_charge_dc_limit_kw':'5.5','battery_discharge_dc_limit_kw':'5.5','baseline_import_gbp_kwh':'0.3','baseline_standing_gbp_day':'0'}


def tariffs():
    return [{'id':'test-import','kind':'import','effective_from':'2026-01-01T00:00:00Z','effective_to':None,'standing':'0','bands':[[0,1440,'0.3']]},
            {'id':'test-export','kind':'export','effective_from':'2026-01-01T00:00:00Z','effective_to':None,'standing':'0','bands':[[0,1440,'0']]}]


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Archive(self.tmp.name+'/archive.sqlite3');self.r=MatchedReplay(self.a)
    def tearDown(self):self.a.close();self.tmp.cleanup()
    def plan(self,received='2026-11-08T01:59:00Z'):
        return self.a.put('predbat-plan',{'plan_valid':True,'midnight_utc':'2026-11-08T00:00:00Z','charge_windows':[{'start':120,'end':300}],'charge_limits_kwh':['17.52'],'export_windows':[],'export_limits':[],'valid_until':'2026-11-09T00:00:00Z'},received)
    def part(self,start='02:00',end='03:00',uncertain=False):
        return {'part':{'start':'2026-11-08T'+start+':00Z','end':'2026-11-08T'+end+':00Z','load':'0.72','pv':'0','import':'0.72','export':'0'},'physical':CFG,'uncertain':uncertain}
    def test_cash_shared_window_and_persistent_inventory(self):
        self.r.seed('2026-11-08T02:00:00Z','2','2',{'synthetic':True})
        self.r.advance([self.part()],tariffs(),[self.plan()]);s=self.r.state()
        self.assertEqual(s['predbat'],['7.1150','7.1150'])
        view=self.r.view('2026-11-01T00:00:00Z','2026-12-01T00:00:00Z')
        from decimal import Decimal
        self.assertEqual(Decimal(view['covered_hours']),1);self.assertEqual(Decimal(view['aligned_comparisons']['baseline']),Decimal('0.216'))
        with self.assertRaises(Exception):self.r.seed('2026-11-08T03:00:00Z','17','17',{})
        self.a.close();self.a=Archive(self.tmp.name+'/archive.sqlite3');self.r=MatchedReplay(self.a)
        self.assertEqual(self.r.state()['predbat'],s['predbat'])
    def test_hindsight_plan_omits_all_cash_and_widens_stock(self):
        self.r.seed('2026-11-08T02:00:00Z','2','2',{'synthetic':True})
        self.r.advance([self.part()],tariffs(),[self.plan('2026-11-08T03:00:00Z')])
        self.assertEqual(self.r.view('2026-11-01T00:00:00Z','2026-12-01T00:00:00Z')['status'],'Pending')
        self.assertNotEqual(self.r.state()['predbat'],['2','2'])
    def test_duplicate_replay_and_boundary_abort_atomically(self):
        self.r.seed('2026-11-08T01:00:00Z','2','2',{'synthetic':True});old=self.r.state()
        with self.assertRaises(ValueError):self.r.advance([self.part('01:00','03:00')],tariffs(),[self.plan('2026-11-08T00:00:00Z')])
        self.assertEqual(self.r.state(),old)
    def test_no_fake_zero_empty_fourth_card(self):self.assertIsNone(reduce_rows([])['display'])
    def test_acquisition_failure_consumes_attempt_without_secret_error(self):
        class Opener:
            def open(self,*a,**k):raise OSError('synthetic network failure')
        with self.assertRaisesRegex(RuntimeError,'attempt counted'):
            acquire(self.a,'synthetic-key',['abcd-1234'],datetime(2026,11,8,tzinfo=timezone.utc),opener=Opener())
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM attempts').fetchone()[0],1)
    def test_received_forecasts_power_to_energy_and_duplicate_site(self):
        start=datetime(2026,11,8,tzinfo=timezone.utc)
        rows=[{'period_end':(start+timedelta(minutes=30*(i+1))).isoformat(),'period':'PT30M','pv_estimate':3.0} for i in range(48)]
        key=self.a.put('solcast-forecast',{'site_alias':'plane-1','forecasts':rows},start.isoformat())
        self.assertAlmostEqual(sum(pv_minutes(self.a,[key],start.isoformat(),1440)),72)
        with self.assertRaises(ValueError):pv_minutes(self.a,[key,key],start.isoformat(),1440)

    def test_plan_cannot_act_before_valid_from(self):
        key=self.a.put('predbat-plan',{'plan_valid':True,'midnight_utc':'2026-11-08T00:00:00Z','charge_windows':[],'charge_limits_kwh':[],'export_windows':[],'export_limits':[],'valid_from':'2026-11-08T03:00:00Z','valid_until':'2026-11-09T00:00:00Z'},'2026-11-08T01:00:00Z')
        self.r.seed('2026-11-08T02:00:00Z','2','2',{'synthetic':True})
        self.r.advance([self.part()],tariffs(),[key])
        self.assertEqual(self.r.view('2026-11-08T00:00:00Z','2026-11-09T00:00:00Z')['status'],'Pending')

    def test_unknown_gap_explicit_then_no_second_soc_seed(self):
        self.r.seed('2026-11-08T02:00:00Z','2','2',{'synthetic':True})
        self.r.align_unknown_gap('2026-11-08T02:05:00Z',CFG)
        before=self.r.state()
        self.assertIn('last_unknown_gap',before)
        self.r.align_unknown_gap('2026-11-08T02:05:00Z',CFG)
        self.assertEqual(self.r.state(),before)

    def test_broker_rejects_credential_or_control_payload_fields(self):
        from service import Controller
        c=Controller(self.tmp.name,{'enabled':False},'/not-used','/not-used')
        try:
            with self.assertRaises(ValueError):c.ingest({'schema':1,'api_key':'synthetic-rejected'},'2026-11-08T00:00:00Z')
            c.tick(datetime(2026,11,8,tzinfo=timezone.utc))
            self.assertEqual(c.archive.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
        finally:c.archive.close()

if __name__=='__main__':unittest.main()
