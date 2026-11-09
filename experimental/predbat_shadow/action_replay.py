"""Staged action extension of the reviewed pure finance kernel, no live imports.

Uses exactly the reviewed conservation/power/loss maths. Only schedule selection
and the grid-charge target change. Rejects battery export/freeze semantics until
explicitly implemented and tested. This is not yet an HA/Ledger runtime adapter.
"""
import hashlib
import inspect
from pathlib import Path
import importlib.util

FROZEN_HASH = '48a525ef9091a3d489d5e90428c92d26ea404ddc073c3fb607e8af2964223fe3'
path = Path(__file__).resolve().parents[2] / 'custom_components/solar_finance/engine.py'
if hashlib.sha256(path.read_bytes()).hexdigest() != FROZEN_HASH:
    raise RuntimeError('Common finance kernel hash drift')
spec = importlib.util.spec_from_file_location('shadow_finance_engine', path)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
source = inspect.getsource(engine.simulate)
start = source.index('    minute = lambda')
end = source.index('    hours = dec')
source = source[:start] + '    mode = policy\n    if mode not in ("charge", "discharge", "preserve"):\n        raise ValueError("Unsupported plan action")\n' + source[end:]
source = source.replace('def simulate(', 'def action_simulate(')
needle = '(capacity - stored) / eta_c - solar_charge'
assert source.count(needle) == 1
source = source.replace(needle, 'max(Decimal(0), min(capacity, dec(inputs["plan_target_kwh"])) - stored) / eta_c - solar_charge')
# Solar charging may already exceed the grid target; never allow negative charge.
source = source.replace('grid_charge = min(', 'grid_charge = max(Decimal(0), min(')
source = source.replace('if mode == "charge" else Decimal(0)', 'if mode == "charge" else Decimal(0))', 1)
namespace = dict(vars(engine))
exec(compile(source, str(path) + ':explicit-plan-action', 'exec'), namespace)
simulate = namespace['action_simulate']


def replay(parts, stored, plan, physics):
    """Parts must already share corrected finance coverage and dated physics.

    No actual SOC is accepted. Caller must split at every window/tariff/settings
    boundary. Unknown gaps must be handled by an interval-state coordinator first.
    """
    if plan.get('export_windows') or plan.get('export_limits'):
        raise ValueError('Battery export plan semantics not supported')
    rows = []
    for part in parts:
        start, end = engine.timestamp(part['start']), engine.timestamp(part['end'])
        midnight = engine.timestamp(plan['midnight_utc'])
        lo = (start-midnight).total_seconds()/60
        hi = (end-midnight).total_seconds()/60
        mode, target = 'discharge', '0'
        for w, limit in zip(plan['charge_windows'], plan['charge_limits_kwh'], strict=True):
            if lo < w['end'] and hi > w['start']:
                if not w['start'] <= lo < hi <= w['end']:
                    raise ValueError('Split part at plan boundary first')
                if engine.dec(limit) <= 0:
                    continue
                mode, target = 'charge', str(limit)
        out = simulate(part, engine.dec(stored), {**physics, 'plan_target_kwh':target}, policy=mode)
        rows.append({'start':part['start'],'end':part['end'],'stored_before':str(stored),'stored_after':str(out['stored']),'action':mode})
        stored = out['stored']
    return str(stored), rows
