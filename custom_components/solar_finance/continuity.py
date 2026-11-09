"""Staged only: conserved episode boundaries and explicit inventory settlement.

This is NOT a retrofit to production reports or a fitted physical model.
Unknown gap demand is never advanced as zero. Recovered cumulative intervals
may join episodes only after external endpoint/source/configuration validation.
Settlement prices describe a separate inventory-balanced scenario, not cash
actually paid to a supplier. No implicit SOC re-anchoring exists in this API.
"""
from dataclasses import dataclass
from decimal import Decimal as D


class UnrecoveredGap(ValueError):
    pass


@dataclass
class Trajectory:
    stored: D
    end: str
    net_charge: D = D(0)
    external_transfer: D = D(0)

    def advance(self, start, end, charged_stored, discharged_stored, loss_stored=D(0)):
        if start != self.end:
            raise UnrecoveredGap('Unobserved demand/dispatch: reconstruct or explicitly close this episode')
        delta = D(charged_stored) - D(discharged_stored) - D(loss_stored)
        self.stored += delta
        self.net_charge += delta
        self.end = end
        return self.stored

    def settle(self, observed_terminal, eta_c, eta_d, min_import_rate, max_import_rate, reference_rate):
        """Conserve stored energy; value deficit/surplus separately from cash.

        Lower/upper are opportunity-value scenarios on supplied retained rates,
        NOT guaranteed bounds on the inaccessible full-period counterfactual.
        The upper-cost case credits no unspent surplus as realised cash.
        """
        target = D(observed_terminal)
        transfer = target - self.stored
        deficit, surplus = max(D(0), transfer), max(D(0), -transfer)
        eta_c, eta_d = D(eta_c), D(eta_d)
        rlo, rhi, ref = D(min_import_rate), D(max_import_rate), D(reference_rate)
        if not 0 < eta_c <= 1 or not 0 < eta_d <= 1 or not 0 <= rlo <= ref <= rhi:
            raise ValueError('Invalid fixed evidence/valuation parameters')
        result = {
            'stored_transfer_kwh': transfer,
            'replacement_import_kwh': deficit / eta_c,
            'surplus_deliverable_ac_kwh': surplus * eta_d,
            'cost_lower': deficit / eta_c * rlo - surplus * eta_d * rhi,
            'cost_upper': deficit / eta_c * rhi,
            'reference_cost': deficit / eta_c * ref - surplus * eta_d * ref,
            'qualification': 'Explicit inventory opportunity-value settlement; not measured supplier cash or guaranteed full-period policy bounds',
        }
        self.stored = target
        self.external_transfer += transfer
        return result


def recover_cumulative(left, right, intermediate, stable_configuration, stable_tariff_and_policy, max_seconds=900):
    """Pure endpoint validator. Inputs use collector-normalised kWh.

    Caller must supply dated physical-identity/configuration/tariff validation.
    Collector UUID changes do not imply a physical cumulative counter reset.
    No result overwrites any source interval; provenance hashes belong alongside
    the returned derived observation in an append-only reconstruction table.
    """
    keys = ('import', 'export', 'load', 'pv', 'charge', 'discharge')
    from datetime import datetime
    stamp = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00'))
    seconds = (stamp(right['timestamp']) - stamp(left['timestamp'])).total_seconds()
    if not 0 < seconds <= max_seconds or not stable_configuration or not stable_tariff_and_policy:
        raise UnrecoveredGap('Unknown/changing duration, configuration, reserve, tariff or dispatch band')
    chain = [left, *intermediate, right]
    if not left.get('sources') or any(s.get('sources') != left.get('sources') for s in chain):
        raise UnrecoveredGap('Physical source mapping discontinuity')
    for s in (left, right):
        if s.get('normalised_unit') != 'kWh' or any(k not in s.get('energy', {}) for k in keys):
            raise UnrecoveredGap('Unknown endpoint unit/counter')
        support = [stamp(s['source_timestamps'][k]) for k in keys]
        if any(not 0 <= (stamp(s['timestamp'])-t).total_seconds() <= 90 for t in support) or (max(support)-min(support)).total_seconds() > 30:
            raise UnrecoveredGap('Stale/asynchronous endpoint')
    for k in keys:
        values = [D(s['energy'][k]) for s in chain if k in s.get('energy', {})]
        if any(b < a for a,b in zip(values,values[1:])):
            raise UnrecoveredGap('Cumulative reset/decrease')
    return {'start':left['timestamp'], 'end':right['timestamp'], **{k:str(D(right['energy'][k])-D(left['energy'][k])) for k in keys},
            'provenance':'Derived cumulative endpoint difference; originals immutable',
            'collector_session_changed': left.get('collection_session') != right.get('collection_session'),
            'intermediate_missing_counters': any(k not in s.get('energy', {}) for s in intermediate for k in keys),
            'time_allocation_uncertainty':'Totals measured at asynchronous endpoints; no invented within-gap zero load or instantaneous dispatch. Average-power simulation remains an assumption.'}
