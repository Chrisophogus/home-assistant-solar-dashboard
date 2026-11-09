"""Pure hourly-report adapter; no HA, database writes, fitting or controls.

Input: independently prepared, nonoverlapping AC-end-use intervals and dated
physical configurations. Reconstruction provenance must accompany replacements.
Replay ONCE from the earliest covered start; window views only reduce that run.
The caller supplies the unchanged engine and tariffs. Calibration is never run
on the reporting/collector path. This adapter is staged, NOT installed.
"""
from copy import deepcopy
from decimal import Decimal as D
from itertools import product
from time import monotonic

ZERO = D(0)
POLICY = 'cheap-window-self-consumption-v2'


def cell_to_engine(port_configuration, capacity, eta_cell_charge, eta_cell_discharge,
                   cell_standby_kw):
    """Keep AC↔DC-port and DC-port↔cell layers distinct, including port caps."""
    cfg = deepcopy(port_configuration)
    capacity, ec, ed, standby = map(D, (capacity, eta_cell_charge,
                                      eta_cell_discharge, cell_standby_kw))
    if not capacity > 0 or not 0 < ec <= 1 or not 0 < ed <= 1 or standby < 0:
        raise ValueError('Invalid independently supplied physical scenario')
    cfg['usable_capacity_kwh'] = str(capacity)
    cfg['charge_efficiency'] = str(D(cfg['charge_efficiency']) * ec)
    cfg['discharge_efficiency'] = str(D(cfg['discharge_efficiency']) * ed)
    cfg['standby_loss_kw'] = str(standby)
    # Engine DC ceilings act on stored energy. Real configured ceilings act on
    # the battery port; conversion is required, not silently redefining ports.
    cfg['battery_charge_dc_limit_kw'] = str(D(cfg['battery_charge_dc_limit_kw']) * ec)
    cfg['battery_discharge_dc_limit_kw'] = str(D(cfg['battery_discharge_dc_limit_kw']) / ed)
    return cfg


def controls(cfg, uncertain):
    if not uncertain:
        return [cfg]
    # Unknown historical controls: conservative rectangle, not an endpoint hold
    # asserted to have been observed. Installed ratings remain fixed evidence.
    return [{**cfg, 'reserve_percent': str(reserve), 'charge_limit_kw': str(charge),
             'discharge_limit_kw': str(discharge)}
            for reserve, charge, discharge in product(
                (0, 100), (ZERO, D(cfg['charge_limit_kw'])),
                (ZERO, D(cfg['discharge_limit_kw'])))]


def replay_once(parts, tariffs, initial_soc_percent, engine, *, transform=None,
                max_parts=100000, max_seconds=45):
    """Conditional state/cash enclosure; gaps have unknown energy, never free SOC.

    Fixed physical scenarios are separate complete runs, not per-step model
    switching. Control/state extrema enclose this engine's monotone piecewise
    affine fixed-policy maps only; they are not empirical confidence limits.
    Parent intervals must already be split at rate, policy and evidence changes.
    """
    started = monotonic()
    if not parts or len(parts) > max_parts or max_seconds <= 0:
        raise ValueError('Empty or unbounded prepared observation set')
    previous = None
    for item in parts:
        if monotonic()-started > max_seconds:
            raise TimeoutError('Hourly scenario deadline; no partial report published')
        part = item['part']
        lo, hi = engine.timestamp(part['start']), engine.timestamp(part['end'])
        if hi <= lo or previous is not None and lo < previous:
            raise ValueError('Overlapping/nonpositive intervals; replace, never append')
        if item.get('reconstructed') and not item.get('provenance'):
            raise ValueError('Reconstruction needs endpoint provenance')
        previous = hi
    rows, gaps = [], []
    first_cfg = parts[0]['physical']
    if transform:
        first_cfg = transform(first_cfg)
    capacity = D(first_cfg['usable_capacity_kwh'])
    initial = capacity * D(initial_soc_percent) / 100
    if not 0 <= initial <= capacity:
        raise ValueError('Initial SOC outside capacity')
    lower = upper = initial
    previous = None
    for item in parts:
        if monotonic()-started > max_seconds:
            raise TimeoutError('Hourly scenario deadline; no partial report published')
        part, cfg = item['part'], deepcopy(item['physical'])
        if transform:
            cfg = transform(cfg)
        lo, hi = engine.timestamp(part['start']), engine.timestamp(part['end'])
        # Never prorate cumulative gross registers across changing tariffs.
        ip, standing, iid = engine.interval_tariff(tariffs, 'import', lo, hi)
        ep, _, eid = engine.interval_tariff(tariffs, 'export', lo, hi)
        if D(cfg['usable_capacity_kwh']) != capacity:
            raise ValueError('Capacity revision needs an explicit conserved mapping')
        if previous is not None and previous != lo:
            hours = D(str((lo - previous).total_seconds())) / 3600
            # Ratings supplied by dated installed evidence, not actual observed
            # dispatch. Unknown interval cash omitted from ALL columns.
            maxc = min(D(cfg['charge_limit_kw']) * D(cfg['charge_efficiency']),
                       D(cfg['battery_charge_dc_limit_kw'])) * hours
            maxd = min(D(cfg['discharge_limit_kw']) / D(cfg['discharge_efficiency']),
                       D(cfg['battery_discharge_dc_limit_kw'])) * hours
            maxd += D(cfg['standby_loss_kw']) * hours
            before = [lower, upper]
            lower, upper = max(ZERO, lower-maxd), min(capacity, upper+maxc)
            gaps.append({'start': engine.iso(previous), 'end': part['start'],
                         'before': list(map(str, before)),
                         'after': list(map(str, (lower, upper))),
                         'possible_stored_transfer': [str(-maxd), str(maxc)],
                         'cash_coverage': 'Omitted identically; no zero-load claim'})
        states_before = (lower, upper)
        simulations = [engine.simulate(part, state, physical, policy=POLICY)
                       for state in dict.fromkeys(states_before)
                       for physical in controls(cfg, item.get('uncertain', False))]
        lower = min(s['stored'] for s in simulations)
        upper = max(s['stored'] for s in simulations)
        days = engine.civil_fraction(lo, hi)
        cash = [s['import']*ip-s['export']*ep+days*standing for s in simulations]
        actual = D(part['import'])*ip-D(part['export'])*ep+days*standing
        # Load was corrected using PORT conversion only during preparation.
        # Cell loss must NEVER be subtracted from AC household load a second time.
        baseline = D(part['load'])*D(cfg['baseline_import_gbp_kwh']) + days*D(cfg['baseline_standing_gbp_day'])
        rows.append({'start': part['start'], 'end': part['end'],
                     'hours': D(str((hi-lo).total_seconds()))/3600,
                     'baseline': baseline, 'actual': actual,
                     'cost_lower': min(cash), 'cost_upper': max(cash),
                     'actual_import': D(part['import']), 'actual_export': D(part['export']),
                     'import_lower': min(s['import'] for s in simulations),
                     'import_upper': max(s['import'] for s in simulations),
                     'export_lower': min(s['export'] for s in simulations),
                     'export_upper': max(s['export'] for s in simulations),
                     'stored_before': states_before, 'stored_after': (lower, upper),
                     'tariff_ids': (iid, eid),
                     'allocation': item.get('provenance', 'Original measured interval')})
        previous = hi
    return {'rows': rows, 'gaps': gaps, 'initial_stored': initial,
            'terminal_stored': (lower, upper), 'policy': POLICY,
            'qualification': 'Conditional model range, not proven savings, statistical confidence bounds or a validated physical enclosure. Measured gross actual cash retained; terminal stock uncredited.'}


