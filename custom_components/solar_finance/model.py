"""ILLUSTRATIVE example model; not calibrated or verified for any installation."""
from .const import POLICY
ESTIMATED_MODEL = {
    "effective_from": POLICY["registered_at"],
    "model_registered_at": POLICY["registered_at"],
    "retrospective_calibration": True,
    "calibration_status": "estimated",
    "calibration_uncertainty": "Synthetic example only. Replace with dated independent evidence before using monetary results. Not a measured capacity, efficiency, confidence interval or AI benefit.",
    "usable_capacity_kwh": "10", "charge_efficiency": "0.95",
    "discharge_efficiency": "0.90", "pv_ac_factor": "1", "standby_loss_kw": "0",
    "load_boundary": "counter_balance", "limits_source": "reviewed_inputs",
    "inverter_ac_limit_kw": "3", "charge_limit_kw": "3", "discharge_limit_kw": "3",
    "battery_charge_dc_limit_kw": "3", "battery_discharge_dc_limit_kw": "3",
    "export_limit_kw": "3", "grid_limit_unreported": True,
    "hardware_evidence": "EXAMPLE ONLY: no installation evidence included",
    "loss_evidence": "EXAMPLE ONLY: no household observations or fitted coefficients included",
    "sensitivity": [{"pv_ac_factor": "0.90"}, {"pv_ac_factor": "0.95"}, {"pv_ac_factor": "1"}],
}
