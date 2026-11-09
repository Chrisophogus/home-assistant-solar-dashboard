"""Independent meter-accounting readiness and strictly gated simulation."""

from datetime import datetime, timezone
from bisect import bisect_right
from decimal import Decimal
import json
import hashlib

from .const import COMMISSIONED_AT, ENTITIES, POLICY
from .engine import Pending, baseline_cost, costs, dec, input_blockers, iso, measured_costs, pounds, tariff_at, timestamp, TZ, KEYS, end_use_interval
from .settings import LIMITS, CAPABILITIES, at, Timeline

SCOPE = {
    "id": "sigenergy-monitored-household-v1",
    "label": "Sigenergy-monitored boundary estimate; not a verified whole-bill CT boundary",
    "evidence": "User states Hypervolt is separately metered and must not be added. Installed sigen/static_sensor.py maps plant_accumulated_consumed_energy, plant_accumulated_grid_import_energy and plant_accumulated_grid_export_energy to the three selected plant counters. No Hypervolt counter is added/subtracted. This documents the monitored scope, not independently verified physical CT wiring.",
    "entities": {key: ENTITIES[key] for key in ("import", "export", "load")},
}


def issues(item, keys):
    if "key_errors" in item:
        errors = sorted({e for key in keys for e in item["key_errors"].get(key, [])})
    else:
        errors = item.get("errors", [])
    errors = list(errors)
    if "import" in keys or "export" in keys:
        errors.extend(item.get("column_errors", {}).get("saving", []))
    if "pv" in keys:
        errors.extend(item.get("column_errors", {}).get("schedule", []))
    for key in keys:
        if item.get(key) is None:
            errors.append(f"Missing measured {key} energy")
    return errors


def scope(item, inputs):
    mapping = item.get("source_entities", {})
    if all(mapping.get(k) == v for k, v in SCOPE["entities"].items()):
        return SCOPE["label"]
    if inputs.get("boundary_verified") is True and inputs.get("boundary_evidence"):
        return "User-verified meter boundary: " + inputs["boundary_evidence"]
    raise Pending("Selected meter mapping has no audited shared boundary evidence")


def coverage(items):
    segments, seconds = [], Decimal(0)
    for item in items:
        start, end = timestamp(item["start"]), timestamp(item["end"])
        seconds += dec((end - start).total_seconds())
        if segments and timestamp(segments[-1][1]) == start:
            segments[-1][1] = iso(end)
        else:
            segments.append([iso(start), iso(end)])
    return {"coverage_start": segments[0][0] if segments else None,
            "coverage_end": segments[-1][1] if segments else None,
            "covered_hours": str(seconds / 3600), "coverage_intervals": len(items),
            "gap_count": max(0, len(segments) - 1), "coverage_segments": segments[:1000],
            "segments_truncated": len(segments) > 1000, "full_period": False}


