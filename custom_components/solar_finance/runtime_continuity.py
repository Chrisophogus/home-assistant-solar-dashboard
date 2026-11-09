"""Hourly-only derived view from retained Ledger evidence, never a raw rewrite."""
from bisect import bisect_right
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal as D
import hashlib
import json
from time import monotonic
from . import engine as e
from .continuity import recover_cumulative, UnrecoveredGap
from .production_adapter import replay_once, window_view, cell_to_engine
from .settings import LIMITS


def physical_at(cfg, settings, when, *, allow_unknown=False):
    p = dict(cfg)
    uncertain = False
    reserve = settings.at('reserve_percent', when)
    if reserve['status'] == 'observed':
        p['reserve_percent'] = reserve['value']
    elif allow_unknown:
        p['reserve_percent'] = '0'
        uncertain = True
    else:
        raise e.Pending('Unobserved dated reserve')
    for key in (*LIMITS, 'pcs_import_limit_kw', 'pcs_export_limit_kw'):
        obs = settings.at(key, when)
        if obs.get('timestamp') and obs['status'] not in ('observed', 'not_valid'):
            if not allow_unknown:
                raise e.Pending('Unobserved configured limit: '+key)
            uncertain = True
        if obs['status'] != 'observed':
            continue
        if key in ('charge_limit_kw', 'discharge_limit_kw') and not cfg.get('limit_applicability_evidence'):
            continue
        cap = e.dec(obs['value'])
        if key in ('pcs_import_limit_kw', 'pcs_export_limit_kw'):
            target = 'charge_limit_kw' if key == 'pcs_import_limit_kw' else 'discharge_limit_kw'
            p[target] = str(min(e.dec(p[target]), cap))
            if key == 'pcs_export_limit_kw':
                p['inverter_output_limit_kw'] = str(cap)
            continue
        if key == 'charge_limit_kw':
            cap /= e.dec(p['charge_efficiency'])
        elif key == 'discharge_limit_kw':
            cap *= e.dec(p['discharge_efficiency'])
        p[key] = str(min(e.dec(p[key]), cap)) if key in p else str(cap)
        if key == 'grid_import_limit_kw':
            p['grid_limit_unreported'] = False
    for key,target in (('available_charge_kw','battery_charge_dc_limit_kw'),('available_discharge_kw','battery_discharge_dc_limit_kw'),('inverter_max_absorption_power','charge_limit_kw'),('inverter_max_active_power','inverter_output_limit_kw'),('inverter_max_active_power_adjustment_value','inverter_output_limit_kw'),('plant_available_active_kw','inverter_output_limit_kw')):
        obs = settings.at(key, when)
        if obs['status'] == 'conflicting':
            raise e.Pending('Conflicting inverter capabilities')
        if obs['status'] == 'observed' and 0 <= (when-e.timestamp(obs['timestamp'])).total_seconds() <= 180:
            p[target] = str(min(e.dec(p.get(target,p['inverter_ac_limit_kw'])), e.dec(obs['value'])))
    return p, uncertain


