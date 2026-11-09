# SOURCE EXPORT: dates/rates below are illustrative, not this installation history.
"""Read-only financial accounting; deliberately no energy-control services."""

DOMAIN = "solar_finance"
SIGNAL = "solar_finance_updated"
ENTITIES = {
    "import": "sensor.sigen_plant_total_imported_energy",
    "export": "sensor.sigen_plant_total_exported_energy",
    "load": "sensor.sigen_plant_total_load_consumption",
    "pv": "sensor.sigen_plant_total_pv_generation",
    "charge": "sensor.sigen_plant_total_charged_energy_of_the_ess",
    "discharge": "sensor.sigen_plant_total_discharged_energy_of_the_ess",
    "soc": "sensor.sigen_plant_battery_state_of_charge",
    "mode": "sensor.sigen_plant_ems_work_mode",
}
COMMISSIONED_AT = "2026-08-01T00:00:00+01:00"
HISTORY_OBSERVED_AT = "2026-10-26T00:00:00Z"
# Registration is the time this prospective policy was committed to disk,
# not commissioning, AI activation, installation, or the first HA sample.
POLICY_REGISTERED_AT = "2026-10-27T00:00:00Z"
POLICY = {
    "id": "flux-house-only-v1",
    "registered_at": POLICY_REGISTERED_AT,
    "timezone": "Europe/London",
    "description": "02–05 charge towards 100%; solar serves house then charges; preserve until 16; 16–19 discharge to house down to recorded reserve; preserve after 19; no deliberate battery export.",
    "comparison": "Forward fixed-policy counterfactual, not an AI comparison",
}
TEMPLATES = {
    "flexible_import": {"kind": "import", "bands": [[0, 1440, "0.25"]], "standing": "0.40"},
    "flux_import": {"kind": "import", "bands": [[0, 120, "0.30"], [120, 300, "0.10"], [300, 960, "0.30"], [960, 1140, "0.40"], [1140, 1440, "0.30"]], "standing": "0.45"},
    "prime_export": {"kind": "export", "bands": [[0, 960, "0.09"], [960, 1140, "0.16"], [1140, 1440, "0.09"]], "standing": "0"},
    "flux_export_planned": {"kind": "export", "bands": [[0, 120, "0.08"], [120, 300, "0.03"], [300, 960, "0.08"], [960, 1140, "0.20"], [1140, 1440, "0.08"]], "standing": "0"},
}
ASSUMED_FLUX_START = "2026-09-01T00:00:00+01:00"
INITIAL_TARIFFS = [
    {**TEMPLATES["flexible_import"], "record_id": "import-flexible-before-flux", "template": "flexible_import", "effective_from": COMMISSIONED_AT, "effective_to": None, "assumed": True, "evidence": "EXAMPLE ONLY: synthetic tariff history; configure your own effective dates"},
    {**TEMPLATES["flux_import"], "record_id": "import-flux-assumed", "template": "flux_import", "effective_from": ASSUMED_FLUX_START, "effective_to": None, "assumed": True, "evidence": "EXAMPLE ONLY: synthetic tariff history, not billing evidence"},
    {**TEMPLATES["prime_export"], "record_id": "export-prime-assumed", "template": "prime_export", "effective_from": ASSUMED_FLUX_START, "effective_to": None, "assumed": True, "evidence": "EXAMPLE ONLY: synthetic tariff history, not billing evidence"},
]
