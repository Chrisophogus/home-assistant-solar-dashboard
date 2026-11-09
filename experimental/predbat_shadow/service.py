"""Narrow local snapshot broker + Solcast acquisition + hourly isolated planning.

No HA, Supervisor, inverter, MQTT or vendor-control client is present here.
Only Solcast forecast GET is outbound. No household telemetry is uploaded.
"""
from datetime import datetime,timezone,timedelta
from http.server import BaseHTTPRequestHandler,HTTPServer
import hmac
import json
import os
from pathlib import Path
import resource
from archive import Archive,stamp
from acquire import acquire
from forecast_inputs import pv_minutes
from matched_replay import MatchedReplay
from worker_hour import plan_hour


class Controller:
    def __init__(self,data,options,runtime,launcher):
        self.data=Path(data);self.options=options;self.runtime=runtime;self.launcher=launcher
        self.archive=Archive(self.data/'shadow.sqlite3');self.replay=MatchedReplay(self.archive)
        # Stop on storage exhaustion, never silently delete issue-time evidence.
        self.archive.db.execute('PRAGMA max_page_count=262144')
        self.status={'status':'Pending','blockers':['Not yet configured/evaluated']}
        self.snapshot=None
        row=self.archive.db.execute("SELECT hash FROM artifacts WHERE kind='ha-corrected-snapshot' ORDER BY received DESC LIMIT 1").fetchone()
        if row:self.snapshot=row[0]

    def ingest(self,body,received):
        if body.get('schema')!=1 or 'actual_soc' in body:raise ValueError('Snapshot schema or forbidden actual SOC')
        if not isinstance(body.get('prepared_parts'),list) or len(body['prepared_parts'])>10000:raise ValueError('Bounded corrected parts required')
        if not body.get('tariffs') or not body.get('physics') or not body.get('load_forecast'):raise ValueError('Missing corrected inputs')
        # Caller can only submit this fixed envelope, not URLs, credentials or paths.
        allowed={'schema','prepared_parts','tariffs','physics','load_forecast','demand_forecast_received_at','physics_received_at'}
        if set(body)-allowed:raise ValueError('Unrecognised snapshot fields')
        self.snapshot=self.archive.put('ha-corrected-snapshot',body,received)
        return self.snapshot

    def tick(self,now=None):
        now=now or datetime.now(timezone.utc)
        if not self.options.get('enabled'):return
        try:
            if not self.options.get('api_key') or not self.options.get('site_ids'):raise ValueError('Solcast key/sites pending')
            # Each UTC slot gets at most one primary attempt per site. Failed
            # calls remain counted; automatic minute retries are prohibited.
            day=now.astimezone(timezone.utc).date().isoformat();slot=f'{now.hour//6*6:02}'
            aliases=['plane-'+str(i+1) for i in range(len(self.options['site_ids']))]
            done={x[0] for x in self.archive.db.execute('SELECT site FROM attempts WHERE day=? AND slot=? AND retry=0',(day,slot))}
            if not done:acquire(self.archive,self.options['api_key'],self.options['site_ids'])
            # Start at :05 so issue-time snapshots/forecasts received around the
            # hour are available. No optimiser runs every five minutes.
            if not 5<=now.minute<15 or not self.snapshot:return
            decision=now.replace(minute=5,second=0,microsecond=0)
            exists=self.archive.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='planner_hours'").fetchone()
            if exists and self.archive.db.execute('SELECT 1 FROM planner_hours WHERE hour=?',(now.strftime('%Y-%m-%dT%H'),)).fetchone():return
            snap=self.archive.available(self.snapshot,decision.isoformat())
            if (decision-stamp(snap['received'])).total_seconds()>2*3600:raise ValueError('HA snapshot stale')
            body=snap['body'];cfg=body['physics']
            if stamp(body['physics_received_at'])>decision or stamp(body['demand_forecast_received_at'])>decision:raise ValueError('Hindsight forecast/settings')
            if not self.archive.db.execute('SELECT 1 FROM replay_state').fetchone():
                seed=self.options.get('initial_virtual_state')
                if not seed:raise ValueError('Explicit once-only initial stock mapping pending')
                self.replay.seed(seed['at'],seed['lower_kwh'],seed['upper_kwh'],seed['evidence'])
            plans=[x[0] for x in self.archive.db.execute("SELECT hash FROM artifacts WHERE kind='predbat-plan' ORDER BY received DESC LIMIT 168")]
            at=stamp(self.replay.state()['at'])
            parts=[x for x in body['prepared_parts'] if at<=stamp(x['part']['start']) and stamp(x['part']['end'])<=decision]
            if parts:self.replay.advance(parts,body['tariffs'],plans)
            self.replay.align_unknown_gap(decision.isoformat(),cfg)
            forecasts=[]
            for alias in aliases:
                found=None
                for row in self.archive.db.execute("SELECT hash FROM artifacts WHERE kind='solcast-forecast' AND received<=? ORDER BY received DESC",(decision.isoformat(),)):
                    candidate=self.archive.available(row[0],decision.isoformat())
                    if candidate['body']['site_alias']==alias:found=row[0];break
                if not found:raise ValueError('Received Solcast forecast missing')
                forecasts.append(found)
            pv=pv_minutes(self.archive,forecasts,decision.isoformat(),1440)
            load=body['load_forecast']
            # Load forecasts carry explicit timestamps; no future actual PV/load
            # may substitute for issue-time forecast values.
            if load['start']!=decision.isoformat() or len(load['kwh_per_minute'])!=1440:raise ValueError('Demand forecast does not cover this decision')
            from action_replay import engine as e
            import_rates=[];export_rates=[]
            for i in range(1440):
                t=decision+timedelta(minutes=i)
                import_rates.append(float(e.rate_at(e.tariff_at(body['tariffs'],'import',t),t)*100))
                export_rates.append(float(e.rate_at(e.tariff_at(body['tariffs'],'export',t),t)*100))
            packet={'schema':1,'decision_at':decision.isoformat(),'physics':cfg,'load_kwh_per_minute':load['kwh_per_minute'],'pv_kwh_per_minute':pv,'import_p_per_kwh':import_rates,'export_p_per_kwh':export_rates,'provenance':{'snapshot':{'received_at':snap['received']},'demand':{'received_at':body['demand_forecast_received_at']}}}
            plan_hour(self.archive,self.replay,packet,self.runtime,self.launcher)
            self.status={'status':'Collecting forward shadow coverage','periods':{k:self.replay.view(start,decision.isoformat()) for k,start in [('lifetime',self.options['initial_virtual_state']['at']),('month',decision.replace(day=1,hour=0,minute=0).isoformat())]}}
        except Exception as exc:
            # No payload/URL/key output. Exception type only in exposed status.
            self.status={'status':'Pending','blockers':['Shadow inputs/job incomplete: '+type(exc).__name__]}
        (self.data/'report.json').write_text(json.dumps(self.status,sort_keys=True))


