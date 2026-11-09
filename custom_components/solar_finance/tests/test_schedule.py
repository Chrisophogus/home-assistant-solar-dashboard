"""Read-only observations, estimated boundary and same-period schedule regression."""

import asyncio
import ast
from copy import deepcopy
from datetime import timedelta, timezone
from decimal import Decimal as D
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest

from test_engine import e, l, c, ROOT, Frozen, inputs, tariffs, interval

s = importlib.import_module("solar_finance.settings")
r = importlib.import_module("solar_finance.reporting")
m = importlib.import_module("solar_finance.model")


def reserve(when="2026-10-28T00:00:00Z", value="25"):
    return s.observation("reserve_percent", value, when, "TEST Recorder", "%")


def cfg():
    return {**inputs(), **m.ESTIMATED_MODEL, "id": "TEST-model"}


def row(start="2026-10-28T16:00:00Z", **changes):
    # Counter-balanced DC-register fixture. Not a production measurement.
    return {**interval(start, load=".06", pv=".01"), "import": ".04", "export": "0",
            "charge": "0", "discharge": ".01", "initial_soc": "75",
            "source_entities": c.ENTITIES, **changes}


def compare(rows, settings=None, config=None):
    return r.comparable_schedule([(i, config or cfg(), {}) for i in rows], tariffs(), settings if settings is not None else [reserve()])


class SettingsTests(unittest.TestCase):
    def test_sentinel_not_a_limit_and_not_missing(self):
        for key in (*s.LIMITS, "pcs_import_limit_kw", "pcs_export_limit_kw"):
            obs = s.observation(key, "4294967.295", "2026-11-02T10:00:00Z", "TEST diagnostic", "kW")
            self.assertEqual(obs["status"], "not_valid")
            self.assertIsNone(obs["value"])
            self.assertEqual(obs["raw_value"], "4294967.295")

    def test_unknown_unavailable_missing_invalid_and_zero(self):
        for raw, expected in (("unknown", "unknown"), ("unavailable", "unavailable"), (None, "missing"), ("nan", "invalid"), ("101", "invalid"), ("0", "observed")):
            self.assertEqual(reserve(value=raw)["status"], expected)
        self.assertEqual(s.observation("charge_limit_kw", "0", "2026-11-02T10:00:00Z", "TEST", "kW")["value"], "0")

    def test_dated_backfill_no_early_extrapolation_and_unavailable_fence(self):
        rows = [reserve(), reserve("2026-10-29T00:00:00Z", "unavailable"), reserve("2026-10-30T00:00:00Z", "10")]
        for evidence in (rows, s.Timeline(rows)):
            self.assertEqual(s.at(evidence, "reserve_percent", e.timestamp("2026-10-27T23:59:59Z"))["status"], "unknown")
            self.assertEqual(s.at(evidence, "reserve_percent", e.timestamp("2026-10-28T01:00:00Z"))["value"], "25")
            self.assertEqual(s.at(evidence, "reserve_percent", e.timestamp("2026-10-29T01:00:00Z"))["status"], "unavailable")

    def test_conflicting_observations_are_not_arbitrarily_selected(self):
        self.assertEqual(s.at(s.Timeline([reserve(), reserve(value="50")]), "reserve_percent", e.timestamp("2026-10-28T01:00:00Z"))["status"], "conflicting")

    def test_numeric_serializations_are_equal_without_mutating_raw_evidence(self):
        for value in ("10", "25", "50"):
            rows = [reserve(value=value), reserve(value=value + ".0"), reserve(value=value + ".00")]
            original = deepcopy(rows)
            for evidence in (rows, s.Timeline(rows)):
                self.assertEqual(s.at(evidence, "reserve_percent", e.timestamp("2026-10-28T01:00:00Z"))["status"], "observed")
            self.assertEqual(rows, original)
        unavailable = [reserve(), reserve(value="unavailable")]
        self.assertEqual(s.at(unavailable, "reserve_percent", e.timestamp("2026-10-28T01:00:00Z"))["status"], "conflicting")

    def test_diagnostic_allowlist_units_and_dynamic_vs_rated(self):
        plant = {name: "4294967.295" for name, _ in s.LIMITS.values()}
        plant.update(plant_ess_rated_charging_power="8.8", plant_ess_available_max_charging_power="2.5")
        rows = s.diagnostic_rows({"coordinator": {"last_update_success": True}, "data": {"plant": plant, "inverters": {"one": {"inverter_max_active_power": "5.5"}}}, "entry": {"password": "NEVER-SAVE-THIS"}}, "2026-11-02T10:00:00Z")
        self.assertNotIn("NEVER-SAVE-THIS", json.dumps(rows))
        self.assertEqual(next(x["value"] for x in rows if x["key"] == "rated_charge_kw"), "8.8")
        self.assertEqual(next(x["value"] for x in rows if x["key"] == "available_charge_kw"), "2.5")

    def test_reserve_import_preserves_only_actual_rows(self):
        rows = s.reserve_rows([{"entity_id": s.RESERVE_ENTITY, "state": "25", "unit": "%", "timestamp": "2026-10-29T06:00:00.000000Z"}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timestamp"], "2026-10-29T06:00:00+00:00")

    def test_append_only_idempotence_and_report_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = l.Ledger(Path(tmp) / "ledger.sqlite3"); ledger.initialize()
            ledger.observe_settings([reserve(), reserve()])
            self.assertEqual(len(ledger.setting_observations()), 1)
            with self.assertRaises(sqlite3.IntegrityError):
                with ledger.connect() as db:
                    db.execute("DELETE FROM setting_observations")
            self.assertEqual(len(ledger.setting_observations()), 1)

    def test_actual_event_callback_immediate_timestamp_and_refresh(self):
        tree = ast.parse((ROOT / "__init__.py").read_text())
        runtime = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Runtime")
        method = next(n for n in runtime.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "reserve_changed")
        namespace = {"observation": s.observation, "iso": e.iso, "RESERVE_ENTITY": s.RESERVE_ENTITY}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "actual reserve event", "exec"), namespace)
        async def run():
            captured, refreshed = [], []
            async def executor(fn, arg): fn(arg)
            async def refresh(): refreshed.append(True)
            fake = types.SimpleNamespace(lock=asyncio.Lock(), ledger=types.SimpleNamespace(observe_settings=captured.extend), hass=types.SimpleNamespace(async_add_executor_job=executor), refresh=refresh)
            event = types.SimpleNamespace(time_fired=e.timestamp("2026-11-02T10:00:00Z"), data={"new_state": types.SimpleNamespace(state="10", attributes={"unit_of_measurement": "%"})})
            await namespace["reserve_changed"](fake, event)
            self.assertEqual(captured[0]["value"], "10")
            self.assertEqual(captured[0]["timestamp"], e.iso(event.time_fired))
            self.assertTrue(refreshed)
        asyncio.run(run())