def prepare(ledger, comparison, tariffs, settings, inputs, models, current, deadline):
    """Ledger-derived eligible history/live rows plus validated replacements.

    Parent comparison supplies original effective input/model/source gates only;
    its simulated cash and SOC resets are NOT used by this view.
    """
    if not comparison:
        raise e.Pending('No eligible retained history/live meter intervals')
    rows = [r[0] for r in comparison]
    starts = [e.timestamp(r['start']) for r in rows]
    ends = [e.timestamp(r['end']) for r in rows]
    with ledger.connect() as db:
        samples = {t:{**json.loads(p),'normalised_unit':'kWh'} for t,p in db.execute(
            'SELECT timestamp,payload FROM samples WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp',
            (rows[0]['start'], e.iso(current)))}
    ordered = sorted(samples.values(),key=lambda s:e.timestamp(s['timestamp']))
    stamps = [e.timestamp(s['timestamp']) for s in ordered]
    def check_time():
        if monotonic()>deadline:
            raise TimeoutError('Corrected-view hourly preparation deadline')
    def recover(lo,hi):
        check_time()
        if lo not in samples or hi not in samples:
            raise UnrecoveredGap('No exact retained fresh endpoint')
        start,end=e.timestamp(lo),e.timestamp(hi)
        if any(v.get('effective_from') and start<e.timestamp(v['effective_from'])<end for v in (*inputs,*models)):
            raise UnrecoveredGap('Dated input/model change')
        e.interval_tariff(tariffs,'import',start,end)
        e.interval_tariff(tariffs,'export',start,end)
        mode=lambda t:2<=t.astimezone(e.TZ).hour<5
        if mode(start)!=mode(end-timedelta(microseconds=1)):
            raise UnrecoveredGap('Policy boundary')
        pos=max(0,bisect_right(starts,start)-1)
        cfg=comparison[pos][1]
        p,uncertain=physical_at(cfg,settings,start,allow_unknown=True)
        for key in settings.rows:
            observed=[settings.at(key,t) for t in (start,*sorted(settings.between(start,end)),end)]
            values={str(v['value']) for v in observed if v['status']=='observed'}
            if key=='reserve_percent' and len(values)>1:
                raise UnrecoveredGap('Observed reserve change')
            if any(v['status'] not in ('observed','not_valid') for v in observed):
                uncertain=True
            if len(values)>1:
                uncertain=True
        intermediate=ordered[bisect_right(stamps,start):bisect_right(stamps,end)-1]
        raw=recover_cumulative(samples[lo],samples[hi],intermediate,True,True)
        hours=D(str((end-start).total_seconds()))/3600
        for key,rating in (('charge','8.8'),('discharge','9.6'),('pv','3.39'),('export','5.5'),('load','50'),('import','50')):
            if D(raw[key])>D(rating)*hours+D('.02'):
                raise UnrecoveredGap('Implausible endpoint delta: '+key)
        part=e.end_use_interval(raw,p)
        # When controls were unavailable, use installed scenario ceilings rather
        # than claiming held observed controls. Every corner is conditional.
        if uncertain:
            p={**p,'charge_limit_kw':cfg['charge_limit_kw'],'discharge_limit_kw':cfg['discharge_limit_kw']}
        provenance={'kind':'retained cumulative endpoints; nonoverlapping replacement',
                    'start':lo,'end':hi,'endpoint_hashes':[hashlib.sha256(json.dumps(samples[t],sort_keys=True).encode()).hexdigest() for t in (lo,hi)],
                    'collector_session_changed':raw['collector_session_changed'],
                    'intermediate_missing_counters':raw['intermediate_missing_counters'],
                    'identity_limit':'Retained source mapping, monotonicity, dated installed model and collector-normalised kWh; no historical hardware serial proof',
                    'time_allocation':'Measured totals, unknown within-interval dispatch; average-power assumption',
                    'settings_uncertain':uncertain}
        return {'part':part,'physical':p,'reconstructed':True,'uncertain':uncertain,'provenance':provenance}
    gaps=[(a,b) for a,b in zip(rows,rows[1:]) if a['end']!=b['start']]
    replacements=[];unrecovered=[]
    for left,right in gaps:
        check_time();lo,hi=left['end'],right['start'];li=bisect_right(ends,e.timestamp(lo))-1;ri=bisect_right(starts,e.timestamp(hi))-1
        candidates=[(lo,hi)]
        for width in range(1,6):
            a=rows[max(0,li-width+1)]['start'];b=rows[min(len(rows)-1,ri+width-1)]['end']
            candidates.extend(((a,hi),(lo,b),(a,b)))
        errors=[]
        for a,b in dict.fromkeys(candidates):
            try:
                recovered = recover(a,b)
                recovered['provenance'].update(original_gap=[lo,hi], widened=(a,b)!=(lo,hi))
                replacements.append(recovered);break
            except (e.Pending,UnrecoveredGap,KeyError,ValueError) as err:
                errors.append(str(err))
        else:
            unrecovered.append({'start':lo,'end':hi,'reasons':sorted(set(errors))})
    merged=[]
    for item in sorted(replacements,key=lambda r:e.timestamp(r['part']['start'])):
        if merged and e.timestamp(item['part']['start'])<=e.timestamp(merged[-1]['part']['end']):
            old=merged.pop();item=recover(old['part']['start'],max(old['part']['end'],item['part']['end'],key=e.timestamp))
        merged.append(item)
    bounds=[(e.timestamp(r['part']['start']),e.timestamp(r['part']['end'])) for r in merged]
    parts=[]
    for item,cfg,_ in comparison:
        check_time();start,end=e.timestamp(item['start']),e.timestamp(item['end'])
        overlap=[(lo,hi) for lo,hi in bounds if lo<end and start<hi]
        if overlap:
            if not any(lo<=start and end<=hi for lo,hi in overlap):
                raise e.Pending('Reconstruction partially overlaps parent; no clipped deltas')
            continue
        points=sorted(set(e.split_points(tariffs,start,end))|settings.between(start,end))
        duration=D(str((end-start).total_seconds()))
        for lo,hi in zip(points,points[1:]):
            p,uncertain=physical_at(cfg,settings,lo)
            fraction=D(str((hi-lo).total_seconds()))/duration
            raw={**item,'start':e.iso(lo),'end':e.iso(hi),**{k:str(D(item[k])*fraction) for k in e.KEYS}}
            parts.append({'part':e.end_use_interval(raw,p),'physical':p,'uncertain':uncertain,'reconstructed':False})
    parts=sorted([*parts,*merged],key=lambda r:e.timestamp(r['part']['start']))
    return parts, {'recovered_gap_count':len(gaps)-len(unrecovered),'replacement_count':len(merged),
                   'unrecovered':unrecovered,'replacement_provenance':[r['provenance'] for r in merged]}


