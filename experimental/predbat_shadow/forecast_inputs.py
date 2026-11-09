"""Issue-time-only forecast normalisation; no actual PV is accepted here."""
from datetime import timedelta
from decimal import Decimal as D
from archive import stamp


def pv_minutes(archive,keys,decision_at,minutes=2880):
    decision=stamp(decision_at)
    if minutes not in (1440,2880) or not 1<=len(keys)<=2:raise ValueError('One/two sites and 24/48h required')
    combined=[D(0)]*minutes;seen=set()
    for key in keys:
        artifact=archive.available(key,decision_at)
        if artifact['kind']!='solcast-forecast':raise ValueError('Not a received Solcast forecast')
        age=(decision-stamp(artifact['received'])).total_seconds()
        if not 0<=age<=8*3600:raise ValueError('Stale received forecast')
        body=artifact['body'];alias=body['site_alias']
        if alias in seen:raise ValueError('Duplicate site would double PV')
        seen.add(alias);rows=body['forecasts'];index=0
        for minute in range(minutes):
            lo=decision+timedelta(minutes=minute);hi=lo+timedelta(minutes=1)
            while index<len(rows) and stamp(rows[index]['period_end'])<=lo:index+=1
            if index>=len(rows):raise ValueError('Forecast does not cover planning horizon')
            row=rows[index];end=stamp(row['period_end'])
            if row['period']!='PT30M' or not end-timedelta(minutes=30)<=lo<hi<=end:raise ValueError('Forecast gap/unsupported period')
            # Solcast estimate is mean kW, not interval kWh or measured PV.
            value=D(str(row['pv_estimate']))/60
            if not value.is_finite() or value<0:raise ValueError('Invalid PV power')
            combined[minute]+=value
    return [float(x) for x in combined]