def comparable_schedule(meter_rows, tariffs, settings, models=(), *, policy="registered-peak-only-v1", cost_function=None):
    """Forward fixed policy on covered segments, never backfill gaps with energy.

    Each gap/physical-model change requires a fresh observed SOC. This is a
    conditional segmented estimate, NOT a gap-free lifetime/AI backtest.
    Settings inside a meter interval split energy explicitly pro-rata in time.
    Reject the entire parent interval if any subinterval cannot be simulated.
    """
    settings = settings if isinstance(settings, Timeline) else Timeline(settings)
    selected, excluded, resets = [], set(), []
    stored = previous_end = previous_model = initial = None
    for item, cfg, _measured in meter_rows:
        start, end = timestamp(item["start"]), timestamp(item["end"])
        try:
            if start < timestamp(POLICY["registered_at"]):
                raise Pending("Pre-registration interval excluded from all three comparison columns")
            if issues(item, KEYS):
                raise Pending("Schedule needs matching six-counter coverage")
            candidates = [m for m in models if timestamp(m["effective_from"]) <= start]
            if candidates:
                model = max(enumerate(candidates), key=lambda pair: (timestamp(pair[1]["effective_from"]), pair[0]))[1]
                cfg = {**cfg, **model, "id": cfg["id"] + ":" + model["id"]}
            # Validate closure on the original measured interval. Splitting at
            # a reserve event must not make a failing residual pass tolerance.
            end_use_interval(item, cfg)
            model_identity = tuple((k, str(v)) for k, v in sorted(cfg.items()) if k not in ("reserve_percent", "reserve_evidence", "id", "ledger_registered_at"))
            reset = previous_end != start or previous_model != model_identity or stored is None
            if reset:
                if item.get("initial_soc") is None:
                    raise Pending("Covered segment needs measured starting SOC")
                support = item.get("soc_observed_at") or item["start"]
                if not 0 <= (start - timestamp(support)).total_seconds() <= 90:
                    raise Pending("Covered segment starting SOC is not a fresh observation (90s)")
                candidate = dec(cfg["usable_capacity_kwh"]) * dec(item["initial_soc"]) / 100
            else:
                candidate = stored
            points = sorted({start, end} | settings.between(start, end))
            total = {k: Decimal(0) for k in ("baseline", "actual", "saving", "schedule")}
            ids, assumed, setting_ids = set(), False, []
            for lo, hi in zip(points, points[1:]):
                physical = dict(cfg)
                reserve = at(settings, "reserve_percent", lo)
                if reserve["status"] != "observed":
                    raise Pending("Reserve is " + reserve["status"] + "; saved static reserve is not historical evidence")
                physical.update(reserve_percent=reserve["value"], reserve_evidence=reserve["source"])
                setting_ids.append(reserve)
                if cfg.get("limits_source", "settings_history") == "settings_history":
                    for key in LIMITS:
                        value = at(settings, key, lo)
                        if value["status"] != "observed":
                            raise Pending(f"Timestamped {key} is {value['status']}; ratings/plateaus are not configured limits")
                        physical[key] = value["value"]
                        setting_ids.append(value)
                    if not cfg.get("limit_applicability_evidence"):
                        raise Pending("Remote-EMS register applicability and effective PCS/thermal ceilings require evidence")
                else:
                    # Disclosed historical hardware scenario, but do not ignore
                    # later real unavailable settings or valid restrictive caps.
                    for key in (*LIMITS, "pcs_import_limit_kw", "pcs_export_limit_kw"):
                        observed = at(settings, key, lo)
                        if observed.get("timestamp") and observed["status"] not in ("observed", "not_valid"):
                            raise Pending(f"Current {key} observation is {observed['status']}")
                        if observed["status"] == "observed":
                            # Protocol v2.7 p17: ESS limits apply ONLY to
                            # remote-EMS modes 3/4 (charge) and 5/6 (discharge).
                            # A valid remote value is not a global AI/TOU cap.
                            if key in ("charge_limit_kw", "discharge_limit_kw") and not cfg.get("limit_applicability_evidence"):
                                setting_ids.append(observed)
                                continue
                            cap = dec(observed["value"])
                            if key in ("pcs_import_limit_kw", "pcs_export_limit_kw"):
                                # Separate directional caps, not both directions.
                                target = "charge_limit_kw" if key == "pcs_import_limit_kw" else "discharge_limit_kw"
                                physical[target] = str(min(dec(physical[target]), cap))
                                if key == "pcs_export_limit_kw":
                                    physical["inverter_output_limit_kw"] = str(cap)
                                setting_ids.append(observed)
                                continue
                            if key == "charge_limit_kw":
                                cap /= dec(physical["charge_efficiency"])
                            elif key == "discharge_limit_kw":
                                cap *= dec(physical["discharge_efficiency"])
                            physical[key] = str(min(dec(physical[key]), cap)) if key in physical else str(cap)
                            if key == "grid_import_limit_kw":
                                physical["grid_limit_unreported"] = False
                        setting_ids.append(observed)
                    for key, target in (("available_charge_kw", "battery_charge_dc_limit_kw"), ("available_discharge_kw", "battery_discharge_dc_limit_kw")):
                        observed = at(settings, key, lo)
                        if observed["status"] == "observed" and 0 <= (lo - timestamp(observed["timestamp"])).total_seconds() <= 180:
                            physical[target] = str(min(dec(physical[target]), dec(observed["value"])))
                    for key in ("inverter_max_absorption_power", "inverter_max_active_power", "inverter_max_active_power_adjustment_value", "plant_available_active_kw"):
                        observed = at(settings, key, lo)
                        if observed["status"] == "conflicting":
                            raise Pending("Multiple/conflicting inverter capabilities need aggregate evidence")
                        if observed["status"] == "observed" and 0 <= (lo - timestamp(observed["timestamp"])).total_seconds() <= 180:
                            cap = dec(observed["value"])
                            if key == "inverter_max_absorption_power":
                                physical["charge_limit_kw"] = str(min(dec(physical["charge_limit_kw"]), cap))
                            else:
                                physical["inverter_output_limit_kw"] = str(min(dec(physical.get("inverter_output_limit_kw", physical["inverter_ac_limit_kw"])), cap))
                missing = [x for x in input_blockers(physical) if not x.startswith("Confirm import, export and load")]
                if missing:
                    raise Pending("; ".join(missing))
                fraction = dec((hi - lo).total_seconds()) / dec((end - start).total_seconds())
                part = {**item, "start": iso(lo), "end": iso(hi), **{k: str(dec(item[k]) * fraction) for k in KEYS}}
                part = end_use_interval(part, physical)
                model = (cost_function or costs)(part, tariffs, candidate, physical, **({"policy": policy} if policy != "registered-peak-only-v1" else {}))
                candidate = model["stored"]
                for key in total:
                    total[key] += model[key]
                ids.update(model["tariff_ids"])
                assumed |= model["assumed_tariff"]
            if reset:
                resets.append({"timestamp": item["start"], "measured_soc": item["initial_soc"], "reason": "Start, gap, rejection or model revision; no energy carried over omitted time"})
                if initial is None:
                    initial = dec(cfg["usable_capacity_kwh"]) * dec(item["initial_soc"]) / 100
            stored, previous_end, previous_model = candidate, end, model_identity
            selected.append((item, cfg, {**total, "tariff_ids": sorted(ids), "assumed_tariff": assumed, "settings_evidence": setting_ids}))
        except (Pending, ValueError, ArithmeticError, KeyError) as err:
            excluded.add(str(err))
            stored = previous_end = previous_model = None
    return selected, excluded, resets, initial, stored


