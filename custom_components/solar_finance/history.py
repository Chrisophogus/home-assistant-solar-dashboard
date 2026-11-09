"""Pure Recorder state-history reconstruction, with explicit evidence limits.

Recorder stores state CHANGES, not every unchanged report. A held counter is
used only while the same Sigenergy plant has continuous measured-power history.
Explicit unknown/unavailable states, heartbeat gaps, duplicate-value restart
fences and any counter decrease break coverage. This is a state-history meter
estimate, not reconstructed last_reported telemetry or supplier settlement data.
"""

from bisect import bisect_left, bisect_right
from datetime import timedelta
import hashlib
import json

from .engine import KEYS, dec, iso, timestamp

VERSION = "recorder-state-history-v1"
HEARTBEAT = "sensor.sigen_plant_consumed_power"


def event_value(row, key):
    if row.get("state") in (None, "unknown", "unavailable"):
        raise ValueError("Unavailable recorded state")
    if key == "mode":
        return row["state"]
    value = dec(row["state"])
    if key == "soc":
        if row.get("unit") != "%" or not 0 <= value <= 100:
            raise ValueError("SOC unit/range")
        return value
    units = {"Wh": ".001", "kWh": "1", "MWh": "1000"} if key in KEYS else {"W": ".001", "kW": "1", "MW": "1000"}
    if row.get("unit") not in units or value < 0:
        raise ValueError("Recorded unit/range unavailable")
    return value * dec(units[row["unit"]])


def reconstruct(rows, sources, start, end):
    """Fixed UTC minute bins; original state observations retained separately.

    Quantized, asynchronous counter states are held between recorded changes,
    only within an observed plant heartbeat segment. No interpolated energy,
    fabricated zero, or inferred operation mode. Tariff sub-bins are separately
    labelled elapsed-time allocation estimates in the accounting engine.
    """
    mapping = {**sources, "heartbeat": HEARTBEAT}
    streams, times = {}, {}
    fences = set()
    for key, entity in mapping.items():
        stream = sorted((r for r in rows if r["entity_id"] == entity), key=lambda r: timestamp(r["timestamp"]))
        # Identical API replays must not become invented restart fences.
        unique = {}
        for row in stream:
            when = timestamp(row["timestamp"])
            if when in unique and (unique[when]["state"], unique[when].get("unit")) != (row["state"], row.get("unit")):
                raise ValueError("Conflicting Recorder states at one entity/timestamp")
            unique[when] = row
        stream = list(unique.values())
        streams[key], times[key] = stream, list(unique)
        if key in KEYS:
            for prev, current in zip(stream, stream[1:]):
                if prev["state"] == current["state"]:
                    fences.add(timestamp(current["timestamp"]))
    heartbeat = streams["heartbeat"]
    heartbeat_times = times["heartbeat"]
    fence_times = sorted(fences)
    # A counter held from before a telemetry outage cannot attribute the
    # outage's eventual cumulative delta to a later apparently healthy minute.
    breaks = []
    for index, row in enumerate(heartbeat):
        try:
            event_value(row, "heartbeat")
        except (ValueError, ArithmeticError):
            breaks.append(heartbeat_times[index])
        if index and (heartbeat_times[index] - heartbeat_times[index - 1]).total_seconds() > 180:
            breaks.append(heartbeat_times[index])
    break_times = sorted(set(breaks) | fences)
    cursor = start.replace(second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(minutes=1)
    result = []
    while cursor + timedelta(minutes=1) <= end:
        stop = cursor + timedelta(minutes=1)
        common, errors, values, support = [], {}, {}, {}
        left = bisect_right(heartbeat_times, cursor) - 1
        right = bisect_left(heartbeat_times, stop)
        if left < 0 or right >= len(heartbeat):
            common.append("No bracketing plant heartbeat observations")
        else:
            observed = heartbeat[left:right + 1]
            observed_times = heartbeat_times[left:right + 1]
            if any((b - a).total_seconds() > 180 for a, b in zip(observed_times, observed_times[1:])):
                common.append("Recorded plant heartbeat gap exceeds 180s")
            try:
                for row in observed:
                    event_value(row, "heartbeat")
            except (ValueError, ArithmeticError):
                common.append("Unavailable recorded plant heartbeat")
        if bisect_right(fence_times, stop) > bisect_left(fence_times, cursor):
            common.append("Repeated cumulative state/restart fence; interval not bridged")
        boundary = bisect_right(break_times, cursor) - 1
        segment_start = break_times[boundary] if boundary >= 0 else start
        for key in KEYS:
            stream, ts = streams[key], times[key]
            a, b = bisect_right(ts, cursor) - 1, bisect_right(ts, stop) - 1
            issues = list(common)
            if a < 0 or b < 0:
                issues.append(f"No recorded {key} starting state")
            elif ts[a] < segment_start:
                issues.append(f"{key} starting counter predates telemetry gap/restart; no bridging")
            else:
                try:
                    observations = stream[a:b + 1]
                    quantities = [event_value(row, key) for row in observations]
                    if any(y < x for x, y in zip(quantities, quantities[1:])):
                        raise ValueError("Counter reset/decrease inside interval")
                    values[key] = str(quantities[-1] - quantities[0])
                    support[key] = {"start": stream[a]["timestamp"], "end": stream[b]["timestamp"], "held_unchanged": a == b}
                except (ValueError, ArithmeticError) as err:
                    issues.append(f"{key}: {err}")
            errors[key] = issues
        optional = {}
        for key in ("soc", "mode"):
            ts, stream = times[key], streams[key]
            pos = bisect_right(ts, cursor) - 1
            try:
                optional[key] = str(event_value(stream[pos], key)) if pos >= 0 else None
                optional[key + "_observed_at"] = stream[pos]["timestamp"] if pos >= 0 else None
            except (ValueError, ArithmeticError):
                optional[key] = None
        payload = {"start": iso(cursor), "end": iso(stop), **values, "key_errors": errors,
                   "source": VERSION, "source_entities": sources, "observation_support": support,
                   "initial_soc": optional.get("soc"), "soc_observed_at": optional.get("soc_observed_at"),
                   "actual_mode": optional.get("mode") or "Unknown", "mode_observed_at": optional.get("mode_observed_at"),
                   "reserve_percent": None, "provenance": "HA Recorder read-only history API; held state changes gated by plant power heartbeat; no independent per-counter last_reported reconstruction"}
        payload["errors"] = sorted({e for group in errors.values() for e in group})
        payload["id"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        result.append(payload)
        cursor = stop
    return result
