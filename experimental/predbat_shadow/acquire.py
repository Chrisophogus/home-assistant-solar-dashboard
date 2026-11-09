"""Solcast-only acquisition. No HA client, telemetry upload or planner credentials."""
import json
import re
import urllib.request
from datetime import datetime, timezone


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Solcast redirects are not allowed')


def acquire(archive, key, site_ids, now=None, *, retry=False, opener=None):
    if not key or not 1 <= len(site_ids) <= 2:
        raise ValueError('API key and one/two verified sites required')
    fixed_now = now
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError('UTC-aware acquisition clock required')
    now = now.astimezone(timezone.utc)
    if any(not re.fullmatch(r'[A-Za-z0-9-]{8,100}', v) for v in site_ids):
        raise ValueError('Invalid site resource ID')
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    output = []
    for i, site in enumerate(site_ids):
        now = (fixed_now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        attempt = archive.reserve_request(now.isoformat(), 'plane-'+str(i+1), f'{now.hour//6*6:02}', retry=retry)
        request = urllib.request.Request('https://api.solcast.com.au/rooftop_sites/'+site+'/forecasts?format=json&hours=48', headers={'Authorization':'Bearer '+key,'Accept':'application/json'})
        try:
            with opener.open(request, timeout=20) as response:
                if response.status != 200:
                    raise ValueError('Non-success Solcast response')
                raw = response.read(2*1024*1024+1)
                if len(raw) > 2*1024*1024:
                    raise ValueError('Oversized Solcast response')
                payload = json.loads(raw)
            rows = payload.get('forecasts')
            if not isinstance(rows,list) or not rows:
                raise ValueError('Forecast rows missing')
            from archive import stamp
            previous = None
            clean = []
            for row in rows:
                end = stamp(row['period_end'])
                if previous is not None and end <= previous:
                    raise ValueError('Unordered forecast periods')
                if row['period'] != 'PT30M':
                    raise ValueError('Unsupported forecast resolution')
                v = {k:row[k] for k in ('period_end','period','pv_estimate','pv_estimate10','pv_estimate90') if k in row}
                if any(not isinstance(v[k],(int,float)) or not 0 <= v[k] < 100 for k in ('pv_estimate','pv_estimate10','pv_estimate90') if k in v):
                    raise ValueError('Invalid kW forecast')
                clean.append(v)
                previous = end
            received = datetime.now(timezone.utc).isoformat()
            # Preserve the exact forecast rows and receipt metadata; don't send
            # site IDs/coordinates/keys to the planner or diagnostics.
            body = {'site_alias':'plane-'+str(i+1),'attempt_id':attempt,'forecasts':clean,'power_unit':'kW','energy_conversion':'power × period hours','provider_issue_time_not_supplied':True}
            output.append(archive.put('solcast-forecast',body,received,issued=None))
            archive.finish_request(attempt,'forecast-archived')
        except Exception:
            archive.finish_request(attempt,'failed-request-counted')
            # Avoid propagating HTTP URLs, response bodies or credentials.
            raise RuntimeError('Solcast acquisition failed; attempt counted') from None
    return output