def window_view(trajectory, start, end, engine):
    """Reduce ONE trajectory; never initialise month SOC from actual again.

    Select whole measured intervals only. A crossing interval is omitted and
    disclosed rather than inventing cumulative endpoint energy at midnight.
    """
    lo, hi = engine.timestamp(start), engine.timestamp(end)
    rows = [r for r in trajectory['rows'] if lo <= engine.timestamp(r['start'])
            and engine.timestamp(r['end']) <= hi]
    if not rows:
        raise ValueError('No complete interval in requested window')
    names = ('hours', 'baseline', 'actual', 'cost_lower', 'cost_upper',
             'actual_import', 'actual_export', 'import_lower', 'import_upper',
             'export_lower', 'export_upper')
    sums = {k: sum((r[k] for r in rows), ZERO) for k in names}
    segments = []
    for r in rows:
        if segments and engine.timestamp(segments[-1][1]) == engine.timestamp(r['start']):
            segments[-1][1] = r['end']
        else:
            segments.append([r['start'], r['end']])
    return {'scenario_id': POLICY, 'matched': True,
            'status': 'Conditional continuous model range; physical validation incomplete',
            'coverage_start': rows[0]['start'], 'coverage_end': rows[-1]['end'],
            'coverage_intervals': len(rows), 'gap_count': len(segments)-1,
            'coverage_segments': segments, 'segments_truncated': False,
            'values': {k: str(v) for k, v in sums.items()},
            'schedule_saving_range': [str(sums['baseline']-sums['cost_upper']),
                                      str(sums['baseline']-sums['cost_lower'])],
            'actual_saving': str(sums['baseline']-sums['actual']),
            'simulated_initial_stored_range': list(map(str, rows[0]['stored_before'])),
            'simulated_final_stored_range': list(map(str, rows[-1]['stored_after'])),
            'soc_resets': [], 'qualification': trajectory['qualification'],
            'boundary_allocation': 'Whole intervals only; no synthetic midnight counter',
            'coverage_fingerprint': __import__('hashlib').sha256(
                __import__('json').dumps([(r['start'], r['end']) for r in rows]).encode()).hexdigest()}


def attach_review_output(report, views):
    """Nonmutating report hook, preserving registered legacy policy accounting.

    Existing parent values/schedule and historical reports remain intact. A UI
    must read range fields; no range midpoint is passed off as a precise value.
    """
    result = deepcopy(report)
    for name, view in views.items():
        period = result['periods'][name]
        period['retained_segmented_ordinary_diagnostic'] = period.get('ordinary_schedule_scenario')
        period['ordinary_schedule_scenario'] = deepcopy(view)
    return result


def hourly_report(report, prepared_parts, tariffs, initial_soc_percent, engine,
                  *, transform=None, max_seconds=45):
    """Worker-only report hook. One trajectory for both live reporting windows.

    Call from the existing hourly executor AFTER endpoint preparation, never
    the minute collector. Caller owns last-good-report/error handling. No ledger
    mutation or policy registration is performed here; no partial report on error.
    """
    trajectory = replay_once(prepared_parts, tariffs, initial_soc_percent, engine,
                             transform=transform, max_seconds=max_seconds)
    first, last = trajectory['rows'][0]['start'], trajectory['rows'][-1]['end']
    # Current calculation time, not last coverage end, defines calendar month.
    # Caller may have no recent data; stale October coverage must not become a
    # November report merely because it was the last available trajectory.
    current = report.get('calculated_at', last)
    local = engine.timestamp(current).astimezone(engine.TZ)
    month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    views = {name: window_view(trajectory, start, last, engine)
             for name, start in (('lifetime', first), ('month', engine.iso(month)))
             if name in report['periods']}
    return attach_review_output(report, views)
