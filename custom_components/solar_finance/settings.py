"""Timestamped settings evidence, never inferred from a power plateau or rating.

An observation is valid from its actual observed timestamp, not commissioning.
Unknown, unavailable, missing and invalid are different recorded states. A later
invalid observation fences an earlier value; no fallback to saved options.
"""

from bisect import bisect_left, bisect_right

from .engine import dec, iso, timestamp

RESERVE_ENTITY = "number.sigen_plant_ess_backup_state_of_charge"
LIMITS = {
    "charge_limit_kw": ("plant_ess_max_charging_limit", 40032),
    "discharge_limit_kw": ("plant_ess_max_discharging_limit", 40034),
    "export_limit_kw": ("plant_grid_point_maximum_export_limitation", 40038),
    "grid_import_limit_kw": ("plant_grid_maximum_import_limitation", 40040),
}
CAPABILITIES = {
    "pcs_import_limit_kw": ("plant_pcs_maximum_import_limitation", 40044),
    "pcs_export_limit_kw": ("plant_pcs_maximum_export_limitation", 40042),
    "available_charge_kw": ("plant_ess_available_max_charging_power", 30047),
    "available_discharge_kw": ("plant_ess_available_max_discharging_power", 30049),
    "rated_charge_kw": ("plant_ess_rated_charging_power", 30068),
    "rated_discharge_kw": ("plant_ess_rated_discharging_power", 30070),
    "plant_available_active_kw": ("plant_available_max_active_power", 30039),
}


def observation(key, state, when, source, unit=None, **evidence):
    status = str(state) if state in ("unknown", "unavailable") else "missing" if state is None else "observed"
    value = None
    if status == "observed":
        try:
            value = dec(state)
            if key == "reserve_percent":
                if unit != "%" or not 0 <= value <= 100:
                    raise ValueError("Reserve unit/range")
            else:
                if unit not in ("kW", "W"):
                    raise ValueError("Limit unit")
                value *= dec(".001") if unit == "W" else 1
                # U32 invalid sentinel after installed Sigen gain=1000.
                if value == dec("4294967.295"):
                    return {"key": key, "timestamp": iso(timestamp(when)), "status": "not_valid",
                            "value": None, "raw_value": "4294967.295", "source": source,
                            "reason": "U32 all-ones sentinel 0xFFFFFFFF, not a physical limit", **evidence}
                if not 0 <= value < dec("4294967.295"):
                    raise ValueError("Invalid register sentinel/range")
            value = str(value)
        except (ValueError, ArithmeticError, TypeError):
            status, value = "invalid", None
    return {"key": key, "timestamp": iso(timestamp(when)), "status": status,
            "value": value, "source": source,
            **({"raw_value": str(state), "raw_unit": unit} if status == "observed" else {}), **evidence}


def at(rows, key, when):
    if isinstance(rows, Timeline):
        return rows.at(key, when)
    candidates = [r for r in rows if r["key"] == key and timestamp(r["timestamp"]) <= when]
    if not candidates:
        return {"key": key, "status": "unknown", "value": None, "reason": "No earlier observation"}
    latest = max(candidates, key=lambda r: timestamp(r["timestamp"]))
    tied = [r for r in candidates if timestamp(r["timestamp"]) == timestamp(latest["timestamp"])]
    # Recorder can serialize the same measurement as 25 or 25.0. Compare
    # numeric meaning, never discard the distinct immutable raw observations.
    if len({(r["status"], dec(r["value"]) if r["status"] == "observed" else r["value"]) for r in tied}) != 1:
        return {"key": key, "status": "conflicting", "value": None, "timestamp": latest["timestamp"]}
    return latest


class Timeline:
    """One index per report; logarithmic lookup rather than intervals × history."""

    def __init__(self, rows):
        self.rows, self.times = {}, {}
        self._resolved = {}
        for row in sorted(rows, key=lambda r: timestamp(r["timestamp"])):
            self.rows.setdefault(row["key"], []).append(row)
        for key, values in self.rows.items():
            self.times[key] = [timestamp(r["timestamp"]) for r in values]

    def at(self, key, when):
        times, rows = self.times.get(key, []), self.rows.get(key, [])
        pos = bisect_right(times, when) - 1
        cache_key = (key, pos)
        if cache_key not in self._resolved:
            if pos < 0:
                self._resolved[cache_key] = at([], key, when)
            else:
                first = bisect_left(times, times[pos])
                self._resolved[cache_key] = at(rows[first:pos + 1], key, when)
        return dict(self._resolved[cache_key])

    def between(self, start, end):
        points = set()
        for times in self.times.values():
            points.update(times[bisect_right(times, start):bisect_left(times, end)])
        return points


def reserve_rows(rows):
    return [observation("reserve_percent", row.get("state"), row["timestamp"],
                        "HA Recorder state observation", row.get("unit"), entity_id=RESERVE_ENTITY)
            for row in rows if row["entity_id"] == RESERVE_ENTITY]


def diagnostic_rows(report, when):
    """Accept only the public Sigen diagnostics shape, already-scaled kW values.

    This is a cache observation, not a fresh Modbus read or proof of which
    remote-EMS limits apply in AI mode. No report entry/options/credentials saved.
    """
    healthy = report.get("coordinator", {}).get("last_update_success") is True
    plant = report.get("data", {}).get("plant", {}) if healthy else {}
    rows = [observation(key, plant.get(name) if healthy else "unavailable", when,
                        "Sigen public diagnostics coordinator cache", "kW", register=register,
                        applicability="Configured remote-EMS ceiling; effective mode/PCS/thermal ceiling unverified")
            for key, (name, register) in {**LIMITS, **CAPABILITIES}.items()]
    if "plant_backup_soc" in plant or not healthy:
        rows.append(observation("reserve_percent", plant.get("plant_backup_soc") if healthy else "unavailable", when,
                                "Sigen public diagnostics coordinator cache", "%", register=40046))
    inverters = report.get("data", {}).get("inverters", {}) if healthy else {}
    for name, data in inverters.items():
        for field in ("inverter_max_absorption_power", "inverter_max_active_power", "inverter_max_active_power_adjustment_value"):
            rows.append(observation(field, data.get(field), when, "Sigen public diagnostics coordinator cache", "kW", device=name))
    return rows