def corrected_view(ledger, comparison, tariffs, settings, inputs, models, current, *, sensitivity_scenarios=()):
    started=monotonic();deadline=started+90
    parts,evidence=prepare(ledger,comparison,tariffs,settings,inputs,models,current,deadline)
    initial=e.dec(comparison[0][0]['initial_soc'])
    # Frozen training-only sensitivities, never fit online or called measured
    # capacity. K is an arbitrary representative of the SOC-equivalent family.
    # Private training-derived fits excluded; caller may supply independent scenarios.
    scenarios=[('retained-physical-model',None,{})] + list(sensitivity_scenarios)
    output={};month=current.astimezone(e.TZ).replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    for name,coefs,_ in scenarios:
        if monotonic()>deadline:raise TimeoutError('Corrected-view hourly deadline')
        transform=None
        if coefs:
            a,b,c=map(D,coefs);k=D('10')
            transform=lambda cfg,a=a,b=b,c=c,k=k:cell_to_engine(cfg,k,a*k,1/(b*k),c*k)
        trajectory=replay_once(parts,tariffs,initial,e,transform=transform,max_seconds=max(.001,deadline-monotonic()))
        views={}
        for period,lower in (('lifetime',parts[0]['part']['start']),('month',e.iso(month))):
            v=window_view(trajectory,lower,parts[-1]['part']['end'],e)
            v.update(period=period,calculated_at=e.iso(current),blockers=[],model_scenario=name)
            v['actual_terminal_soc'] = comparison[-1][0].get('terminal_soc')
            vals=v['values'];vals['saving']=str(D(vals['baseline'])-D(vals['actual']))
            v['covered_hours']=vals['hours']
            v['display']={key:e.pounds(D(vals[key])) for key in ('baseline','saving')}
            v['display']['schedule']=' – '.join(e.pounds(D(x)) for x in v['schedule_saving_range'])
            v['terminal_stock_qualification']='Terminal inventory uncredited; unequal actual/virtual stocks. Initial SOC/capacity mapping and AC conversion are conditional. No proven AI winner.'
            views[period]=v
        output[name]={'periods':views,'unknown_gaps':trajectory['gaps'],
            'physics':{'status':'retained assumptions' if not coefs else 'fixed training-only sensitivity, not measured capacity',
                       'training_window':'Not supplied in source export; no online refitting',
                       'holdout_window':'Not supplied in source export',
                       'soc_coefficients':coefs,'representative_capacity_kwh':'10' if coefs else None}}
    principal=output['retained-physical-model']['periods']
    return {'schema':1,'status':'Conditional MODEL SCENARIO RANGE; not confidence or guaranteed physical bounds',
            'calculated_at':e.iso(current),'periods':principal,'fixed_scenarios':output,'preparation':evidence,
            'runtime_seconds':monotonic()-started,'layout_policy':'Corrected coverage for all three cards; parent/raw/native views unchanged',
            'boundary_qualification':'EV inclusion in Sigen CT unknown; no EV energy added/subtracted',
            'physical_limits':'No household holdout/fits included. Independent physical validation required; no verified winner.'}
