"""Executable stdlib tests; no Home Assistant installation required."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("solar_finance")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("solar_finance", package)
e = importlib.import_module("solar_finance.engine")
l = importlib.import_module("solar_finance.ledger")
c = importlib.import_module("solar_finance.const")


def inputs():
    # TEST FIXTURE ONLY. None of these calibration/limit numbers are production defaults.
    return {"boundary_verified": True, "boundary_evidence": "TEST calibrated wiring", "usable_capacity_kwh": "17.52", "reserve_percent": "50", "charge_limit_kw": "4", "discharge_limit_kw": "4", "grid_import_limit_kw": "8", "export_limit_kw": "3", "charge_efficiency": ".96", "discharge_efficiency": ".94", "standby_loss_kw": ".02", "pv_ac_factor": ".97", "hardware_evidence": "TEST limits", "loss_evidence": "TEST ONLY calibration provenance", "reserve_evidence": "TEST dated fixed reserve", "effective_from": c.POLICY_REGISTERED_AT, "baseline_import_gbp_kwh": ".25", "baseline_standing_gbp_day": ".40", "baseline_evidence": "TEST Flexible"}


def tariffs():
    return [{**c.TEMPLATES[name], "id": name, "effective_from": "2026-08-25T14:11:00Z", "effective_to": None} for name in ("flux_import", "prime_export")]


def interval(start="2026-10-28T16:00:00Z", seconds=60, load=".04", pv=".01"):
    return {"start": start, "end": e.iso(e.timestamp(start) + timedelta(seconds=seconds)), "load": load, "pv": pv, "import": ".02", "export": ".001", "charge": "0", "discharge": "0", "initial_soc": "75", "terminal_soc": "75"}


def sample(at, value, errors=None):
    return {"timestamp": at, "energy": {key: str(value) for key in e.KEYS}, "soc": "70", "mode": "Maximum Self Consumption", "errors": errors or [], "sources": dict(c.ENTITIES), "source_timestamps": {key: at for key in c.ENTITIES}}


class Frozen(datetime):
    instant = e.timestamp("2026-10-28T16:02:05Z")

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)


class EngineTests(unittest.TestCase):
    def test_ceiling_negative_grouped(self):
        for raw, expected in [("1234.00001", "£1,235"), ("-1234.99", "−£1,234"), ("-1.2", "−£1"), ("-0.2", "£0"), ("0", "£0")]:
            self.assertEqual(e.pounds(raw), expected)

    def test_flexible_baseline_is_configurable(self):
        i, cfg = interval(), inputs()
        old = e.costs(i, tariffs(), D("12"), cfg)
        cfg["baseline_import_gbp_kwh"] = ".50"
        cfg["baseline_standing_gbp_day"] = "1"
        new = e.costs(i, tariffs(), D("12"), cfg)
        self.assertEqual(new["baseline"], D(i["load"]) * D(".50") + D(60) / D(86400))
        self.assertNotEqual(old["baseline"], new["baseline"])

    def test_import_and_export_tariff_changes(self):
        versions = tariffs()
        i = interval()
        old = e.costs(i, versions, D("12"), inputs())
        self.assertEqual(old["actual"], D(".02") * D(".40") + D(60)/D(86400)*D(".45") - D(".001") * D(".16"))
        versions.append({**c.TEMPLATES["flux_export_planned"], "id": "replacement", "effective_from": "2026-10-28T16:01:00Z", "effective_to": None})
        self.assertEqual(e.interval_tariff(versions,"export",e.timestamp(i["start"]),e.timestamp(i["end"]))[0], D(".16"))
        newer = interval("2026-10-28T16:01:00Z")
        self.assertEqual(e.interval_tariff(versions,"export",e.timestamp(newer["start"]),e.timestamp(newer["end"]))[0], D(".20"))
        with self.assertRaises(e.Pending):
            e.interval_tariff(versions, "export", e.timestamp("2026-10-28T16:00:30Z"), e.timestamp("2026-10-28T16:01:30Z"))

    def test_no_retrospective_current_rate(self):
        versions = tariffs()
        for v in versions:
            v["effective_from"] = "2026-10-29T00:00:00Z"
        with self.assertRaises(e.Pending):
            e.costs(interval(), versions, D("12"), inputs())
        with self.assertRaises(e.Pending):
            e.costs(interval(), [], D("12"), inputs())

    def test_missing_export_is_unknown_not_zero(self):
        for key in ("import","export","load","pv"):
            i=interval();del i[key]
            with self.assertRaises(e.Pending):
                e.costs(i,tariffs(),D("12"),inputs())

    def test_expired_newer_tariff_does_not_revert_to_superseded_rate(self):
        versions=tariffs()
        versions.append({**versions[0],"id":"newer","effective_from":"2026-10-28T15:00:00Z","effective_to":"2026-10-28T15:30:00Z"})
        with self.assertRaises(e.Pending):
            e.costs(interval(),versions,D("12"),inputs())

    def test_tariff_end_and_uncovered_gap(self):
        versions = tariffs()
        versions[0]["effective_to"] = "2026-10-28T16:00:30Z"
        with self.assertRaises(e.Pending):
            e.costs(interval(), versions, D("12"), inputs())

    def test_local_dst_windows(self):
        import_tariff = tariffs()[0]
        # London BST: cheap local 02:00 starts at 01:00 UTC.
        self.assertEqual(e.rate_at(import_tariff, e.timestamp("2026-08-26T01:30:00Z")), D(".10"))
        # After fall-back, local 02:00 is 02:00 UTC.
        self.assertEqual(e.rate_at(import_tariff, e.timestamp("2026-10-26T01:30:00Z")), D(".30"))
        self.assertEqual(e.rate_at(import_tariff, e.timestamp("2026-10-26T02:30:00Z")), D(".10"))

    def test_standing_dst_and_partial_commission(self):
        for a,b in [("2026-03-29T00:00:00Z", "2026-03-29T23:00:00Z"), ("2026-10-24T23:00:00Z", "2026-10-26T00:00:00Z")]:
            self.assertEqual(e.civil_fraction(e.timestamp(a), e.timestamp(b)), D(1))
        self.assertEqual(e.civil_fraction(e.timestamp(c.COMMISSIONED_AT),e.timestamp(c.COMMISSIONED_AT) + timedelta(minutes=529)), D(529)/D(1440))

    def test_grid_charge_headroom_and_capacity(self):
        cfg = inputs()
        i = interval("2026-10-28T02:00:00Z", seconds=3600, load="7", pv="0")
        result = e.simulate(i, D("9"), cfg)
        self.assertEqual(result["charge"], D(1))
        self.assertEqual(result["import"], D(8))
        self.assertLessEqual(result["stored"], D("17.52"))
        near_full = e.simulate(i, D("17.51"), cfg)
        self.assertLessEqual(near_full["stored"], D("17.52"))

    def test_pv_and_grid_share_charge_budget(self):
        i = interval("2026-10-28T02:00:00Z", seconds=3600, load="1", pv="4")
        result = e.simulate(i, D("9"), inputs())
        self.assertEqual(result["charge"], D(4))
        self.assertLessEqual(result["import"], D(8))

    def test_preserve_and_peak_house_only_no_battery_export(self):
        cfg = inputs()
        for at in ("2026-10-28T15:00:00Z", "2026-10-28T19:00:00Z"):
            result = e.simulate(interval(at), D("12"), cfg)
            self.assertEqual(result["discharge"], 0)
        peak = e.simulate(interval(load=".1",pv="0"), D("12"), cfg)
        self.assertLessEqual(peak["discharge"], D(4)/D(60)+D("1e-25"))
        self.assertEqual(peak["export"], 0)

    def test_reserve_and_losses_no_free_energy(self):
        cfg = inputs()
        result = e.simulate(interval(load=".05",pv="0"), D("8.77"), cfg)
        self.assertGreaterEqual(result["stored"], D("8.76"))
        below = e.simulate(interval(load=".05",pv="0"), D("7"), cfg)
        self.assertEqual(below["discharge"], 0)
        self.assertLess(below["stored"], 7)

    def test_export_curtailment_and_energy_conservation(self):
        cfg=inputs()
        for stored in (D(0),D("8.76"),D("17.52")):
            for at in ("2026-10-28T02:00:00Z", "2026-10-28T12:00:00Z", "2026-10-28T16:00:00Z"):
                for load,pv in [("0","0"),("2","0"),("0","12"),("4","6")]:
                    i=interval(at,3600,load,pv)
                    r=e.simulate(i,stored,cfg)
                    self.assertAlmostEqual(D(pv)*D(cfg["pv_ac_factor"])+r["import"],D(load)+r["export"]+r["curtailment"]+(r["stored"]-stored)+r["loss"],places=20)
                    self.assertLessEqual(r["export"],D(3))
                    self.assertTrue(0 <= r["stored"] <= D("17.52"))

    def test_impossible_connection_and_schedule_transition_block(self):
        with self.assertRaises(e.Pending):
            e.simulate(interval("2026-10-28T12:00:00Z",3600,"20","0"),D(12),inputs())
        with self.assertRaises(e.Pending):
            e.simulate(interval("2026-10-28T15:59:30Z"),D(12),inputs())

    def test_negative_saving_allowed(self):
        i=interval(load=".01",pv="0")
        i["import"]="1"
        self.assertLess(e.costs(i,tariffs(),D(12),inputs())["saving"],0)

    def test_missing_calibration_and_boundary_remain_pending(self):
        self.assertTrue(e.input_blockers({}))
        cfg=inputs();cfg["boundary_verified"]=False
        self.assertTrue(e.input_blockers(cfg))
        for key in ("loss_evidence","grid_import_limit_kw","charge_efficiency"):
            cfg=inputs();del cfg[key]
            self.assertTrue(any(key in b for b in e.input_blockers(cfg)))
        for value in ("NaN","Infinity","-1","0","1.01"):
            cfg=inputs();cfg["charge_efficiency"]=value
            with self.assertRaises((ValueError,ArithmeticError)):
                e.validate_inputs(cfg)
        with self.assertRaises(ValueError):
            e.timestamp("2026-10-27T12:00:00")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/"ledger.sqlite3"
        self.db=l.Ledger(self.path);self.db.initialize()

    def activate(self):
        self.db.configure(inputs(),[])

    def samples(self):
        for at,v in [("2026-10-28T16:00:00Z",100),("2026-10-28T16:01:00Z",100.01),("2026-10-28T16:02:00Z",100.02)]:
            self.db.collect(sample(at,v))

    def report(self):
        with patch.object(l,"datetime",Frozen):
            return self.db.report()

    def test_templates_inactive_and_forward_registration_persistent(self):
        self.assertEqual(len(self.db.versions("tariff")),3)
        self.assertTrue(all(v["assumed"] for v in self.db.versions("tariff")))
        self.assertEqual(len(self.db.versions("template")),4)
        self.assertFalse(any(v["template"] == "flux_export_planned" for v in self.db.tariff_versions()))
        policy=self.db.versions("policy")[0]
        self.db.initialize()
        self.assertEqual(self.db.versions("policy")[0],policy)
        self.assertEqual(policy["registered_at"],c.POLICY_REGISTERED_AT)
        self.samples()
        report=self.report()
        for period in report["periods"].values():
            self.assertIsNone(period["values"])
            self.assertEqual(period["status"],"Collecting data")

    def test_matched_results_exact_common_period_restart_and_backup(self):
        self.activate();self.samples()
        report=self.report()
        m=report["periods"]["month"]
        self.assertIsNotNone(m["values"])
        self.assertEqual(set(m["values"]),{"baseline","actual","saving","schedule"})
        self.assertEqual(m["coverage_start"],"2026-10-28T16:00:00+00:00")
        self.assertEqual(m["coverage_end"],"2026-10-28T16:02:00+00:00")
        self.assertFalse(m["full_period"])
        self.assertEqual(D(m["values"]["saving"]),D(m["values"]["baseline"])-D(m["values"]["actual"]))
        other=l.Ledger(self.path);other.initialize()
        with patch.object(l,"datetime",Frozen):
            self.assertEqual(other.report()["periods"],report["periods"])
        copied=l.Ledger(self.db.backup())
        with patch.object(l,"datetime",Frozen):
            self.assertEqual(copied.report()["periods"],report["periods"])
        with self.db.connect() as db:
            self.assertGreater(db.execute("SELECT count(*) FROM reports").fetchone()[0],0)

    def test_tariffs_and_baseline_versions_are_durable(self):
        self.activate()
        replacement={"template":"flux_export_planned","effective_from":"2026-11-01T00:00:00Z","evidence":"TEST future confirmation"}
        next_inputs=inputs();next_inputs.update(effective_from="2026-11-01T00:00:00Z",baseline_import_gbp_kwh=".30")
        self.db.configure(next_inputs,[replacement])
        self.assertEqual(len(self.db.versions("tariff")),4)
        self.assertEqual(len(self.db.versions("inputs")),2)
        earlier=self.db._inputs_at(self.db.versions("inputs"),e.timestamp("2026-10-28T12:00:00Z"))
        self.assertEqual(earlier["baseline_import_gbp_kwh"],".25")
        later=self.db._inputs_at(self.db.versions("inputs"),e.timestamp("2026-11-02T12:00:00Z"))
        self.assertEqual(later["baseline_import_gbp_kwh"],".30")
        with self.assertRaises(sqlite3.IntegrityError),self.db.connect() as db:
            db.execute("UPDATE versions SET payload='{}'")

    def test_conflict_rolls_back_entire_configuration(self):
        self.activate()
        bad=[{"template":"flux_export_planned","effective_from":c.ASSUMED_FLUX_START,"evidence":"conflict"}]
        with self.assertRaises(ValueError):
            self.db.configure(inputs(),bad)
        self.assertEqual(len(self.db.versions("tariff")),3)

    def test_resets_outages_invalid_samples_and_source_changes(self):
        self.activate();self.samples()
        self.db.collect(sample("2026-10-28T16:03:00Z",0))
        with self.db.connect() as db:
            payload=json.loads(db.execute("SELECT payload FROM intervals ORDER BY id DESC LIMIT 1").fetchone()[0])
            self.assertIn("counter reset",str(payload))
        self.db.collect(sample("2026-10-28T16:20:00Z",1))
        self.db.collect(sample("2026-10-28T16:21:00Z",2,["stale source"]))
        self.db.collect(sample("2026-10-28T16:22:00Z",3))
        changed=sample("2026-10-28T16:23:00Z",4);changed["sources"]["load"]="sensor.other_meter"
        self.db.collect(changed)
        report=self.report()
        # Later bad intervals never erase earlier auditable covered evidence.
        self.assertEqual(report["periods"]["month"]["coverage_intervals"], 2)
        self.assertEqual(report["periods"]["month"]["coverage_end"], "2026-10-28T16:02:00+00:00")

    def test_short_restart_is_a_gap_and_repeated_samples_idempotent(self):
        self.activate()
        a=sample("2026-10-28T16:00:00Z",100);a["collection_session"]="old"
        b=sample("2026-10-28T16:01:00Z",100.01);b["collection_session"]="new"
        self.db.collect(a);self.db.collect(b);self.db.collect(b)
        with self.db.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM samples").fetchone()[0],2)
            row=db.execute("SELECT quality,payload FROM intervals").fetchone()
            self.assertEqual(row["quality"],"gap")
            self.assertIn("restarted",row["payload"])

    def test_no_pre_registration_or_fabricated_lifetime(self):
        self.activate()
        self.db.collect(sample("2026-09-01T16:00:00Z",100))
        self.db.collect(sample("2026-09-01T16:01:00Z",101))
        self.assertIsNone(self.report()["periods"]["lifetime"]["values"])

    def test_stale_collector_retains_explicit_historical_coverage(self):
        self.activate();self.samples()
        with patch.object(l,"datetime",Frozen):
            before=self.db.report()
        self.assertIsNotNone(before["periods"]["month"]["values"])
        old=Frozen.instant
        try:
            Frozen.instant+=timedelta(hours=1)
            report=self.report()
            self.assertFalse(report["collector_current"])
            self.assertEqual(report["periods"]["month"]["values"], before["periods"]["month"]["values"])
        finally:
            Frozen.instant=old

    def test_current_collector_with_partial_inputs_is_not_labelled_stale(self):
        partial={"effective_from":"2026-08-25T14:11:00Z", "baseline_import_gbp_kwh":".25", "baseline_standing_gbp_day":".40", "baseline_evidence":"User confirmed Flexible", "usable_capacity_kwh":"17.52", "reserve_percent":"50", "boundary_verified":False}
        self.db.configure(partial,[])
        self.samples()
        report=self.report()
        self.assertTrue(report["collector_current"])
        self.assertEqual(report["collector_sample_errors"],[])
        for period in report["periods"].values():
            self.assertIsNotNone(period["values"]["baseline"])
            self.assertIsNotNone(period["values"]["saving"])
            self.assertIsNone(period["values"]["schedule"])
            self.assertNotIn("Collector not current",period["blockers"])
            self.assertTrue(any("Reserve is unknown" in blocker for blocker in period["blockers"]))
            self.assertFalse(any("baseline_" in b or "usable_capacity" in b for b in period["blockers"]))
        old=Frozen.instant
        try:
            Frozen.instant+=timedelta(minutes=4)
            stale=self.report()
            self.assertFalse(stale["collector_current"])
            self.assertEqual(stale["periods"]["month"]["values"],report["periods"]["month"]["values"])
        finally:
            Frozen.instant=old

    def test_original_observation_support_retained_and_estimate_labelled(self):
        self.activate()
        a=sample("2026-10-28T16:00:05Z",100)
        a["source_timestamps"]={k:"2026-10-28T15:59:55Z" for k in c.ENTITIES}
        self.db.collect(a);self.db.collect(sample("2026-10-28T16:01:05Z",100.01))
        report=self.report()
        self.assertIsNotNone(report["periods"]["month"]["values"])
        self.assertIn("split pro-rata",report["methodology"])

    def test_september_transition_and_prior_export_unknown(self):
        i=interval(e.iso(e.timestamp(c.ASSUMED_FLUX_START)-timedelta(seconds=30)),60,".04","0");i["export"]="0"
        value=e.costs(i,self.db.tariff_versions(),D("7"),inputs())
        expected=D(".01")*D(".25")+D(".01")*D(".30")+D(30)/D(86400)*(D(".40")+D(".45"))
        self.assertAlmostEqual(value["actual"],expected,places=25)
        self.assertTrue(value["assumed_tariff"])
        i["export"]=".01"
        with self.assertRaises(e.Pending):
            e.costs(i,self.db.tariff_versions(),D("7"),inputs())
        before=interval(e.iso(e.timestamp(c.ASSUMED_FLUX_START)-timedelta(days=1)),60,".04","0");before["export"]="0"
        self.assertIsNotNone(e.costs(before,self.db.tariff_versions(),D("7"),inputs()))

    def test_future_export_only_transition_split_and_import_unchanged(self):
        self.activate()
        original=e.tariff_at(self.db.tariff_versions(),"import",e.timestamp("2026-10-28T16:02:00Z"))["id"]
        self.db.configure(inputs(),[{"template":"flux_export_planned","effective_from":"2026-10-28T16:01:00Z","evidence":"TEST export-only confirmation"}])
        current=self.db.tariff_versions()
        self.assertEqual(original,e.tariff_at(current,"import",e.timestamp("2026-10-28T16:02:00Z"))["id"])
        i=interval("2026-10-28T16:00:30Z");i["export"]=".002"
        value=e.costs(i,current,D(12),inputs())
        expected=D(".02")*D(".40")+D(60)/D(86400)*D(".45")-D(".001")*(D(".16")+D(".20"))
        self.assertAlmostEqual(value["actual"],expected,places=25)

    def test_retroactive_correction_recalculates_idempotently(self):
        self.activate();self.samples()
        before=self.report()["periods"]["month"]["values"]
        target=next(v for v in self.db.tariff_versions() if v["template"]=="prime_export")
        correction={k:v for k,v in target.items() if k not in ("id","ledger_registered_at")}
        correction.update(replaces=target["id"],bands=[[0,1440,".3"]],assumed=False,evidence="TEST corrected supplier rate",revision_registered_at="2026-10-29T00:00:00Z")
        self.db.configure(inputs(),[],[correction])
        after=self.report()["periods"]["month"]["values"]
        self.assertNotEqual(before["actual"],after["actual"])
        self.assertEqual(before["baseline"],after["baseline"])
        for _ in range(3):
            self.db.configure(inputs(),[],[correction])
            self.assertEqual(self.report()["periods"]["month"]["values"],after)
        self.assertEqual(len(self.db.versions("tariff")),4)
        with self.db.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM intervals").fetchone()[0],2)


class CurrencyAndClockTests(unittest.TestCase):
    def test_explicit_pence_units(self):
        self.assertEqual(e.normalize_rate("10.00","pence_per_kWh"),D(".10"))
        self.assertEqual(e.normalize_rate("45.00","pence_per_day"),D(".45"))
        self.assertEqual(e.normalize_rate(".10","GBP_per_kWh"),D(".10"))
        with self.assertRaises(ValueError):e.normalize_rate("10.00","unknown")
        tariff={"kind":"import","effective_from":c.ASSUMED_FLUX_START,"evidence":"TEST explicit pence", "bands":[[0,1440,"10.00"]],"standing":"45.00","rate_unit":"pence_per_kWh","standing_unit":"pence_per_day"}
        normalized=l.normalize_tariff(tariff)
        self.assertEqual(normalized["bands"][0][2],"0.10")
        self.assertEqual(normalized["standing"],"0.45")
        self.assertEqual(normalized["rate_unit"],"GBP_per_kWh")

    def test_simple_local_date_time_dst_rejection(self):
        self.assertEqual(e.local_timestamp("2026-09-27","00:00:00"),"2026-09-26T23:00:00+00:00")
        with self.assertRaises(ValueError):e.local_timestamp("2026-10-25","01:30:00")
        with self.assertRaises(ValueError):e.local_timestamp("2026-03-29","01:30:00")

    def test_dst_crossing_split_conserves_standing_and_prices(self):
        i=interval("2026-10-25T01:59:30Z",60,".1","0");i["export"]="0"
        # This UTC boundary is London 01:59 GMT → 02:00 GMT, cheap window start.
        result=e.costs(i,tariffs(),D(10),inputs())
        expected=D(".01")*(D(".30")+D(".10"))+D(60)/D(90000)*D(".45")
        self.assertAlmostEqual(result["actual"],expected,places=25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