def main():
    os.umask(0o077);os.nice(15)
    resource.setrlimit(resource.RLIMIT_AS,(768*1024**2,)*2)
    data=Path('/data');options=json.loads((data/'options.json').read_text())
    if options.get('enabled') and len(options.get('ingress_key',''))<32:raise RuntimeError('Scoped snapshot key must be at least 32 characters')
    ctl=Controller(data,options,'/runtime','/app/launch_plan.py')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def authorised(self):return bool(options.get('ingress_key')) and hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+options['ingress_key'])
        def respond(self,code,value):
            raw=json.dumps(value).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if not self.authorised():self.respond(401,{'error':'unauthorised'});return
            if self.path!='/report':self.respond(404,{});return
            self.respond(200,ctl.status)
        def do_POST(self):
            if not self.authorised():self.respond(401,{'error':'unauthorised'});return
            if self.path!='/snapshot':self.respond(404,{});return
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=16*1024**2:raise ValueError('Size limit')
                self.connection.settimeout(10)
                key=ctl.ingest(json.loads(self.rfile.read(n)),datetime.now(timezone.utc).isoformat())
                self.respond(202,{'snapshot_hash':key})
            except Exception:self.respond(400,{'error':'Invalid bounded snapshot'})
    # Repository prototype defaults local-only; not an approved deployed endpoint.
    server=HTTPServer((options.get('bind_host','localhost'),8787),Handler);server.timeout=5
    while True:
        server.handle_request();ctl.tick()

if __name__=='__main__':main()
