"""Persistent matched-window replay using the reviewed finance kernel.

No prediction output or actual SOC is ever assigned as virtual inventory.
Input parts must come from corrected Ledger preparation, not Recorder proxies.
"""
import json
from decimal import Decimal as D
from hashlib import sha256
from itertools import product
from action_replay import engine as e, simulate
from fourth_card import reduce_rows
from time import monotonic


class MatchedReplay:
    def __init__(self, archive):
        self.archive=archive
        self.db=archive.db
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS replay_state(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS evaluated(start TEXT PRIMARY KEY, end TEXT, body TEXT NOT NULL);
        ''')

    def seed(self, at, lower, upper, evidence):
        if not evidence or not 0 <= D(lower) <= D(upper):raise ValueError('Explicit initial stock evidence required')
        body={'at':at,'predbat':[str(lower),str(upper)],'timed':[str(lower),str(upper)],'seed_evidence':evidence,'capacity':None}
        # INSERT, not replace/upsert: restart/replan cannot seed again.
        self.db.execute('INSERT INTO replay_state VALUES(1,?)',(json.dumps(body),))

    def state(self):
        row=self.db.execute('SELECT body FROM replay_state WHERE id=1').fetchone()
        if not row:raise ValueError('Explicit seed missing')
        return json.loads(row[0])

    def align_unknown_gap(self,end,cfg):
        """Explicitly propagate missing energy to a decision boundary; no cash."""
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state=self.state();lo=e.timestamp(state['at']);hi=e.timestamp(end)
            if hi<lo:raise ValueError('Cannot move virtual state backwards')
            if hi>lo:
                hours=D(str((hi-lo).total_seconds()))/3600
                for policy in ('predbat','timed'):state[policy]=self.widen(state[policy],hours,cfg)
                state['last_unknown_gap']={'start':state['at'],'end':end,'cash':'Omitted identically'}
                state['at']=end
                self.db.execute('UPDATE replay_state SET body=? WHERE id=1',(json.dumps(state),))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');raise

    @staticmethod
    def widen(stock, hours, cfg):
        cap=D(cfg['usable_capacity_kwh'])
        charge=min(D(cfg['charge_limit_kw'])*D(cfg['charge_efficiency']),D(cfg['battery_charge_dc_limit_kw']))*hours
        discharge=(min(D(cfg['discharge_limit_kw'])/D(cfg['discharge_efficiency']),D(cfg['battery_discharge_dc_limit_kw']))+D(cfg['standby_loss_kw']))*hours
        return [str(max(D(0),D(stock[0])-discharge)),str(min(cap,D(stock[1])+charge))]

    @staticmethod
    def action(plan, part):
        if plan.get('export_windows') or plan.get('export_limits'):raise ValueError('Unsupported battery export plan')
        lo=e.timestamp(part['start']);hi=e.timestamp(part['end']);origin=e.timestamp(plan['midnight_utc'])
        start=D(str((lo-origin).total_seconds()))/60;end=D(str((hi-origin).total_seconds()))/60
        mode,target='discharge','0'
        for window,limit in zip(plan['charge_windows'],plan['charge_limits_kwh'],strict=True):
            a,b=map(D,map(str,(window['start'],window['end'])))
            if start<b and end>a:
                if not a<=start<end<=b:raise ValueError('Unsplit issued-plan boundary')
                if D(str(limit))>0:mode,target='charge',str(limit)
        return mode,target

    def advance(self, parts, tariffs, plan_keys):
        if not parts or len(parts)>10000:raise ValueError('Bounded nonempty preparation batch required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            started=monotonic();state=self.state();newrows=[]
            for item in parts:
                if monotonic()-started>30:raise TimeoutError('Replay deadline; transaction rolled back')
                part,cfg=item['part'],item['physical'];lo=e.timestamp(part['start']);hi=e.timestamp(part['end']);previous=e.timestamp(state['at'])
                if hi<=lo or lo<previous:raise ValueError('Overlap/duplicate/backwards replay')
                cap=cfg['usable_capacity_kwh']
                if state['capacity'] is not None and D(cap)!=D(state['capacity']):raise ValueError('Capacity change requires conserved mapping')
                state['capacity']=cap
                for policy in ('predbat','timed'):
                    if not 0<=D(state[policy][0])<=D(state[policy][1])<=D(cap):raise ValueError('Invalid stored inventory')
                    if lo>previous:state[policy]=self.widen(state[policy],D(str((lo-previous).total_seconds()))/3600,cfg)
                plans=[]
                for key in plan_keys:
                    try:a=self.archive.available(key,part['start'])
                    except ValueError:continue
                    if a['kind']!='predbat-plan' or not a['body'].get('plan_valid'):raise ValueError('Invalid archived planner artifact')
                    if e.timestamp(a['body'].get('valid_from',a['received']))>lo:continue
                    if e.timestamp(a['body']['valid_until'])<hi:continue
                    plans.append((e.timestamp(a['received']),key,a['body'],a['received']))
                if not plans:
                    # Unknown/no-plan energy is NOT frozen and cash is omitted
                    # identically. Do not assign measured SOC to restart later.
                    hours=D(str((hi-lo).total_seconds()))/3600
                    for policy in ('predbat','timed'):state[policy]=self.widen(state[policy],hours,cfg)
                    state['at']=part['end'];continue
                _,key,plan,received=max(plans,key=lambda x:x[0])
                mode,target=self.action(plan,part)
                physical=[cfg] if not item.get('uncertain') else [{**cfg,'reserve_percent':str(r),'charge_limit_kw':str(c),'discharge_limit_kw':str(d)} for r,c,d in product((0,100),(D(0),D(cfg['charge_limit_kw'])),(D(0),D(cfg['discharge_limit_kw'])))]
                ip,standing,_=e.interval_tariff(tariffs,'import',lo,hi);ep,_,_=e.interval_tariff(tariffs,'export',lo,hi)
                days=e.civil_fraction(lo,hi);ranges={}
                for policy in ('predbat','timed'):
                    outcomes=[]
                    for stock in set(state[policy]):
                        for physical_cfg in physical:
                            if policy=='predbat':out=simulate(part,D(stock),{**physical_cfg,'plan_target_kwh':target},policy=mode)
                            else:out=e.simulate(part,D(stock),physical_cfg,policy='cheap-window-self-consumption-v2')
                            outcomes.append(out)
                    state[policy]=[str(min(x['stored'] for x in outcomes)),str(max(x['stored'] for x in outcomes))]
                    cash=[x['import']*ip-x['export']*ep+days*standing for x in outcomes]
                    ranges[policy]=(str(min(cash)),str(max(cash)))
                row={'start':part['start'],'end':part['end'],'plan_hash':key,'plan_available_at':received,'same_physics_and_tariffs':True,'matched_coverage':True,
                     'baseline':str(D(part['load'])*D(cfg['baseline_import_gbp_kwh'])+days*D(cfg['baseline_standing_gbp_day'])),
                     'actual':str(D(part['import'])*ip-D(part['export'])*ep+days*standing),
                     'timed_cost_lower':ranges['timed'][0],'timed_cost_upper':ranges['timed'][1],
                     'predbat_cost_lower':ranges['predbat'][0],'predbat_cost_upper':ranges['predbat'][1],
                     'virtual_terminal_stock':state['predbat'],'timed_terminal_stock':state['timed'],
                     'raw_part_hash':sha256(json.dumps(item,sort_keys=True).encode()).hexdigest()}
                self.db.execute('INSERT INTO evaluated VALUES(?,?,?)',(part['start'],part['end'],json.dumps(row)))
                state['at']=part['end'];newrows.append(row)
            self.db.execute('UPDATE replay_state SET body=? WHERE id=1',(json.dumps(state),))
            self.db.execute('COMMIT')
            return newrows
        except BaseException:
            self.db.execute('ROLLBACK');raise

    def view(self,start,end):
        # Reduce the SAME persistent trajectory; never initialise a month.
        rows=[json.loads(x[0]) for x in self.db.execute('SELECT body FROM evaluated ORDER BY start')]
        rows=[r for r in rows if e.timestamp(start)<=e.timestamp(r['start']) and e.timestamp(r['end'])<=e.timestamp(end)]
        return reduce_rows(rows)
