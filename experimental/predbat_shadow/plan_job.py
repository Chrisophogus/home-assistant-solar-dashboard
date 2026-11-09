"""Generic immutable-input job; executed ONLY by the restricted launcher."""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

os.environ.clear()
os.environ.update(HOME='/work',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
sys.dont_write_bytecode=True
sys.path.insert(0,'/bundle/upstream/apps/predbat')
sys.frozen=True  # Disable import-time update, not OS security.

from annual import create_headless_predbat, reset_sample_state, apply_hardware, _apply_rates

def blocked(*a,**k):
    raise RuntimeError('Live fetch/control/publish is forbidden')


def run(packet):
    if packet.get('schema') != 1 or packet.get('actual_soc') is not None:
        raise ValueError('Unsupported snapshot/actual SOC injection')
    def aware(s):
        t=datetime.fromisoformat(s.replace('Z','+00:00'))
        if t.tzinfo is None:raise ValueError('Timezone required')
        return t.astimezone(timezone.utc)
    decision=aware(packet['decision_at'])
    for source in packet['provenance'].values():
        if aware(source['received_at'])>decision:raise ValueError('Hindsight snapshot')
    required=('load_kwh_per_minute','pv_kwh_per_minute','import_p_per_kwh','export_p_per_kwh')
    arrays={name:packet[name] for name in required}
    n=len(arrays[required[0]])
    if n not in (1440,2880) or any(len(v)!=n for v in arrays.values()):raise ValueError('24/48h matching minute coverage required')
    import math
    if any(not isinstance(x,(float,int)) or not math.isfinite(x) for v in arrays.values() for x in v):raise ValueError('Nonfinite/non-numeric forecast')
    if any(x<0 for name in required[:2] for x in arrays[name]):raise ValueError('Negative energy')
    cfg=packet['physics']
    capacity=float(cfg['usable_capacity_kwh']);stock=float(packet['virtual_stored_kwh'])
    if not 0<=stock<=capacity:raise ValueError('Virtual stock outside capacity')
    def log(*args,**kwargs):
        with Path('/work/planner-debug.log').open('a') as handle:handle.write(' '.join(map(str,args))+'\n')
    p=create_headless_predbat('/work','Europe/London',log)
    for name in ('initialize','execute_plan','call_service','call_service_wrapper','fetch_inverter_data','fetch_sensor_data','dashboard_item','publish_html_plan','set_state_wrapper'):
        setattr(p,name,blocked)
    p.ha_interface.call_service=blocked
    reset_sample_state(p)
    midnight=decision.replace(hour=0,minute=0,second=0,microsecond=0)
    # Require minute-aligned decision. Real eligibility begins after completed
    # plan receipt; coordinator may not backdate execution to this decision.
    if decision.second or decision.microsecond or decision.minute%5:raise ValueError('Predbat five-minute decision boundary required')
    offset=int((decision-midnight).total_seconds()/60)
    p.midnight_utc=midnight;p.now_utc=decision;p.minutes_now=offset
    p.forecast_plan_hours=n/60;p.forecast_minutes=n;p.forecast_days=n/1440;p.end_record=n
    p.num_cars=0;p.load_minutes={};p.load_minutes_age=0
    p.load_forecast={offset:0.0};running=0.0
    for i,x in enumerate(arrays['load_kwh_per_minute']):running+=x;p.load_forecast[offset+i+1]=running
    _apply_rates(p,{offset+i:x for i,x in enumerate(arrays['import_p_per_kwh'])},{offset+i:x for i,x in enumerate(arrays['export_p_per_kwh'])})
    apply_hardware(p,{'size_kwh':capacity,'inverter_kw':float(cfg['inverter_ac_limit_kw']),'charge_rate_kw':float(cfg['charge_limit_kw']),'discharge_rate_kw':float(cfg['discharge_limit_kw']),'hybrid':True},[],float(cfg['export_limit_kw']))
    p.soc_kw=stock  # MUST come after upstream reset/hardware helpers.
    p.reserve=capacity*float(cfg['reserve_percent'])/100;p.reserve_percent=float(cfg['reserve_percent'])
    p.inverter_loss=1.0;p.battery_loss=float(cfg['charge_efficiency']);p.battery_loss_discharge=float(cfg['discharge_efficiency'])
    p.pv_forecast_minute={offset+i:x*float(cfg['pv_ac_factor']) for i,x in enumerate(arrays['pv_kwh_per_minute'])}
    # No invented probability bands. Identical sensitivity inputs when only a
    # nominal forecast is supplied, explicitly labelled in result.
    p.pv_forecast_minute10=dict(p.pv_forecast_minute);p.pv_forecast_minute90=dict(p.pv_forecast_minute)
    p.calculate_best_export=False;p.set_export_window=False;p.update_pending=False
    p.calculate_plan(recompute=True,debug_mode=False,publish=False)
    if not p.plan_valid:raise RuntimeError('Invalid plan; no numeric publication')
    return {'schema':1,'planner_commit':'950a948366d453e825c8d883f6fff1e18c355a6e','decision_at':decision.isoformat(),'finished_at':datetime.now(timezone.utc).isoformat(),'midnight_utc':midnight.isoformat(),'initial_virtual_kwh':str(packet['virtual_stored_kwh']),'charge_windows':p.charge_window_best,'charge_limits_kwh':p.charge_limit_best,'export_windows':[],'export_limits':[],'nominal_only':True,'plan_valid':True,'cash_not_evaluated':True,'physics_mapping_status':'Common AC aggregate efficiencies; dated/DC-limit equivalence requires integration gate'}

if __name__=='__main__':
    result=run(json.loads(Path('/input/request.json').read_text()))
    Path('/work/plan.json').write_text(json.dumps(result,sort_keys=True))
