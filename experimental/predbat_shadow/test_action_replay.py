from decimal import Decimal as D
import unittest
from action_replay import simulate, engine

PHYSICS={'usable_capacity_kwh':'17.52','reserve_percent':'25','charge_efficiency':'0.93','discharge_efficiency':'0.93','standby_loss_kw':'0','pv_ac_factor':'1','grid_import_limit_kw':'20','charge_limit_kw':'5.5','discharge_limit_kw':'5.5','inverter_ac_limit_kw':'5.5','export_limit_kw':'5.5','plan_target_kwh':'17.52'}


class Tests(unittest.TestCase):
    def test_identical_charge_physics_when_target_full(self):
        p={'start':'2026-11-08T02:00:00Z','end':'2026-11-08T03:00:00Z','load':'0.72','pv':'0'}
        self.assertEqual(simulate(p,D('2'),PHYSICS,policy='charge'),engine.simulate(p,D('2'),PHYSICS,policy='cheap-window-self-consumption-v2'))

    def test_target_and_reserve_do_not_materialise_inventory(self):
        p={'start':'2026-11-08T00:00:00Z','end':'2026-11-08T01:00:00Z','load':'0','pv':'0'}
        for reserve in ('25','75','100'):
            self.assertEqual(simulate(p,D('2'),{**PHYSICS,'reserve_percent':reserve},policy='discharge')['stored'],D('2'))
        out=simulate(p,D('2'),{**PHYSICS,'plan_target_kwh':'3'},policy='charge')
        self.assertEqual(out['stored'],D('3'))
        self.assertGreater(out['charge'],0)

    def test_solar_above_target_never_negative_grid_charge(self):
        p={'start':'2026-11-08T02:00:00Z','end':'2026-11-08T03:00:00Z','load':'0','pv':'2'}
        out=simulate(p,D('5'),{**PHYSICS,'plan_target_kwh':'3'},policy='charge')
        self.assertEqual(out['import'],0)
        self.assertGreater(out['stored'],D('5'))

if __name__=='__main__':unittest.main()
