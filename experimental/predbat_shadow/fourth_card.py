"""Additive fourth-card contract, same-row common-window financial reduction."""
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
import json
from datetime import datetime


def reduce_rows(rows):
    if not rows:
        return {'status':'Pending','blockers':['No evaluated issued-plan coverage'],'display':None}
    fields=('baseline','actual','timed_cost_lower','timed_cost_upper','predbat_cost_lower','predbat_cost_upper')
    totals={k:Decimal(0) for k in fields};hours=Decimal(0);bounds=[];prior=None
    for row in rows:
        lo=datetime.fromisoformat(row['start'].replace('Z','+00:00'));hi=datetime.fromisoformat(row['end'].replace('Z','+00:00'))
        if lo.tzinfo is None or hi.tzinfo is None or hi<=lo or (prior and lo<prior):raise ValueError('Invalid/overlapping common coverage')
        if not row.get('plan_hash') or not row.get('same_physics_and_tariffs') or not row.get('matched_coverage'):
            raise ValueError('Missing issued-plan/common-physics/coverage evidence')
        eligible=datetime.fromisoformat(row['plan_available_at'].replace('Z','+00:00'))
        if lo<eligible:raise ValueError('Plan cannot act before availability')
        values={k:Decimal(row[k]) for k in fields}
        if not all(x.is_finite() for x in values.values()):raise ValueError('Nonfinite money')
        for prefix in ('timed','predbat'):
            if values[prefix+'_cost_lower']>values[prefix+'_cost_upper']:raise ValueError('Reversed model range')
        for k in fields:totals[k]+=values[k]
        hours+=Decimal(str((hi-lo).total_seconds()))/3600
        bounds.append([row['start'],row['end'],row['plan_hash']]);prior=hi
    baseline=totals['baseline'];ranges={prefix:[baseline-totals[prefix+'_cost_upper'],baseline-totals[prefix+'_cost_lower']] for prefix in ('timed','predbat')}
    ceil=lambda v:'£'+str(v.to_integral_value(rounding=ROUND_CEILING))
    return {'status':'Conditional MODEL SCENARIO RANGE','coverage_start':rows[0]['start'],'coverage_end':rows[-1]['end'],'covered_hours':str(hours),'coverage_fingerprint':sha256(json.dumps(bounds,separators=(',',':')).encode()).hexdigest(),'display':' – '.join(ceil(x) for x in ranges['predbat']),'schedule_saving_range':[str(x) for x in ranges['predbat']],'aligned_comparisons':{'baseline':str(baseline),'actual_cost':str(totals['actual']),'actual_saving':str(baseline-totals['actual']),'timed_saving_range':[str(x) for x in ranges['timed']]},'qualification':'Only new issued-plan matched coverage; no historical extrapolation, midpoint or terminal-stock cash credit'}
