"""Practical UI setup and evidence options. Missing inputs stay missing."""

import json
from datetime import datetime, timezone

import probatio as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN, ENTITIES, TEMPLATES
from .engine import iso, local_timestamp, normalize_rate, timestamp, validate_inputs, TZ
from .ledger import normalize_tariff


def text(multiline=False):
    return selector.TextSelector(selector.TextSelectorConfig(multiline=multiline))


def source_schema(values):
    return vol.Schema({vol.Required(key, default=values.get(key, entity)): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")) for key, entity in ENTITIES.items()})


class SolarFinanceFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Solar Finance", data={"sources": user_input})
        return self.async_show_form(step_id="user", data_schema=source_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SolarFinanceOptions()


class SolarFinanceOptions(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["live_settings", "schedule_model", "evidence", "tariff_history", "activate_export", "sources"])

    async def async_step_live_settings(self, user_input=None):
        if user_input is not None:
            return await self.async_step_init()
        from .settings import RESERVE_ENTITY
        state = self.hass.states.get(RESERVE_ENTITY)
        runtime = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        summary = runtime.report.get("live_settings", {}) if runtime else {}
        observed = summary.get("reserve_percent")
        return self.async_show_form(step_id="live_settings", data_schema=vol.Schema({}),
                                    description_placeholders={"reserve": (observed.get("value") if observed.get("status") == "observed" else observed.get("status")) if observed else state.state if state else "missing",
                                                              "settings": json.dumps(summary, indent=2)})

    async def async_step_schedule_model(self, user_input=None):
        from .model import ESTIMATED_MODEL
        runtime = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        versions = await self.hass.async_add_executor_job(runtime.ledger.versions, "schedule_model") if runtime else []
        selected = versions[-1] if versions else ESTIMATED_MODEL
        raw = json.dumps({k: v for k, v in selected.items() if k not in ("id", "ledger_registered_at")}, indent=2)
        errors = {}
        if user_input is not None:
            raw = user_input["model"]
            try:
                model = json.loads(raw)
                validate_inputs(model)
                if not model.get("effective_from") or not model.get("hardware_evidence") or not model.get("loss_evidence"):
                    raise ValueError("Dated model evidence required")
                model["model_registered_at"] = iso(datetime.now(timezone.utc))
            except (ValueError, TypeError, ArithmeticError):
                errors["base"] = "invalid_evidence"
            else:
                return self.save("schedule_model", model)
        return self.async_show_form(step_id="schedule_model", data_schema=vol.Schema({vol.Required("model", default=raw): text(True)}), errors=errors)

    def save(self, key, value):
        return self.async_create_entry(title="", data={**self.config_entry.options, key: value})

    async def async_step_evidence(self, user_input=None):
        errors = {}
        values = self.config_entry.options.get("inputs", {})
        if user_input is not None:
            clean = {k: v for k, v in user_input.items() if v != ""}
            try:
                validate_inputs(clean)
            except (ValueError, ArithmeticError, TypeError):
                errors["base"] = "invalid_evidence"
                values = user_input
            else:
                return self.save("inputs", clean)
        defaults = {"usable_capacity_kwh": "10", "baseline_import_gbp_kwh": "0.25", "baseline_standing_gbp_day": "0.40", "baseline_evidence": "EXAMPLE ONLY: replace with your verified baseline tariff"}
        fields = {vol.Required("boundary_verified", default=values.get("boundary_verified", False)): selector.BooleanSelector()}
        for key in ("effective_from", "baseline_import_gbp_kwh", "baseline_standing_gbp_day", "baseline_evidence", "boundary_evidence", "usable_capacity_kwh", "reserve_percent", "reserve_evidence", "charge_limit_kw", "discharge_limit_kw", "grid_import_limit_kw", "export_limit_kw", "hardware_evidence", "charge_efficiency", "discharge_efficiency", "standby_loss_kw", "pv_ac_factor", "loss_evidence"):
            fields[vol.Optional(key, default=values.get(key, defaults.get(key, "")))] = text(key.endswith("evidence"))
        return self.async_show_form(step_id="evidence", data_schema=vol.Schema(fields), errors=errors)

    async def async_step_tariffs(self, user_input=None):
        errors = {}
        raw = json.dumps(self.config_entry.options.get("activations", []), indent=2)
        if user_input is not None:
            raw = user_input["activations"]
            try:
                activations = json.loads(raw)
                if not isinstance(activations, list):
                    raise ValueError("Expected array")
                for item in activations:
                    if not isinstance(item, dict) or set(item) - {"template", "effective_from", "effective_to", "evidence"}:
                        raise ValueError("Invalid activation")
                    if item.get("template") not in TEMPLATES or not item.get("evidence", "").strip():
                        raise ValueError("Template/evidence required")
                    start = timestamp(item["effective_from"])
                    if item.get("effective_to") and timestamp(item["effective_to"]) <= start:
                        raise ValueError("Invalid end")
                runtime = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
                if runtime:
                    # Existing activations cannot be overwritten by options.
                    existing = await self.hass.async_add_executor_job(runtime.ledger.versions, "tariff")
                    for item in activations:
                        for old in existing:
                            if old["kind"] == TEMPLATES[item["template"]]["kind"] and timestamp(old["effective_from"]) == timestamp(item["effective_from"]):
                                if old["template"] != item["template"] or old["evidence"] != item["evidence"] or (timestamp(old["effective_to"]) if old.get("effective_to") else None) != (timestamp(item["effective_to"]) if item.get("effective_to") else None):
                                    raise ValueError("Conflicting immutable activation")
            except (ValueError, KeyError, TypeError):
                errors["base"] = "invalid_tariffs"
            else:
                return self.save("activations", activations)
        return self.async_show_form(step_id="tariffs", data_schema=vol.Schema({vol.Required("activations", default=raw): text(True)}), errors=errors)

    async def async_step_sources(self, user_input=None):
        values = self.config_entry.options.get("sources", self.config_entry.data["sources"])
        if user_input is not None:
            return self.save("sources", user_input)
        return self.async_show_form(step_id="sources", data_schema=source_schema(values))

    async def _tariff_records(self):
        runtime = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if not runtime:
            return []
        return await self.hass.async_add_executor_job(runtime.ledger.tariff_versions)

    async def async_step_tariff_history(self, user_input=None):
        records = await self._tariff_records()
        if not records:
            return self.async_abort(reason="backend_unavailable")
        if user_input is not None:
            self._tariff_target = next((v for v in records if v["id"] == user_input["record"]), None)
            if self._tariff_target:
                return await self.async_step_edit_tariff()
        choices = [{"value": v["id"], "label": f'{v["kind"]}: {v.get("template", "custom")} from {v["effective_from"]}' + (" (assumed)" if v.get("assumed") else "")} for v in records]
        return self.async_show_form(step_id="tariff_history", data_schema=vol.Schema({vol.Required("record"): selector.SelectSelector(selector.SelectSelectorConfig(options=choices))}))

    async def async_step_edit_tariff(self, user_input=None):
        return await self._tariff_form("edit_tariff", self._tariff_target, user_input)

    async def async_step_activate_export(self, user_input=None):
        # This template alone never activates a tariff. Date AND time and
        # confirmation evidence are required on explicit form submission.
        target = {**TEMPLATES["flux_export_planned"], "template": "flux_export_planned", "record_id": "export-flux", "assumed": False, "evidence": ""}
        return await self._tariff_form("activate_export", target, user_input)

    async def _tariff_form(self, step, target, user_input):
        errors = {}
        defaults = {}
        if target.get("effective_from"):
            local = timestamp(target["effective_from"]).astimezone(TZ)
            defaults = {"date": local.date().isoformat(), "time": local.strftime("%H:%M:%S")}
        rate = lambda minute: next(str(v) for lo, hi, v in target["bands"] if lo <= minute < hi)
        values = {**defaults, "off_peak_rate": rate(120), "standard_rate": rate(360), "peak_rate": rate(960), "standing_rate": target["standing"], "currency_unit": "GBP", "assumed": target.get("assumed", False), "evidence": target.get("evidence", "")}
        if user_input is not None:
            values.update(user_input)
            try:
                effective = local_timestamp(user_input["date"], user_input["time"])
                unit = "GBP" if user_input["currency_unit"] == "GBP" else "pence"
                price = lambda name: str(normalize_rate(user_input[name], unit + "_per_kWh"))
                standing = str(normalize_rate(user_input["standing_rate"], unit + "_per_day"))
                off, standard, peak = price("off_peak_rate"), price("standard_rate"), price("peak_rate")
                if target["template"] == "flexible_import":
                    bands = [[0, 1440, standard]]
                else:
                    bands = [[0,120,standard],[120,300,off],[300,960,standard],[960,1140,peak],[1140,1440,standard]]
                payload = {k: v for k, v in target.items() if k not in ("id", "ledger_registered_at")}
                payload.update(bands=bands, standing=standing, effective_from=effective, assumed=user_input["assumed"], evidence=user_input["evidence"], revision_registered_at=iso(datetime.now(timezone.utc)))
                if step == "edit_tariff":
                    payload["replaces"] = target["id"]
                else:
                    payload["effective_to"] = None
                payload = normalize_tariff(payload)
                current = await self._tariff_records()
                if step == "edit_tariff" and not any(v["id"] == target["id"] for v in current):
                    raise ValueError("Stale correction target")
                if any(v["kind"] == payload["kind"] and v["effective_from"] == payload["effective_from"] and v["id"] != payload.get("replaces") for v in current):
                    raise ValueError("Duplicate effective date; edit existing tariff")
            except (ValueError, KeyError, TypeError, ArithmeticError):
                errors["base"] = "invalid_tariffs"
            else:
                return self.save("tariff_edits", [*self.config_entry.options.get("tariff_edits", []), payload])
        fields = {}
        for key, select in (("date", selector.DateSelector()), ("time", selector.TimeSelector())):
            marker = vol.Required(key, default=values[key]) if key in values else vol.Required(key)
            fields[marker] = select
        for key in ("off_peak_rate", "standard_rate", "peak_rate", "standing_rate"):
            fields[vol.Required(key, default=values[key])] = text()
        fields[vol.Required("currency_unit", default=values["currency_unit"])] = selector.SelectSelector(selector.SelectSelectorConfig(options=["GBP", "pence"]))
        fields[vol.Required("assumed", default=values["assumed"])] = selector.BooleanSelector()
        fields[vol.Required("evidence", default=values["evidence"])] = text(True)
        return self.async_show_form(step_id=step, data_schema=vol.Schema(fields), errors=errors)
