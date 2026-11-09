"""One-shot hourly planner coordinator. No HA client and no forecast HTTP."""
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta


def plan_hour(archive, replay, packet, runtime, launcher):
    # Persist at-most-once reservation BEFORE spawning, so restart/failure cannot
    # cause minute retries or multiple expensive jobs in the same UTC hour.
    archive.db.execute('CREATE TABLE IF NOT EXISTS planner_hours(hour TEXT PRIMARY KEY, status TEXT, plan_hash TEXT)')
    now=datetime.now(timezone.utc)
    decision=datetime.fromisoformat(packet['decision_at'].replace('Z','+00:00'))
    if decision.tzinfo is None or not 0 <= (now-decision).total_seconds() <= 600:
        raise ValueError('Snapshot decision must be from the last ten minutes')
    hour=now.strftime('%Y-%m-%dT%H')
    archive.db.execute('INSERT INTO planner_hours VALUES(?,?,NULL)',(hour,'reserved-before-spawn'))
    runtime=Path(runtime)
    state=replay.state()
    if datetime.fromisoformat(state['at'].replace('Z','+00:00'))!=decision:raise ValueError('Virtual replay must reach decision explicitly; no frozen gap or SOC re-anchor')
    # Never take initial stock from the incoming HA snapshot or actual SOC.
    packet={**packet,'virtual_stored_kwh':state['predbat'][0]}
    if state['predbat'][0]!=state['predbat'][1]:packet['stock_basis']='lower endpoint of carried virtual interval, not measured SOC'
    destination=runtime/'input/request.json'
    destination.write_text(json.dumps(packet,allow_nan=False))
    destination.chmod(0o444)
    output=runtime/'work/plan.json'
    if output.exists():output.unlink()  # Only prior bounded job output, not archive.
    try:
        result=subprocess.run([sys.executable,'-B',str(launcher),str(runtime)],env={'PATH':'/usr/bin:/bin'},capture_output=True,timeout=95)
        if result.returncode:raise RuntimeError('Isolated planner failed; no numeric publication')
        plan=json.loads(output.read_text())
        if not plan.get('plan_valid'):raise ValueError('Planner returned invalid output')
        received=datetime.now(timezone.utc)
        plan.update(valid_until=(decision+timedelta(hours=24)).isoformat(),valid_from=(received.replace(second=0,microsecond=0)+timedelta(minutes=1)).isoformat(),input_hash=archive.put('planner-input',packet,now.isoformat()),stock_basis=packet.get('stock_basis','carried virtual point state'))
        key=archive.put('predbat-plan',plan,received.isoformat())
        archive.db.execute('UPDATE planner_hours SET status=?,plan_hash=? WHERE hour=?',('valid',key,hour))
        return key
    except Exception:
        archive.db.execute('UPDATE planner_hours SET status=? WHERE hour=?',('failed-no-minute-retry',hour))
        raise
