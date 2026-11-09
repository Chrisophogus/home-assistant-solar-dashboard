"""Supported read-only HA Recorder queries; never open its database file."""

from datetime import timedelta
from functools import partial

from .engine import iso
from .history import HEARTBEAT


async def fetch_history(hass, sources, start, end):
    from homeassistant.components.recorder.history import get_significant_states
    from homeassistant.helpers.recorder import get_instance
    from .settings import RESERVE_ENTITY

    if not 0 < (end - start).total_seconds() <= 31 * 86400:
        raise ValueError("Backfill request must be positive and bounded to 31 days")
    entities = list(dict.fromkeys([*sources.values(), HEARTBEAT, RESERVE_ENTITY]))
    recorder = get_instance(hass)
    rows, seen = [], set()
    cursor = start
    while cursor < end:
        stop = min(end, cursor + timedelta(hours=6))
        query = partial(get_significant_states, hass, max(start, cursor - timedelta(seconds=1)), stop,
                        entity_ids=entities, include_start_time_state=False,
                        significant_changes_only=False, minimal_response=False, no_attributes=False)
        history = await recorder.async_add_executor_job(query)
        for entity, states in history.items():
            for state in states:
                when = state.last_updated
                if when < start or when > end:
                    continue
                unit = state.attributes.get("unit_of_measurement")
                row = {"entity_id": entity, "timestamp": iso(when), "state": state.state,
                       "unit": str(unit) if unit is not None else None,
                       "recorded_last_changed": iso(state.last_changed),
                       "last_reported_provenance": "Not reconstructed: Recorder returns state changes, not original unchanged reports",
                       "source": "homeassistant.components.recorder.history.get_significant_states (read_only=True internally)"}
                identity = (entity, row["timestamp"], row["state"], row["unit"])
                if identity in seen:
                    continue
                seen.add(identity)
                rows.append(row)
                if len(rows) > 250000:
                    raise ValueError("Bounded backfill exceeds 250000 recorded rows; no partial batch imported")
        cursor = stop
    return rows
