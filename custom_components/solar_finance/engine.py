"""Pure, Decimal-based accounting and fixed-policy simulation. No HA imports.

All energy is AC-side kWh unless explicitly named stored_kwh. Intervals are
UTC half-open [start,end). Short cumulative intervals split pro-rata by time
across tariff discontinuities, explicitly labelled as allocation estimates.
Standing charges alone are prorated by elapsed fraction of each London civil
day (a 23/25-hour DST day still incurs exactly one daily charge).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from zoneinfo import ZoneInfo
from functools import lru_cache

TZ = ZoneInfo("Europe/London")
UTC = timezone.utc
KEYS = ("import", "export", "load", "pv", "charge", "discharge")


class Pending(ValueError):
    """Insufficient verified evidence, never a financial zero."""


def dec(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite input")
    return result


@lru_cache(maxsize=65536)
def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp requires UTC offset")
    return result.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat()


def pounds(value):
    """Mathematical ceiling, including negatives; retain precise value elsewhere."""
    n = int(dec(value).to_integral_value(rounding=ROUND_CEILING))
    return ("−" if n < 0 else "") + "£" + format(abs(n), ",")


def normalize_rate(value, unit):
    """Explicit currency units; never silently interpret pence as pounds."""
    if unit in ("GBP_per_kWh", "GBP_per_day"):
        return dec(value)
    if unit in ("pence_per_kWh", "pence_per_day"):
        return dec(value) / 100
    raise ValueError("Explicit GBP or pence rate unit required")


def local_timestamp(date, time):
    """Reject ambiguous/nonexistent local wall times; caller can use RFC3339."""
    naive = datetime.fromisoformat(date + "T" + time)
    if naive.tzinfo is not None:
        raise ValueError("Expected local date/time")
    a, b = naive.replace(tzinfo=TZ, fold=0), naive.replace(tzinfo=TZ, fold=1)
    valid = [t for t in (a, b) if t.astimezone(UTC).astimezone(TZ).replace(tzinfo=None) == naive]
    if not valid or (len(valid) == 2 and a.utcoffset() != b.utcoffset()):
        raise ValueError("Ambiguous/nonexistent London time; use unambiguous confirmation time")
    return iso(valid[0])


def civil_fraction(start, end):
    """Fractional standing-charge days, including partial commissioning days."""
    total = Decimal(0)
    while start < end:
        local = start.astimezone(TZ)
        midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), TZ).astimezone(UTC)
        day_start = datetime.combine(local.date(), datetime.min.time(), TZ).astimezone(UTC)
        stop = min(end, midnight)
        total += dec((stop - start).total_seconds()) / dec((midnight - day_start).total_seconds())
        start = stop
    return total


def validate_inputs(data):
    """Options are reviewed evidence, not fabricated measurement defaults."""
    allowed = {"boundary_verified", "boundary_evidence", "usable_capacity_kwh", "reserve_percent", "charge_limit_kw", "discharge_limit_kw", "grid_import_limit_kw", "export_limit_kw", "charge_efficiency", "discharge_efficiency", "standby_loss_kw", "pv_ac_factor", "hardware_evidence", "loss_evidence", "reserve_evidence", "effective_from", "baseline_import_gbp_kwh", "baseline_standing_gbp_day", "baseline_evidence", "calibration_status", "calibration_uncertainty", "load_boundary", "limits_source", "limit_applicability_evidence", "inverter_ac_limit_kw", "battery_charge_dc_limit_kw", "battery_discharge_dc_limit_kw", "grid_limit_unreported", "sensitivity", "retrospective_calibration", "model_registered_at"}
    if set(data) - allowed:
        raise ValueError("Unknown input field")
    if data.get("boundary_verified") not in (None, False, True):
        raise ValueError("boundary_verified must be boolean")
    if data.get("effective_from"):
        timestamp(data["effective_from"])
    if data.get("calibration_status", "verified") not in ("estimated", "verified"):
        raise ValueError("Calibration must be explicitly estimated or verified")
    if data.get("load_boundary", "ac_end_use") not in ("ac_end_use", "counter_balance"):
        raise ValueError("Unknown load boundary")
    if data.get("limits_source", "settings_history") not in ("settings_history", "reviewed_inputs"):
        raise ValueError("Unknown power limit provenance")
    if data.get("calibration_status") == "estimated" and not data.get("calibration_uncertainty", "").strip():
        raise ValueError("Estimated model requires explicit uncertainty/identifiability disclosure")
    if data.get("load_boundary") == "counter_balance" and dec(data.get("standby_loss_kw", 0)) != 0:
        raise ValueError("Counter-balance model retains AC auxiliary demand; do not subtract battery standby too")
    for key in ("usable_capacity_kwh", "inverter_ac_limit_kw"):
        if key in data and dec(data[key]) <= 0:
            raise ValueError(f"{key} must be positive")
    for key in ("export_limit_kw", "charge_limit_kw", "discharge_limit_kw", "grid_import_limit_kw", "battery_charge_dc_limit_kw", "battery_discharge_dc_limit_kw"):
        if key in data and dec(data[key]) < 0:
            raise ValueError(f"{key} must be nonnegative")
    for key in ("baseline_import_gbp_kwh", "baseline_standing_gbp_day", "standby_loss_kw"):
        if key in data and dec(data[key]) < 0:
            raise ValueError(f"{key} must be nonnegative")
    if "reserve_percent" in data and not 0 <= dec(data["reserve_percent"]) <= 100:
        raise ValueError("Reserve outside 0–100")
    for key in ("charge_efficiency", "discharge_efficiency", "pv_ac_factor"):
        if key in data and not 0 < dec(data[key]) <= 1:
            raise ValueError("Efficiencies must be >0 and <=1")
    return data


def input_blockers(data):
    missing = []
    if data.get("boundary_verified") is not True or not data.get("boundary_evidence"):
        missing.append("Confirm import, export and load use the SAME household boundary excluding Hypervolt; provide meter/wiring evidence")
    for key in ("usable_capacity_kwh", "reserve_percent", "charge_limit_kw", "discharge_limit_kw", "grid_import_limit_kw", "export_limit_kw", "charge_efficiency", "discharge_efficiency", "standby_loss_kw", "pv_ac_factor", "hardware_evidence", "loss_evidence", "reserve_evidence", "effective_from", "baseline_import_gbp_kwh", "baseline_standing_gbp_day", "baseline_evidence"):
        if key == "grid_import_limit_kw" and data.get("grid_limit_unreported") is True:
            continue
        if key not in data or data[key] == "":
            missing.append(f"{'Estimated' if data.get('calibration_status') == 'estimated' else 'Verified'} {key} required")
    if data.get("calibration_status") == "estimated" and not data.get("calibration_uncertainty"):
        missing.append("Estimated calibration uncertainty required")
    return missing


def end_use_interval(interval, inputs):
    """Explicit alternative boundaries; never add losses to an inclusive load.

    Counter-balance hypothesis: Lregister = I-E+PVdc+Ddc-Cdc.
    Remove ONLY modelled conversion losses already in that accounting load;
    AC auxiliary consumption remains load. Battery standby is not inferred
    from the observed 50 W AC idle draw. This hypothesis needs dated evidence
    or explicit acceptance as an assumption, not just a matching cycle ratio.
    """
    if inputs.get("load_boundary", "ac_end_use") == "ac_end_use":
        return dict(interval)
    eta_c, eta_d, pv_factor = (dec(inputs[k]) for k in ("charge_efficiency", "discharge_efficiency", "pv_ac_factor"))
    for key in ("charge", "discharge", "pv", "load"):
        if interval.get(key) is None or dec(interval[key]) < 0:
            raise Pending(f"Counter-balance boundary requires measured {key}")
    # A residual mismatch means these register boundaries are not established.
    residual = dec(interval["import"]) - dec(interval["export"]) + dec(interval["pv"]) + dec(interval["discharge"]) - dec(interval["charge"]) - dec(interval["load"])
    tolerance = Decimal(".02")  # explicit quantization tolerance, kWh per interval
    if abs(residual) > tolerance + Decimal("1e-20"):
        raise Pending("Counter-balance hypothesis fails measured register closure (>0.02 kWh/interval)")
    loss = dec(interval["charge"]) * (1 / eta_c - 1) + dec(interval["discharge"]) * (1 - eta_d) + dec(interval["pv"]) * (1 - pv_factor)
    load = dec(interval["load"]) - loss
    if load < 0:
        raise Pending("Modelled conversion loss exceeds recorded inclusive load; no clamping")
    return {**interval, "load": str(load), "removed_inclusive_conversion_loss_kwh": str(loss)}


def baseline_blockers(data):
    return [f"Known {key} required" for key in ("baseline_import_gbp_kwh", "baseline_standing_gbp_day", "baseline_evidence", "effective_from") if key not in data or data[key] == ""]


def baseline_cost(interval, inputs):
    missing = baseline_blockers(inputs)
    if missing:
        raise Pending("; ".join(missing))
    if interval.get("load") is None or dec(interval["load"]) < 0:
        raise Pending("Missing/nonmonotonic measured Sigenergy household load")
    start, end = timestamp(interval["start"]), timestamp(interval["end"])
    return dec(interval["load"]) * dec(inputs["baseline_import_gbp_kwh"]) + civil_fraction(start, end) * dec(inputs["baseline_standing_gbp_day"])


def measured_costs(interval, versions, inputs):
    """Meter-only accounting: no SOC, PV, battery limits or loss calibration."""
    baseline = baseline_cost(interval, inputs)
    for key in ("import", "export"):
        if interval.get(key) is None or dec(interval[key]) < 0:
            raise Pending(f"Missing/nonmonotonic measured {key}; unknown is not zero")
    start, end = timestamp(interval["start"]), timestamp(interval["end"])
    duration = dec((end - start).total_seconds())
    if duration <= 0:
        raise ValueError("Nonpositive interval")
    actual = Decimal(0)
    identifiers, assumed = set(), False
    points = split_points(versions, start, end)
    for lo, hi in zip(points, points[1:]):
        fraction = dec((hi - lo).total_seconds()) / duration
        imp = tariff_at(versions, "import", lo)
        identifiers.add(imp["id"])
        assumed |= imp.get("assumed", False)
        imported, exported = dec(interval["import"]) * fraction, dec(interval["export"]) * fraction
        revenue = Decimal(0)
        try:
            exp = tariff_at(versions, "export", lo)
        except Pending:
            if exported:
                raise Pending("Nonzero export without known eligibility/rate")
        else:
            if dec(exp["standing"]):
                raise Pending("Unsupported export standing")
            identifiers.add(exp["id"])
            assumed |= exp.get("assumed", False)
            revenue = exported * rate_at(exp, lo)
        actual += imported * rate_at(imp, lo) + civil_fraction(lo, hi) * dec(imp["standing"]) - revenue
    return {"baseline": baseline, "actual": actual, "saving": baseline - actual, "tariff_ids": sorted(identifiers), "assumed_tariff": assumed}


def tariff_at(versions, kind, when):
    candidates = [v for v in versions if v["kind"] == kind and timestamp(v["effective_from"]) <= when]
    if not candidates:
        raise Pending(f"No verified {kind} tariff effective at {iso(when)}")
    result = max(enumerate(candidates), key=lambda pair: (timestamp(pair[1]["effective_from"]), pair[0]))[1]
    if result.get("effective_to") and when >= timestamp(result["effective_to"]):
        raise Pending(f"Latest {kind} tariff ended; no fallback to a superseded rate")
    return result


def rate_at(version, when):
    local = when.astimezone(TZ)
    minute = local.hour * 60 + local.minute
    for lo, hi, price in version["bands"]:
        if lo <= minute < hi:
            return dec(price)
    raise Pending("Tariff bands do not cover local time")


def interval_tariff(versions, kind, start, end):
    """Reject energy allocation across price/version/policy boundaries.

    Check minute boundaries in UTC so repeated/nonexistent DST local times are
    handled without guessing. Collector intervals are bounded to 180 seconds.
    """
    first = tariff_at(versions, kind, start)
    price = rate_at(first, start)
    checks = [end - timedelta(microseconds=1)]
    cursor = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while cursor < end:
        checks.append(cursor)
        cursor += timedelta(minutes=1)
    checks += [timestamp(v[k]) for v in versions if v["kind"] == kind for k in ("effective_from", "effective_to") if v.get(k) and start < timestamp(v[k]) < end]
    for instant in checks:
        v = tariff_at(versions, kind, instant)
        if v["id"] != first["id"] or rate_at(v, instant) != price:
            raise Pending("Interval crosses tariff version/rate boundary; no energy interpolation")
    return price, dec(first["standing"]), first["id"]


def simulate(interval, stored, inputs, *, policy="registered-peak-only-v1"):
    """Average-power interval model, no lookahead and no battery export.

    Charge/discharge efficiencies are separately calibrated AC↔stored values.
    Reserve changes do not create energy: already-below-reserve SOC remains so.
    """
    start, end = timestamp(interval["start"]), timestamp(interval["end"])
    if (end - start).total_seconds() <= 0:
        raise ValueError("Nonpositive interval")
    minute = lambda t: t.astimezone(TZ).hour * 60 + t.astimezone(TZ).minute
    if policy == "registered-peak-only-v1":
        band = lambda m: "charge" if 120 <= m < 300 else "discharge" if 960 <= m < 1140 else "preserve"
    elif policy == "cheap-window-self-consumption-v2":
        band = lambda m: "charge" if 120 <= m < 300 else "discharge"
    else:
        raise ValueError("Unknown fixed simulation policy")
    mode = band(minute(start))
    if mode != band(minute(end - timedelta(microseconds=1))):
        raise Pending("Interval crosses benchmark schedule boundary")
    hours = dec((end - start).total_seconds()) / 3600
    capacity = dec(inputs["usable_capacity_kwh"])
    reserve = capacity * dec(inputs["reserve_percent"]) / 100
    eta_c, eta_d = dec(inputs["charge_efficiency"]), dec(inputs["discharge_efficiency"])
    if not 0 <= stored <= capacity:
        raise Pending("Initial SOC energy outside verified usable capacity")
    standby = min(stored, dec(inputs["standby_loss_kw"]) * hours)
    initial = stored
    stored -= standby
    load, pv = dec(interval["load"]), dec(interval["pv"]) * dec(inputs["pv_ac_factor"])
    if load < 0 or pv < 0:
        raise ValueError("Negative energy")
    direct = min(load, pv)
    demand, surplus = load - direct, pv - direct
    # An invalid grid sentinel supplies NO connection rating. With explicit
    # model disclosure, do not impose a fabricated cap; label feasibility unknown.
    grid_budget = dec(inputs["grid_import_limit_kw"]) * hours if inputs.get("grid_limit_unreported") is not True else load + dec(inputs["charge_limit_kw"]) * hours
    if demand > grid_budget and mode != "discharge":
        raise Pending("House demand exceeds verified grid import limit during preserve/charge")
    charge_budget = dec(inputs["charge_limit_kw"]) * hours
    inverter_budget = dec(inputs["inverter_ac_limit_kw"]) * hours if inputs.get("inverter_ac_limit_kw") is not None else None
    if inverter_budget is not None:
        charge_budget = min(charge_budget, inverter_budget)
        if inputs.get("inverter_output_limit_kw") is not None:
            inverter_budget = min(inverter_budget, dec(inputs["inverter_output_limit_kw"]) * hours)
    if inputs.get("battery_charge_dc_limit_kw") is not None:
        charge_budget = min(charge_budget, dec(inputs["battery_charge_dc_limit_kw"]) * hours / eta_c)
    solar_charge = min(surplus, charge_budget, (capacity - stored) / eta_c)
    grid_charge = min(charge_budget - solar_charge, (capacity - stored) / eta_c - solar_charge, max(Decimal(0), grid_budget - demand)) if mode == "charge" else Decimal(0)
    charge = solar_charge + grid_charge
    stored_after = stored + charge * eta_c
    discharge_budget = dec(inputs["discharge_limit_kw"]) * hours
    if inverter_budget is not None:
        # PV-to-load and battery-to-load share the same inverter AC ceiling.
        # Excess DC PV cannot bypass it merely because export is curtailed.
        if direct > inverter_budget:
            raise Pending("PV exceeds inverter AC budget; subinterval clipping model required")
        discharge_budget = min(discharge_budget, max(Decimal(0), inverter_budget - direct))
    if inputs.get("battery_discharge_dc_limit_kw") is not None:
        discharge_budget = min(discharge_budget, dec(inputs["battery_discharge_dc_limit_kw"]) * hours * eta_d)
    discharge = min(demand, discharge_budget, max(Decimal(0), stored_after - reserve) * eta_d) if mode == "discharge" else Decimal(0)
    export_budget = dec(inputs["export_limit_kw"]) * hours
    if inverter_budget is not None:
        export_budget = min(export_budget, max(Decimal(0), inverter_budget - direct - discharge))
    export = min(surplus - solar_charge, export_budget)
    curtailment = surplus - solar_charge - export
    imported = demand - discharge + grid_charge
    if imported > grid_budget:
        raise Pending("Schedule cannot serve measured house demand within verified grid connection limit")
    loss = charge * (1 - eta_c) + discharge * (1 / eta_d - 1) + standby
    final = stored_after - discharge / eta_d
    # AC energy conservation: sources = loads + export + curtailed + stored Δ + losses.
    error = pv + imported - load - export - curtailment - (final - initial) - loss
    if abs(error) > Decimal("1e-20"):
        raise AssertionError("Energy conservation failure")
    return {"stored": final, "import": imported, "export": export, "charge": charge, "discharge": discharge, "loss": loss, "curtailment": curtailment, "mode": mode}


def split_points(versions, start, end):
    """UTC minute boundaries cover local rate/schedule changes across DST.

    Effective timestamps retain second/microsecond precision. Collection is at
    most 180 seconds, so this is bounded for live intervals.
    """
    points = {start, end}
    cursor = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while cursor < end:
        points.add(cursor)
        cursor += timedelta(minutes=1)
    for version in versions:
        for key in ("effective_from", "effective_to"):
            if version.get(key) and start < timestamp(version[key]) < end:
                points.add(timestamp(version[key]))
    return sorted(points)


def costs(interval, versions, stored, inputs, *, policy="registered-peak-only-v1"):
    """Split cumulative interval energy pro-rata by elapsed seconds.

    This is an explicit short-interval allocation estimate, not a claim of
    measured subinterval supplier data. The same tariff versions drive both
    actual and counterfactual costs. No retrospective optimization.
    """
    for key in ("import", "export", "load", "pv"):
        if key not in interval or interval[key] is None:
            raise Pending(f"Missing measured {key} energy; unknown is not zero")
        if dec(interval[key]) < 0:
            raise Pending(f"Negative/reset {key} energy")
    start, end = timestamp(interval["start"]), timestamp(interval["end"])
    if end <= start:
        raise ValueError("Nonpositive interval")
    duration = dec((end - start).total_seconds())
    baseline = actual = timed = Decimal(0)
    identifiers, assumed = set(), False
    points = split_points(versions, start, end)
    for lo, hi in zip(points, points[1:]):
        fraction = dec((hi-lo).total_seconds()) / duration
        part = {**interval, "start": iso(lo), "end": iso(hi), **{key: str(dec(interval.get(key, 0)) * fraction) for key in KEYS}}
        version = tariff_at(versions, "import", lo)
        imp, standing = rate_at(version, lo), dec(version["standing"])
        identifiers.add(version["id"])
        assumed |= version.get("assumed", False)
        counterfactual = simulate(part, stored, inputs, **({"policy": policy} if policy != "registered-peak-only-v1" else {}))
        try:
            export_version = tariff_at(versions, "export", lo)
        except Pending:
            # Known measured zero earns exactly zero, not assumed eligibility.
            # Counterfactual nonzero export still needs an eligible tariff.
            if dec(part["export"]) != 0 or counterfactual["export"] != 0:
                raise Pending("Export eligibility/rate unknown before first activation")
            exp = Decimal(0)
        else:
            if dec(export_version["standing"]):
                raise Pending("Export standing unsupported")
            exp = rate_at(export_version, lo)
            identifiers.add(export_version["id"])
            assumed |= export_version.get("assumed", False)
        days = civil_fraction(lo, hi)
        baseline += dec(part["load"]) * dec(inputs["baseline_import_gbp_kwh"]) + days * dec(inputs["baseline_standing_gbp_day"])
        actual += dec(part["import"]) * imp + days * standing - dec(part["export"]) * exp
        timed += counterfactual["import"] * imp + days * standing - counterfactual["export"] * exp
        stored = counterfactual["stored"]
    return {"baseline": baseline, "actual": actual, "saving": baseline - actual, "schedule": timed, "stored": stored, "tariff_ids": sorted(identifiers), "assumed_tariff": assumed, "allocation": "Elapsed-time proportional allocation within each short cumulative interval"}