class RevisedBoundaryTests(unittest.TestCase):
    def test_no_double_counting_and_baseline_uses_same_revised_house_load(self):
        original = row(charge=".02", discharge="0", load=".03", pv=".01", **{"import": ".04"})
        corrected = e.end_use_interval(original, cfg())
        expected = D(".03") - D(".02") * (1 / D(".95") - 1)
        self.assertEqual(D(corrected["load"]), expected)
        self.assertEqual(original["load"], ".03")
        selected, _, _, _, _ = compare([original])
        result = selected[0][2]
        self.assertEqual(result["baseline"], expected * D(".25") + D(60) / D(86400) * D(".40"))
        # Actual bill is never adjusted for an inferred loss correction.
        self.assertEqual(result["actual"], e.measured_costs(original, tariffs(), cfg())["actual"])

    def test_bad_boundary_and_negative_demand_are_not_clamped(self):
        with self.assertRaises(e.Pending): e.end_use_interval(row(load="1"), cfg())
        with self.assertRaises(e.Pending): e.end_use_interval(row(load="0", charge=".5", pv=".5", **{"import": "0", "discharge": "0"}), cfg())

    def test_zero_ac_auxiliary_does_not_create_stored_side_loss(self):
        config = cfg(); config.pop("id"); config["standby_loss_kw"] = ".05"
        with self.assertRaises(ValueError): e.validate_inputs(config)
        result = e.simulate(interval("2026-10-28T12:00:00Z", load=".05", pv="0"), D("10"), cfg())
        self.assertEqual(result["stored"], D("10"))

    def test_inverter_ac_and_battery_dc_caps_energy_conservation(self):
        config = cfg(); config["battery_charge_dc_limit_kw"] = "1"
        charge = e.simulate(interval("2026-10-28T02:00:00Z", seconds=3600, load="0", pv="0"), D("1"), config)
        self.assertLessEqual(charge["charge"] * D(config["charge_efficiency"]), D("1"))
        config["battery_discharge_dc_limit_kw"] = ".5"
        discharge = e.simulate(interval(seconds=3600, load="8", pv="1"), D("8"), config)
        self.assertLessEqual(discharge["discharge"], D(".5") * D(".90"))
        self.assertLessEqual(discharge["discharge"] + D("1"), D("5.5"))
        for result, initial, load, pv in ((charge, D("1"), D("0"), D("0")), (discharge, D("8"), D("8"), D("1"))):
            self.assertLess(abs(pv + result["import"] - load - result["export"] - result["curtailment"] - (result["stored"] - initial) - result["loss"]), D("1e-20"))

    def test_zero_power_cap_disables_charge_without_inventing_energy(self):
        config = cfg(); config.pop("id"); config["charge_limit_kw"] = "0"; e.validate_inputs(config)
        result = e.simulate(interval("2026-10-28T02:00:00Z"), D("1"), config)
        self.assertEqual(result["charge"], 0)

    def test_current_valid_ess_cap_is_not_confused_with_sentinel(self):
        settings = [reserve(), s.observation("discharge_limit_kw", ".1", "2026-10-28T00:00:00Z", "TEST", "kW")]
        config = cfg(); config["limit_applicability_evidence"] = "TEST hypothetical remote-EMS discharge mode 5"
        selected, errors, _, _, _ = compare([row()], settings, config)
        self.assertTrue(selected, errors)
        self.assertGreater(selected[0][2]["schedule"], compare([row()])[0][0][2]["schedule"])
        # A remote-only configured value does not constrain AI/TOU by default.
        self.assertEqual(compare([row()], settings)[0][0][2]["schedule"], compare([row()])[0][0][2]["schedule"])

    def test_midinterval_reserve_change_and_no_saved_50_fallback(self):
        settings = [reserve(value="75"), reserve("2026-10-28T16:00:30Z", "25")]
        selected, errors, _, _, _ = compare([row()], settings)
        self.assertTrue(selected, errors)
        low = compare([row()], [reserve(value="25")])[0][0][2]["schedule"]
        high = compare([row()], [reserve(value="75")])[0][0][2]["schedule"]
        self.assertLess(low, selected[0][2]["schedule"])
        self.assertLess(selected[0][2]["schedule"], high)
        selected, errors, *_ = compare([row()], [])
        self.assertFalse(selected); self.assertTrue(any("Reserve is unknown" in x for x in errors))

    def test_gap_resets_soc_and_does_not_carry_hidden_energy(self):
        rows = [row(), row("2026-10-28T16:01:00Z"), row("2026-10-28T17:00:00Z", initial_soc="25")]
        selected, errors, resets, initial, final = compare(rows)
        self.assertEqual(len(selected), 3, errors)
        self.assertEqual(len(resets), 2)
        self.assertEqual(final, D("10") * D(".25"))
        rows[-1]["soc_observed_at"] = rows[0]["start"]
        self.assertEqual(len(compare(rows)[0]), 2)

    def test_unavailable_inside_interval_rejects_whole_interval(self):
        selected, errors, *_ = compare([row()], [reserve(), reserve("2026-10-28T16:00:30Z", "unavailable")])
        self.assertFalse(selected)
        self.assertTrue(any("unavailable" in x for x in errors))

    def test_prospective_policy_unchanged(self):
        before = c.POLICY
        self.assertEqual(before["id"], "flux-house-only-v1")
        self.assertEqual(before["registered_at"], c.POLICY_REGISTERED_AT)
        self.assertTrue(m.ESTIMATED_MODEL["retrospective_calibration"])


class IntegratedReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = l.Ledger(Path(self.tmp.name) / "ledger.sqlite3")
        self.ledger.initialize(); self.ledger.configure(inputs(), [])
        self.ledger.observe_settings([reserve()])
        start = e.timestamp("2026-10-28T16:00:00Z")
        delta = {"import": D(".04"), "export": D("0"), "load": D(".06"), "pv": D(".01"), "charge": D("0"), "discharge": D(".01")}
        for n in range(3):
            when = e.iso(start + timedelta(minutes=n))
            self.ledger.collect({"timestamp": when, "energy": {k: str(D(100) + n * delta[k]) for k in e.KEYS}, "soc": "75", "mode": "AI Mode", "sources": c.ENTITIES,
                                 "errors": [], "key_errors": {}, "collection_session": "TEST", "source_timestamps": {k: when for k in c.ENTITIES}})
        self.current = start + timedelta(minutes=2, seconds=5)

    def tearDown(self): self.tmp.cleanup()

    def test_three_columns_same_revised_boundary_and_sensitivity_coverage(self):
        period = r.build_report(self.ledger, self.current)["periods"]["lifetime"]
        self.assertEqual(period["boundary_version"], "sigenergy-estimated-ac-end-use-v2")
        self.assertEqual(period["coverage_intervals"], 2)
        self.assertEqual(D(period["covered_hours"]), D(120) / 3600)
        self.assertEqual(len(period["sensitivity"]), 3)
        for variant in period["sensitivity"]:
            self.assertIsNotNone(variant["values"])
            self.assertEqual(variant["covered_hours"], period["covered_hours"])
            self.assertEqual(variant["values"]["actual"], period["values"]["actual"])
        corrected_load = D(".12") - D(".02") * (1 - D(".90"))
        self.assertEqual(D(period["values"]["baseline"]), corrected_load * D(".25") + D(120) / 86400 * D(".40"))
        self.assertGreater(D(period["meter_only_accounting"]["values"]["baseline"]), D(period["values"]["baseline"]))

    def test_recorder_reimport_decimal_format_retains_original_common_coverage(self):
        before = r.build_report(self.ledger, self.current)["periods"]
        self.ledger.observe_settings([reserve(value="25.0")])
        after = r.build_report(self.ledger, self.current)["periods"]
        for name in ("month", "lifetime"):
            self.assertEqual(before[name]["coverage_segments"], after[name]["coverage_segments"])
            self.assertEqual(before[name]["values"], after[name]["values"])
        self.assertEqual(len(self.ledger.setting_observations()), 2)

    def test_first_retained_ai_mode_is_evidence_not_activation_claim(self):
        when = "2026-10-28T16:43:00.931899+00:00"
        with self.ledger.connect() as db:
            payload = json.dumps({"timestamp": when, "entity_id": c.ENTITIES["mode"], "state": "AI Mode", "source": "TEST Recorder read-only API"})
            db.execute("INSERT INTO history_rows VALUES (?,?,?,?)", ("TEST-mode", c.ENTITIES["mode"], when, payload))
        report = r.build_report(self.ledger, e.timestamp("2026-10-28T17:00:00Z"))
        self.assertEqual(report["mode_evidence"]["first_retained_ai_mode_at"], when)
        self.assertIn("not commissioning", report["mode_evidence"]["qualification"])

    def test_tariff_correction_recomputes_schedule_idempotently_and_preserves_reports(self):
        old = r.build_report(self.ledger, self.current)["periods"]["lifetime"]
        self.ledger.report()
        before = len(self.ledger.versions("tariff"))
        target = next(v for v in self.ledger.tariff_versions() if v["kind"] == "import" and v["template"] == "flux_import")
        edit = {k: v for k, v in target.items() if k not in ("id", "ledger_registered_at")}
        edit.update(replaces=target["id"], bands=[[0, 1440, "1"]], standing="1", evidence="TEST corrected price and standing")
        self.ledger.configure(inputs(), [], [edit]); self.ledger.configure(inputs(), [], [edit])
        self.assertEqual(len(self.ledger.versions("tariff")), before + 1)
        revised = r.build_report(self.ledger, self.current)["periods"]["lifetime"]
        self.assertEqual(old["coverage_segments"], revised["coverage_segments"])
        self.assertEqual(old["values"]["baseline"], revised["values"]["baseline"])
        self.assertNotEqual(old["values"]["schedule"], revised["values"]["schedule"])
        with self.ledger.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM reports").fetchone()[0], 1)