def coverage_fingerprint(rows):
    """Hash every exact interval boundary, not only truncated display segments."""
    return hashlib.sha256(json.dumps([(r["start"], r["end"]) for r in rows], separators=(",", ":")).encode()).hexdigest()


def ordinary_schedule_scenario(comparison, tariffs, settings, parent, period, current, cost_function=None):
    """Add a cash-only alternative without replacing the registered policy.

    Historical replay is explicitly retrospective, never backdated registration.
    Reject the whole alternative on any coverage/value mismatch; actual cash
    accounting and the original policy results remain visible and unchanged.
    """
    result = {"scenario_id": "cheap-window-self-consumption-v2", "period": period,
              "calculated_at": iso(current), "matched": False, "values": None,
              "status": "Unavailable on identical displayed coverage",
              "prospectively_registered": False, "legacy_policy_id": POLICY["id"],
              "description": "02–05 Europe/London charge towards full; PV to house then battery; self-consume outside cheap period to timestamped reserve; no deliberate battery export",
              "qualification": "Retrospective fixed-rule estimate on the displayed recorded intervals, not a prospectively registered historical policy or proven AI advantage. Tariff/model assumptions and effective dates are retained.",
              "inventory_qualification": "Cash accounting only. Equal observed starting SOC at each gap/model reset; no energy carried across omitted time. Different terminal inventories and missing historical endpoint evidence can bias costs. No terminal-energy credit is added to realised cash saving."}
    if not comparison or not parent.get("values"):
        result["blockers"] = ["No complete identical main-comparison interval set"]
        return result
    rows = [r[0] for r in comparison]
    # Already-selected effective configurations are reused, without remerging
    # today's model or changing any tariff/settings/physical-loss evidence.
    variants, errors, resets, initial, final = comparable_schedule(
        comparison, tariffs, settings, (), policy=result["scenario_id"], cost_function=cost_function)
    same_intervals = [(r[0]["start"],r[0]["end"]) for r in variants] == [(r["start"],r["end"]) for r in rows]
    sums = {k: sum((r[2][k] for r in variants), Decimal(0)) for k in ("baseline", "actual", "saving", "schedule")}
    sums["saving"] = sums["baseline"] - sums["actual"]
    matched = (same_intervals and not errors
               and coverage_fingerprint(rows) == parent["coverage_fingerprint"]
               and all(sums[k] == dec(parent["values"][k]) for k in ("baseline", "actual", "saving"))
               and resets == parent["soc_resets"]
               and initial == dec(parent["simulated_initial_stored_kwh"]))
    if not matched:
        result["blockers"] = sorted(errors) + ["Ordinary scenario must match every main interval, baseline/actual value and initial-SOC reset; no subset fallback"]
        return result
    result.update(matched=True, status="Retrospective estimate on identical displayed coverage",
                  coverage=coverage(rows), coverage_fingerprint=parent["coverage_fingerprint"],
                  matched_parent_values={k: parent["values"][k] for k in ("baseline", "actual", "saving")},
                  values={"baseline": str(sums["baseline"]), "actual": str(sums["actual"]),
                          "schedule_cost": str(sums["schedule"]), "schedule_saving": str(sums["baseline"]-sums["schedule"])},
                  display={"saving": pounds(sums["baseline"]-sums["schedule"])},
                  soc_resets=resets, simulated_initial_stored_kwh=str(initial), simulated_final_stored_kwh=str(final),
                  actual_terminal_soc=rows[-1].get("terminal_soc"),
                  tariff_version_ids=sorted({i for r in variants for i in r[2]["tariff_ids"]}),
                  input_version_ids=sorted({r[1]["id"] for r in variants}),
                  assumed_tariff=any(r[2]["assumed_tariff"] for r in variants), blockers=[])
    return result


