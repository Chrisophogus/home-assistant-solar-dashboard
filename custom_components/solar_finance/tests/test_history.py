"""Offline fixtures only: no synthetic readings are written to production."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import importlib
import json
from pathlib import Path
import tempfile
import unittest
import sys
import types
from unittest.mock import patch

from test_engine import c, e, inputs, l, sample, tariffs

h = importlib.import_module("solar_finance.history")
r = importlib.import_module("solar_finance.reporting")
START = e.timestamp("2026-10-26T12:00:00Z")


def history_fixture(minutes=6):
    rows = []
    def add(entity, seconds, state, unit):
        rows.append({"entity_id": entity, "timestamp": e.iso(START + timedelta(seconds=seconds)), "state": str(state), "unit": unit})
    for seconds in range(0, minutes * 60 + 1, 30):
        add(h.HEARTBEAT, seconds, "1.5", "kW")
    for key in e.KEYS:
        for minute in range(minutes + 1):
            # Export is genuinely unchanged, not filled with a fabricated zero.
            if key == "export" and minute:
                continue
            add(c.ENTITIES[key], minute * 60, Decimal("10") + (Decimal(minute) / 100 if key != "export" else 0), "kWh")
    add(c.ENTITIES["soc"], 0, "50.1", "%")
    add(c.ENTITIES["mode"], 0, "Maximum Self Consumption", None)
    return rows


class ReconstructionTests(unittest.TestCase):
    def derive(self, rows):
        return h.reconstruct(rows, c.ENTITIES, START, START + timedelta(minutes=6))

    def test_unit_conversion_unchanged_export_and_observation_provenance(self):
        rows = history_fixture()
        for row in rows:
            if row["entity_id"] == c.ENTITIES["load"]:
                row["unit"] = "MWh"
                row["state"] = str(Decimal(row["state"]) / 1000)
        intervals = self.derive(rows)
        self.assertEqual(len(intervals), 6)
        self.assertEqual(sum((Decimal(i["load"]) for i in intervals)), Decimal(".06"))
        self.assertTrue(all(i["export"] == "0" and not i["errors"] for i in intervals))
        self.assertTrue(intervals[0]["observation_support"]["export"]["held_unchanged"])
        self.assertIn("no independent", intervals[0]["provenance"])

    def test_missing_export_not_zero_baseline_still_valid(self):
        rows = [row for row in history_fixture() if row["entity_id"] != c.ENTITIES["export"]]
        item = self.derive(rows)[0]
        self.assertNotIn("export", item)
        self.assertTrue(item["key_errors"]["export"])
        self.assertEqual(item["key_errors"]["load"], [])

    def test_heartbeat_gap_does_not_launder_later_counter_delta(self):
        rows = [row for row in history_fixture() if row["entity_id"] != h.HEARTBEAT or not START < e.timestamp(row["timestamp"]) < START + timedelta(minutes=4)]
        derived = self.derive(rows)
        self.assertTrue(derived[1]["key_errors"]["load"])
        # Export held before the gap remains ineligible AFTER plant recovers.
        self.assertTrue(derived[-1]["key_errors"]["export"])
        self.assertEqual(derived[-1]["key_errors"]["load"], [])

    def test_reset_inside_bin_is_not_bridged_even_if_counter_recovers(self):
        rows = history_fixture()
        rows.append({"entity_id": c.ENTITIES["load"], "timestamp": e.iso(START + timedelta(seconds=20)), "state": "0", "unit": "kWh"})
        item = self.derive(rows)[0]
        self.assertNotIn("load", item)
        self.assertIn("reset", str(item["key_errors"]["load"]))

    def test_unavailable_heartbeat_blocks_and_requires_new_counter_support(self):
        rows = history_fixture()
        next(row for row in rows if row["entity_id"] == h.HEARTBEAT and e.timestamp(row["timestamp"]) == START + timedelta(seconds=90))["state"] = "unavailable"
        derived = self.derive(rows)
        self.assertTrue(derived[1]["key_errors"]["load"])
        self.assertTrue(derived[-1]["key_errors"]["export"])

    def test_identical_replay_idempotent_conflicting_state_rejected(self):
        rows = history_fixture()
        self.assertEqual(self.derive(rows), self.derive(rows + deepcopy(rows)))
        conflict = {**rows[-1], "state": "conflict"}
        with self.assertRaises(ValueError):
            self.derive(rows + [conflict])

    def test_restart_fence_requires_fresh_counters(self):
        rows = history_fixture()
        rows.append({"entity_id": c.ENTITIES["export"], "timestamp": e.iso(START + timedelta(seconds=120)), "state": "10", "unit": "kWh"})
        derived = self.derive(rows)
        self.assertTrue(derived[1]["errors"])
        self.assertEqual(derived[3]["errors"], [])


class IndependentReadinessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = l.Ledger(Path(self.folder.name) / "test.sqlite3")
        self.db.initialize()
        self.cfg = {"baseline_import_gbp_kwh": ".25", "baseline_standing_gbp_day": ".40", "baseline_evidence": "TEST baseline", "effective_from": c.COMMISSIONED_AT}
        self.db.configure(self.cfg, [])

    def report(self):
        return r.build_report(self.db, START + timedelta(minutes=10))["periods"]["month"]

    def import_rows(self, rows):
        return self.db.import_history(rows, c.ENTITIES, START, START + timedelta(minutes=6))

    def test_pre_policy_actual_accounting_without_model_inputs(self):
        self.import_rows(history_fixture())
        period = self.report()
        self.assertEqual(period["coverage_intervals"], 6)
        self.assertIsNotNone(period["values"]["baseline"])
        self.assertIsNotNone(period["values"]["saving"])
        self.assertIsNone(period["values"]["schedule"])
        self.assertEqual(period["column_blockers"]["baseline"], [])
        self.assertEqual(period["column_blockers"]["saving"], [])
        self.assertTrue(period["column_blockers"]["schedule"])
        self.assertFalse(period["full_period"])
        self.assertEqual(Decimal(period["values"]["saving"]), Decimal(period["values"]["baseline"]) - Decimal(period["values"]["actual"]))

    def test_baseline_without_export_and_soc_or_battery_measurements(self):
        rows = [row for row in history_fixture() if row["entity_id"] in (h.HEARTBEAT, c.ENTITIES["load"])]
        self.import_rows(rows)
        period = self.report()
        self.assertIsNotNone(period["values"]["baseline"])
        self.assertIsNone(period["values"]["saving"])
        self.assertIsNone(period["values"]["schedule"])

    def test_idempotent_import_and_overlap_do_not_double_count(self):
        rows = history_fixture()
        self.import_rows(rows)
        before = self.report()
        self.import_rows(rows)
        for minute in (1, 2):
            item = sample(e.iso(START + timedelta(minutes=minute)), 10 + minute / 100)
            self.db.collect(item)
        self.assertEqual(self.report()["values"], before["values"])
        self.assertEqual(self.report()["coverage_intervals"], 6)
        with self.db.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM history_rows").fetchone()[0], len(rows))
            self.assertEqual(db.execute("SELECT count(*) FROM history_batches").fetchone()[0], 1)

    def test_gaps_preserved_and_baseline_saving_use_same_interval_set(self):
        rows = history_fixture()
        next(row for row in rows if row["entity_id"] == c.ENTITIES["import"] and row["timestamp"] == e.iso(START + timedelta(minutes=2)))["state"] = "unavailable"
        self.import_rows(rows)
        period = self.report()
        self.assertEqual(period["coverage_intervals"], 4)
        self.assertEqual(period["gap_count"], 1)
        self.assertEqual(period["excluded_baseline_intervals"], 2)
        expected = Decimal(".04") * Decimal(".25") + Decimal(240) / 86400 * Decimal(".40")
        self.assertAlmostEqual(Decimal(period["values"]["baseline"]), expected, places=25)

    def test_version_correction_recomputes_retained_history_once(self):
        self.import_rows(history_fixture())
        before = self.report()["values"]
        changed = {**self.cfg, "baseline_import_gbp_kwh": ".5"}
        self.db.configure(changed, [])
        after = self.report()["values"]
        self.assertNotEqual(before["baseline"], after["baseline"])
        self.assertEqual(before["actual"], after["actual"])
        self.db.configure(changed, [])
        self.assertEqual(self.report()["values"], after)

    def test_meter_costs_missing_unknown_export_and_negative_saving(self):
        item = {"start": e.iso(START), "end": e.iso(START + timedelta(minutes=1)), "load": ".01", "import": "1", "export": "0"}
        versions = [v for v in tariffs() if v["kind"] == "import"]
        self.assertLess(e.measured_costs(item, versions, self.cfg)["saving"], 0)
        for value in (None, ".01"):
            item["export"] = value
            with self.assertRaises(e.Pending):
                e.measured_costs(item, versions, self.cfg)


class SupportedRecorderAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_executor_query_flags_and_deduplication(self):
        importer = importlib.import_module("solar_finance.recorder_import")
        calls = []
        observed = START + timedelta(hours=6) - timedelta(milliseconds=500)
        # Deliberately no last_reported: Recorder cannot reconstruct that trace.
        state = types.SimpleNamespace(state="1.5", last_updated=observed, last_changed=observed,
                                      attributes={"unit_of_measurement": "MWh"})
        def query(hass, start, end, **kwargs):
            calls.append((start, end, kwargs))
            return {c.ENTITIES["load"]: [state]}
        async def executor(fn):
            return fn()
        history_module = types.ModuleType("homeassistant.components.recorder.history")
        history_module.get_significant_states = query
        helper_module = types.ModuleType("homeassistant.helpers.recorder")
        helper_module.get_instance = lambda hass: types.SimpleNamespace(async_add_executor_job=executor)
        with patch.dict(sys.modules, {history_module.__name__: history_module, helper_module.__name__: helper_module}):
            rows = await importer.fetch_history(object(), c.ENTITIES, START, START + timedelta(hours=7))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(rows), 1)
        for _, _, flags in calls:
            self.assertFalse(flags["include_start_time_state"])
            self.assertFalse(flags["significant_changes_only"])
            self.assertFalse(flags["minimal_response"])
            self.assertFalse(flags["no_attributes"])
            self.assertIn(h.HEARTBEAT, flags["entity_ids"])
        self.assertEqual(rows[0]["unit"], "MWh")
        self.assertIn("Not reconstructed", rows[0]["last_reported_provenance"])


if __name__ == "__main__":
    unittest.main()
