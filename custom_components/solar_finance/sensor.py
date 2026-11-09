"""Summary and six monetary sensors. Pending values are None, never zero."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FinanceSensor(runtime, entry, None, None)] + [FinanceSensor(runtime, entry, period, column) for period in ("month", "lifetime") for column in ("baseline", "saving", "schedule")])


class FinanceSensor(SensorEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:cash-multiple"

    def __init__(self, runtime, entry, period, column):
        self.runtime, self.period, self.column = runtime, period, column
        suffix = f"{period}_{column}" if period else "summary"
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_name = "Solar finance " + suffix.replace("_", " ")
        if period:
            self._attr_native_unit_of_measurement = "GBP"

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, self.async_write_ha_state))

    @property
    def native_value(self):
        if not self.period:
            return self.runtime.report.get("status", "Collecting data")
        values = self.runtime.report.get("periods", {}).get(self.period, {}).get("values")
        # Decimal preserves unrounded calculations; HA serializes entity values.
        if values is None or values.get(self.column) is None:
            return None
        from decimal import Decimal
        return Decimal(values[self.column])

    @property
    def extra_state_attributes(self):
        if not self.period:
            return {"solar_finance_schema": 1, **self.runtime.report, "last_backup": self.runtime.last_backup}
        covered = self.runtime.report.get("periods", {}).get(self.period, {})
        return {"solar_finance_schema": 1, "period": self.period, "column": self.column, **covered}
