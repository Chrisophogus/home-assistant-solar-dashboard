"""Persistent, read-only meter accounting. Does not command energy devices."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import sqlite3
from time import monotonic
from uuid import uuid4

from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN, ENTITIES, HISTORY_OBSERVED_AT, SIGNAL
from .engine import dec, iso, timestamp
from .ledger import Ledger
from .settings import RESERVE_ENTITY, observation, diagnostic_rows

_LOGGER = logging.getLogger(__name__)
REPORT_INTERVAL_SECONDS = 3600


async def async_setup_entry(hass, entry):
    runtime = Runtime(hass, entry)
    try:
        await hass.async_add_executor_job(runtime.ledger.initialize)
        await hass.async_add_executor_job(runtime.ledger.configure, entry.options.get("inputs", {}), entry.options.get("activations", []), entry.options.get("tariff_edits", []))
        await hass.async_add_executor_job(runtime.ledger.configure_model, entry.options.get("schedule_model"))
        await runtime.refresh()
    except (OSError, ValueError, ArithmeticError, sqlite3.Error) as err:
        raise ConfigEntryNotReady(f"Solar Finance ledger/configuration: {err}") from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    entry.async_on_unload(async_track_time_interval(hass, runtime.refresh, timedelta(seconds=60)))
    entry.async_on_unload(async_track_state_change_event(hass, [RESERVE_ENTITY], runtime.reserve_changed))
    entry.async_on_unload(entry.add_update_listener(async_reload))
    if not runtime.report.get("settings_history_imported"):
        task = hass.async_create_task(runtime.backfill())
        entry.async_on_unload(task.cancel)
    if not hass.services.has_service(DOMAIN, "backup"):
        async def backup(call):
            for active in hass.data.get(DOMAIN, {}).values():
                async with active.lock:
                    active.last_backup = await hass.async_add_executor_job(active.ledger.backup)
                async_dispatcher_send(hass, SIGNAL)
        async_register_admin_service(hass, DOMAIN, "backup", backup)
        async def backfill(call):
            for active in hass.data.get(DOMAIN, {}).values():
                await active.backfill()
        async_register_admin_service(hass, DOMAIN, "backfill", backfill)
    return True


async def async_reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR]):
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "backup")
            hass.services.async_remove(DOMAIN, "backfill")
        return True
    return False


class Runtime:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.ledger = Ledger(hass.config.path("solar_finance_data", "ledger.sqlite3"))
        self.sources = entry.options.get("sources", entry.data.get("sources", ENTITIES))
        self.report = {}
        self.last_backup = None
        self.error = None
        self.lock = asyncio.Lock()
        self.session_id = uuid4().hex
        self.backfill_lock = asyncio.Lock()
        self.backfill_error = None
        self._next_report_at = 0.0

    @callback
    def sample(self):
        instant = datetime.now(timezone.utc)
        sample = {"timestamp": iso(instant), "collection_session": self.session_id, "energy": {}, "source_timestamps": {}, "sources": self.sources, "errors": [], "soc": None, "mode": "Unknown (unreported/stale)", "reserve_percent": None}
        reported = []
        for key, entity in self.sources.items():
            state = self.hass.states.get(entity)
            if state is None:
                sample["errors"].append(f"Missing {entity}")
                continue
            report_time = state.last_reported
            sample["source_timestamps"][key] = iso(report_time)
            age = (instant - report_time).total_seconds()
            if not 0 <= age <= 90 or state.state in ("unavailable", "unknown"):
                if key != "mode":
                    sample["errors"].append(f"Unavailable/stale {entity} (age {age:.0f}s)")
                continue
            if key == "mode":
                sample["mode"] = state.state
                continue
            reported.append(report_time)
            try:
                value = dec(state.state)
                unit = state.attributes.get("unit_of_measurement")
                if key == "soc":
                    if unit != "%" or not 0 <= value <= 100:
                        raise ValueError("SOC unit/range")
                    sample["soc"] = str(value)
                else:
                    factor = {"Wh": "0.001", "kWh": "1", "MWh": "1000"}.get(unit)
                    if factor is None or value < 0:
                        raise ValueError("Energy unit/range")
                    sample["energy"][key] = str(value * dec(factor))
            except (ValueError, ArithmeticError):
                sample["errors"].append(f"Invalid value/unit for {entity}")
        if reported and (max(reported) - min(reported)).total_seconds() > 30:
            sample["errors"].append("Meter/SOC source timestamp skew exceeds 30s")
        if set(sample["energy"]) != set(ENTITIES) - {"soc", "mode"}:
            sample["errors"].append("Incomplete matching cumulative counters")
        sample["key_errors"] = {key: [error for error in sample["errors"] if entity in error] for key, entity in self.sources.items()}
        reserve = self.hass.states.get("number.sigen_plant_ess_backup_state_of_charge")
        if reserve is not None and reserve.state not in ("unknown", "unavailable"):
            try:
                value = dec(reserve.state)
                if reserve.attributes.get("unit_of_measurement") == "%" and 0 <= value <= 100:
                    sample["reserve_percent"] = str(value)
            except (ValueError, ArithmeticError):
                pass
        observed = getattr(self, "observed_reserve", None)
        if observed is not None:
            sample["reserve_percent"] = observed["value"] if observed["status"] == "observed" else None
        return sample

    async def reserve_changed(self, event):
        state = event.data.get("new_state")
        # Persist event timestamp, not last_changed (which may precede this run).
        row = observation("reserve_percent", state.state if state else None,
                          iso(event.time_fired), "HA live state-change event", state.attributes.get("unit_of_measurement") if state else None,
                          entity_id=RESERVE_ENTITY)
        async with self.lock:
            await self.hass.async_add_executor_job(self.ledger.observe_settings, [row])
        await self.refresh()

    async def settings_snapshot(self):
        instant = iso(datetime.now(timezone.utc))
        state = self.hass.states.get(RESERVE_ENTITY)
        rows = [observation("reserve_percent", state.state if state else None, instant,
                            "HA current state observation", state.attributes.get("unit_of_measurement") if state else None,
                            entity_id=RESERVE_ENTITY)]
        entries = self.hass.config_entries.async_entries("sigen")
        if len(entries) == 1:
            try:
                from custom_components.sigen.diagnostics import async_get_config_entry_diagnostics
                report = await async_get_config_entry_diagnostics(self.hass, entries[0])
                diagnostics = diagnostic_rows(report, instant)
                if any(row["key"] == "reserve_percent" for row in diagnostics):
                    rows = [row for row in rows if row["key"] != "reserve_percent"]
                rows.extend(diagnostics)
            except Exception:
                # Do not log diagnostics: the full report may contain sensitive options.
                rows = diagnostic_rows({}, instant)
        else:
            rows = diagnostic_rows({}, instant)
        self.observed_reserve = next((row for row in rows if row["key"] == "reserve_percent"), None)
        await self.hass.async_add_executor_job(self.ledger.observe_settings, rows)

    async def refresh(self, _when=None):
        async with self.lock:
            try:
                await self.settings_snapshot()
                sample = self.sample()
                await self.hass.async_add_executor_job(self.ledger.collect, sample)
                # Collection stays frequent; only the expensive report is hourly.
                if monotonic() >= self._next_report_at:
                    # Limit failed attempts too, rather than retrying every minute.
                    self._next_report_at = monotonic() + REPORT_INTERVAL_SECONDS
                    self.report = await self.hass.async_add_executor_job(self.ledger.report)
                    self.report["calculated_through_sample_at"] = self.report.get("last_sample_at")
                    self.report["calculated_at"] = iso(datetime.now(timezone.utc))
                # A collector heartbeat is independent of hourly accounting coverage.
                # Publish only after collection succeeds; never extend money coverage.
                self.report["last_sample_at"] = sample["timestamp"]
                self.report["collector_current"] = True
                self.report["collector_sample_errors"] = sample.get("errors", [])
                if self.backfill_error:
                    self.report["backfill_error"] = self.backfill_error
                self.error = None
            except Exception as err:  # Keep pending, never silently expose cached money.
                _LOGGER.exception("Solar Finance collection/report failed")
                self.error = str(err)
                self.report = {"status": "Collecting data", "blockers": [f"Collector error: {err}"], "periods": {}}
        async_dispatcher_send(self.hass, SIGNAL)

    async def backfill(self):
        """Read existing Recorder data, append only to our own persistent ledger."""
        from .recorder_import import fetch_history
        async with self.backfill_lock:
            try:
                start, end = timestamp(HISTORY_OBSERVED_AT), datetime.now(timezone.utc)
                while start < end:
                    stop = min(end, start + timedelta(days=31))
                    rows = await fetch_history(self.hass, self.sources, start, stop)
                    async with self.lock:
                        await self.hass.async_add_executor_job(self.ledger.import_history, rows, self.sources, start, stop)
                    start = stop
                async with self.lock:
                    # Explicit history corrections recalculate once and reset the hour.
                    self._next_report_at = monotonic() + REPORT_INTERVAL_SECONDS
                    self.report = await self.hass.async_add_executor_job(self.ledger.report)
                self.backfill_error = None
            except Exception as err:
                _LOGGER.exception("Solar Finance read-only Recorder backfill failed")
                self.backfill_error = str(err)
                self.report["backfill_error"] = str(err)
        async_dispatcher_send(self.hass, SIGNAL)