def build_report(ledger, current=None):
    current = current or datetime.now(timezone.utc)
    # Report-local exact memoisation only, no persistent/cross-tariff cache or
    # numerical approximation. Month/lifetime repeat many identical native
    # physics calls; retaining their results bounds the added hourly workload.
    cost_cache = {}
    def cached_cost(part, versions, stored, physical, **kwargs):
        key = (json.dumps([part, physical], sort_keys=True, default=str), stored,
               kwargs.get("policy", "registered-peak-only-v1"))
        if key not in cost_cache:
            if len(cost_cache) >= 100000:
                cost_cache.clear()
            cost_cache[key] = costs(part, versions, stored, physical, **kwargs)
        return cost_cache[key]
    tariff, inputs = ledger.tariff_versions(), ledger.versions("inputs")
    settings = Timeline(ledger.setting_observations())
    models = ledger.versions("schedule_model")
    with ledger.connect() as db:
        last = db.execute("SELECT timestamp,payload FROM samples ORDER BY id DESC LIMIT 1").fetchone()
        count = db.execute("SELECT count(*) FROM intervals").fetchone()[0]
        live = db.execute("SELECT i.payload,s.payload AS sample_payload FROM intervals i LEFT JOIN samples s ON s.timestamp=i.start ORDER BY i.start").fetchall()
        historical = db.execute("SELECT payload FROM history_intervals ORDER BY rowid DESC").fetchall()
        mode_row = db.execute("SELECT payload FROM history_rows WHERE entity_id=? AND json_extract(payload,'$.state')='AI Mode' ORDER BY timestamp LIMIT 1", (ENTITIES["mode"],)).fetchone()
    # Newest reconstruction per bin wins; raw observations and old derivations
    # remain append-only. Historical bins take priority over overlapping live
    # intervals, never double count them or clip measured deltas in half.
    bins, occupied = set(), []
    for row in historical:
        item = json.loads(row[0])
        key = (item["start"], item["end"], json.dumps(item["source_entities"], sort_keys=True))
        if key in bins:
            continue
        bins.add(key)
        if not issues(item, ("load",)):
            occupied.append(item)
    occupied.sort(key=lambda r: timestamp(r["start"]))
    history_ends = [timestamp(r["end"]) for r in occupied]
    for row in live:
        item = json.loads(row["payload"])
        if "source_entities" not in item and row["sample_payload"]:
            item["source_entities"] = json.loads(row["sample_payload"]).get("sources", {})
        start, end = timestamp(item["start"]), timestamp(item["end"])
        position = bisect_right(history_ends, start)
        if position >= len(history_ends) or timestamp(occupied[position]["start"]) >= end:
            occupied.append(item)
    occupied.sort(key=lambda r: timestamp(r["start"]))
    # Source changes must not create overlapping energy even across historical
    # source-set revisions. Keep a deterministic non-overlapping selection.
    rows, previous_end = [], None
    for item in occupied:
        start, end = timestamp(item["start"]), timestamp(item["end"])
        if previous_end is not None and start < previous_end:
            continue
        rows.append(item)
        previous_end = end
    sample_current = last is not None and 0 <= (current - timestamp(last["timestamp"])).total_seconds() <= 180
    sample_errors = json.loads(last["payload"]).get("errors", []) if last else []
    result = {"status": "Collecting data", "commissioned_at": COMMISSIONED_AT,
              "policy_registered_at": POLICY["registered_at"], "operation_label": "Actual operation — AI activation unconfirmed",
              "last_sample_at": last["timestamp"] if last else None, "interval_count": count,
              "collector_current": sample_current, "collector_sample_errors": sample_errors,
              "scope": SCOPE, "historical_backfill": ledger.backfill_status(), "periods": {}, "tariff_history": tariff,
              "methodology": "Sigenergy-monitored boundary measured-meter estimates, NOT verified whole-bill CT coverage or supplier invoices. Separately metered Hypervolt is not added. Baseline uses the same recorded load with versioned Flexible rates and standing. Actual saving = baseline − timestamp-priced actual import/standing + export revenue. Missing simulator calibration does not block these measured costs. Short tariff-boundary intervals split pro-rata by elapsed time. Standing is charged only for covered local-day fractions (DST aware). Every displayed amount gives its covered dates; gaps are omitted, never extrapolated into a full month/lifetime. Schedule is COST and needs separate verified limits/losses/reserve, not an AI comparison. Decimal values retained; display uses mathematical ceiling to whole GBP."}
    result["live_settings"] = {key: at(settings, key, current) for key in ("reserve_percent", *LIMITS, *CAPABILITIES, "inverter_max_absorption_power", "inverter_max_active_power", "inverter_max_active_power_adjustment_value")}
    with ledger.connect() as db:
        result["settings_history_imported"] = db.execute("SELECT value FROM metadata WHERE key='settings_history_imported'").fetchone() is not None
    result["schedule_model_history"] = models
    mode = json.loads(mode_row[0]) if mode_row else None
    result["mode_evidence"] = {
        "first_retained_ai_mode_at": mode["timestamp"] if mode else None,
        "source": mode.get("source") if mode else None,
        "entity_id": ENTITIES["mode"],
        "current_recorded_mode": json.loads(last["payload"]).get("mode") if last else None,
        "qualification": "First retained Recorder AI Mode observation, not commissioning, proof of initial activation, or continuous AI operation throughout the covered period.",
    }
    result["operation_label"] = "Actual recorded operation" + (" — AI Mode first retained " + mode["timestamp"] if mode else " — no retained AI Mode observation")
    result["methodology"] = "All three monetary columns, when schedule is available, use identical covered post-registration segments and the revised estimated AC end-use boundary. Inclusive register load is corrected for modelled actual conversion losses BEFORE both baseline and schedule calculations. Actual cost uses measured timestamp-priced import/export plus covered standing. Original inclusive-register accounting and all prior report records remain retained separately. Model calibration is retrospective and estimated; policy timing remains prospectively fixed. No verified AI performance comparison. Missing periods are excluded, never extrapolated; each trajectory gap resets to a fresh measured SOC. Different terminal inventories and resets limit interpretation. Tariffs retain their existing provenance; whole-pound display uses ceiling."
    result["methodology"] += " Short tariff, schedule and reserve boundaries split pro-rata by elapsed time; this is an allocation estimate, not measured subinterval energy."
    for kind in ("import", "export"):
        try:
            active = tariff_at(tariff, kind, current)
            result[f"current_{kind}_tariff"] = {k: active[k] for k in ("template", "effective_from", "assumed", "bands", "standing", "rate_unit", "standing_unit", "id") if k in active}
        except Pending:
            result[f"current_{kind}_tariff"] = None
    month = datetime.combine(current.astimezone(TZ).date().replace(day=1), datetime.min.time(), TZ).astimezone(timezone.utc)
    for period, lower in (("month", month), ("lifetime", timestamp(COMMISSIONED_AT))):
        baseline_rows, meter_rows, rejected = [], [], {"baseline": set(), "saving": set(), "schedule": set()}
        for item in rows:
            start, end = timestamp(item["start"]), timestamp(item["end"])
            if start < lower or end > current:
                continue
            try:
                cfg = ledger._inputs_at(inputs, start)
                if any(v.get("effective_from") and start < timestamp(v["effective_from"]) < end for v in inputs):
                    raise Pending("Interval crosses dated input revision; no attribution across evidence versions")
                error = issues(item, ("load",))
                if error:
                    raise Pending("; ".join(error))
                if item.get("source_entities", {}).get("load") != ENTITIES["load"] and not (cfg.get("boundary_verified") is True and cfg.get("boundary_evidence")):
                    raise Pending("Selected load is not the documented Sigenergy household scope")
                base = baseline_cost(item, cfg)
                baseline_rows.append((item, cfg, base))
            except (Pending, ValueError) as err:
                rejected["baseline"].add(str(err))
                continue
            try:
                scope(item, cfg)
                error = issues(item, ("load", "import", "export"))
                if error:
                    raise Pending("; ".join(error))
                measured = measured_costs(item, tariff, cfg)
                meter_rows.append((item, cfg, measured))
            except (Pending, ValueError) as err:
                rejected["saving"].add(str(err))
        comparison, exclusions, resets, segment_initial, segment_final = comparable_schedule(meter_rows, tariff, settings, models, cost_function=cached_cost)
        measured_only_coverage = coverage([r[0] for r in meter_rows])
        # When schedule is computable, ALL columns use its exact interval set.
        # Full measured accounting remains inspectable, not silently discarded.
        measured_only_totals = {k: str(sum((r[2][k] for r in meter_rows), Decimal(0))) for k in ("baseline", "actual", "saving")}
        if comparison:
            meter_rows = comparison
        rejected["schedule"].update(exclusions)
        # If savings can be shown, baseline uses EXACTLY that same interval set.
        # Otherwise baseline can still stand alone; schedule never gates either.
        selected = meter_rows if meter_rows else baseline_rows
        common = [row[0] for row in selected]
        bounds = coverage(common)
        totals = {"baseline": None, "actual": None, "saving": None, "schedule": None}
        ids, cfg_ids, assumed = set(), set(), False
        if meter_rows:
            for key in ("baseline", "actual", "saving"):
                totals[key] = sum((row[2][key] for row in meter_rows), Decimal(0))
            totals["saving"] = totals["baseline"] - totals["actual"]
            for item, cfg, measured in meter_rows:
                ids.update(measured["tariff_ids"])
                cfg_ids.add(cfg["id"])
                assumed |= measured["assumed_tariff"]
        elif baseline_rows:
            totals["baseline"] = sum((row[2] for row in baseline_rows), Decimal(0))
            cfg_ids.update(cfg["id"] for _, cfg, _ in baseline_rows)
        initial_stored, stored = segment_initial, segment_final
        sensitivities = []
        if comparison:
            totals["schedule"] = sum((r[2]["schedule"] for r in comparison), Decimal(0))
            central_keys = [(r[0]["start"], r[0]["end"]) for r in comparison]
            # Every sensitivity must use exactly the central eligible set. Do
            # not compare a low-loss sunny subset with a high-loss night subset.
            for override in comparison[0][1].get("sensitivity", []):
                unchanged = all(
                    all(key in cfg and type(cfg[key]) is type(value) and cfg[key] == value
                        for key, value in override.items())
                    for _, cfg, _ in comparison
                )
                if unchanged:
                    variants, errors = comparison, set()
                else:
                    altered_models = [{**m, **override} for m in models]
                    variants, errors, _, _, _ = comparable_schedule(comparison, tariff, settings, altered_models, cost_function=cached_cost)
                valid = [(r[0]["start"], r[0]["end"]) for r in variants] == central_keys
                sensitivities.append({"parameters": override, "status": "Illustrative scenario, not confidence bounds" if valid else "Unavailable on identical central coverage",
                                      "values": {key: str(sum((r[2][key] for r in variants), Decimal(0))) for key in ("baseline", "actual", "saving", "schedule")} if valid else None,
                                      "covered_hours": bounds["covered_hours"] if valid else None,
                                      "blockers": sorted(errors) if not valid else []})
        elif not common:
            rejected["schedule"].add("No common covered meter intervals yet")
        column_blockers = {}
        for column in ("baseline", "saving", "schedule"):
            column_blockers[column] = [] if totals[column] is not None else sorted(rejected[column] or {"Waiting for eligible recorded intervals"})[:30]
        all_pending = all(value is None for value in totals.values())
        result["periods"][period] = {**bounds, "status": "Collecting data" if all_pending else "Covered intervals estimate",
            "values": None if all_pending else {key: str(value) if value is not None else None for key, value in totals.items()},
            "display": None if all_pending else {key: pounds(value) if value is not None else None for key, value in totals.items()},
            "column_blockers": column_blockers, "blockers": sorted({v for group in column_blockers.values() for v in group}),
            "excluded_baseline_intervals": len(baseline_rows) - len(meter_rows) if meter_rows else 0,
            "exclusion_examples": {key: sorted(value)[:8] for key, value in rejected.items() if key != "schedule"},
            "tariff_version_ids": sorted(ids), "input_version_ids": sorted(cfg_ids), "policy_id": POLICY["id"], "assumed_tariff": assumed,
            "model": "Baseline, actual saving and schedule COST share identical covered segments when schedule is available. Estimated inclusive-load conversion correction applies BEFORE baseline and schedule. Gaps reset from measured SOC, not extrapolated energy. No terminal inventory credit; partial trajectories/inventory differences limit interpretation. Retrospective calibration, NOT a verified AI comparison.",
            "boundary_version": "sigenergy-estimated-ac-end-use-v2" if comparison else SCOPE["id"],
            "sensitivity": sensitivities,
            "comparison_scope": "All three columns share covered post-registration segments" if comparison else "Meter-only coverage; schedule unavailable, not a three-way comparison",
            "meter_only_accounting": {**measured_only_coverage, "values": measured_only_totals},
            "schedule_exclusion_reasons": sorted(exclusions), "soc_resets": resets,
            "calibration": [{"input_id": cfg["id"], "status": cfg.get("calibration_status", "verified"), "loss_evidence": cfg.get("loss_evidence"), "uncertainty": cfg.get("calibration_uncertainty"), "load_boundary": cfg.get("load_boundary", "ac_end_use")} for cfg in {cfg["id"]: cfg for _, cfg, _ in comparison}.values()],
            "simulated_initial_stored_kwh": str(initial_stored) if totals["schedule"] is not None else None,
            "simulated_final_stored_kwh": str(stored) if totals["schedule"] is not None else None,
            "actual_terminal_soc": common[-1].get("terminal_soc") if common else None,
            "preceding_gap": None, "scan_limited": False,
            "coverage_fingerprint": coverage_fingerprint(common)}
        result["periods"][period]["ordinary_schedule_scenario"] = ordinary_schedule_scenario(
            comparison, tariff, settings, result["periods"][period], period, current, cost_function=cached_cost)
    if any(p["values"] is not None for p in result["periods"].values()):
        result["status"] = "Covered intervals estimate"
    result["blockers"] = sorted({v for p in result["periods"].values() for v in p["blockers"]})
    # Additive derived view only. Existing hourly executor owns this call;
    # native parent values, legacy scenarios and raw/history rows stay intact.
    from .runtime_continuity import corrected_view
    try:
        result["corrected_view"] = corrected_view(ledger, comparison, tariff, settings, inputs, models, current)
    except (Pending, ValueError, ArithmeticError, KeyError, TimeoutError) as err:
        result["corrected_view"] = {"schema": 1, "status": "Corrected model view pending",
                                    "periods": {}, "blockers": [str(err)]}
    return result
