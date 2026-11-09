"""Integration-owned SQLite ledger, outside HA internal storage/databases.

Transactions include raw samples and intervals. Ledger versions are append-only;
SQLite backup API produces consistent snapshots even while WAL is active.
No dependency on Recorder retention, and no automatic pruning of audit data.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

from .const import COMMISSIONED_AT, HISTORY_OBSERVED_AT, INITIAL_TARIFFS, POLICY, TEMPLATES
from .engine import KEYS, Pending, costs, dec, input_blockers, iso, normalize_rate, pounds, timestamp, validate_inputs, tariff_at, TZ


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def now():
    return iso(datetime.now(timezone.utc))


def normalize_tariff(payload):
    original_rate_unit = payload.get("rate_unit", "GBP_per_kWh")
    original_standing_unit = payload.get("standing_unit", "GBP_per_day")
    if original_rate_unit not in ("GBP_per_kWh", "pence_per_kWh") or original_standing_unit not in ("GBP_per_day", "pence_per_day"):
        raise ValueError("Explicit energy/standing currency units required")
    result = {**payload, "rate_unit": "GBP_per_kWh", "standing_unit": "GBP_per_day"}
    if result["kind"] not in ("import", "export") or not result.get("evidence", "").strip():
        raise ValueError("Tariff kind and evidence required")
    result["effective_from"] = iso(timestamp(result["effective_from"]))
    result["effective_to"] = iso(timestamp(result["effective_to"])) if result.get("effective_to") else None
    if result["effective_to"] and timestamp(result["effective_to"]) <= timestamp(result["effective_from"]):
        raise ValueError("Tariff end must follow start")
    if timestamp(result["effective_from"]) < timestamp(COMMISSIONED_AT):
        raise ValueError("Tariff predates accounting commissioning boundary")
    position = 0
    bands = []
    for lo, hi, value in result["bands"]:
        if not isinstance(lo, int) or not isinstance(hi, int) or lo != position or not lo < hi <= 1440:
            raise ValueError("Tariff bands must cover 00:00–24:00 contiguously")
        bands.append([lo, hi, str(normalize_rate(value, original_rate_unit))])
        position = hi
    if position != 1440:
        raise ValueError("Incomplete tariff day")
    result["bands"] = bands
    result["standing"] = str(normalize_rate(result["standing"], original_standing_unit))
    if dec(result["standing"]) < 0 or result["kind"] == "export" and dec(result["standing"]) != 0:
        raise ValueError("Nonnegative import standing and zero export standing required")
    if not isinstance(result.get("assumed", False), bool):
        raise ValueError("Assumed marker must be boolean")
    return result


class Ledger:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, kind TEXT NOT NULL, registered_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS samples(id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL UNIQUE, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS intervals(id INTEGER PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL UNIQUE, quality TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS intervals_end ON intervals(end);
                CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, digest TEXT NOT NULL UNIQUE, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history_rows(id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history_intervals(id TEXT PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history_batches(id TEXT PRIMARY KEY, registered_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS setting_observations(id TEXT PRIMARY KEY, key TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS setting_time ON setting_observations(key,timestamp);
                CREATE TRIGGER IF NOT EXISTS immutable_settings_update BEFORE UPDATE ON setting_observations BEGIN SELECT RAISE(ABORT,'Append-only settings'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_settings_delete BEFORE DELETE ON setting_observations BEGIN SELECT RAISE(ABORT,'Append-only settings'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_versions_update BEFORE UPDATE ON versions BEGIN SELECT RAISE(ABORT,'Append-only ledger'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_versions_delete BEFORE DELETE ON versions BEGIN SELECT RAISE(ABORT,'Append-only ledger'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_intervals_update BEFORE UPDATE ON intervals BEGIN SELECT RAISE(ABORT,'Append-only intervals'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_intervals_delete BEFORE DELETE ON intervals BEGIN SELECT RAISE(ABORT,'Append-only intervals'); END;
            """)
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('schema_version','1')")
            self._version(db, "policy", POLICY, POLICY["registered_at"])
            from .model import ESTIMATED_MODEL
            self._version(db, "schedule_model", ESTIMATED_MODEL, ESTIMATED_MODEL["model_registered_at"])
            from .reporting import SCOPE
            self._version(db, "scope", SCOPE)
            for name, template in TEMPLATES.items():
                self._version(db, "template", {"name": name, "active": False, **template})
            for seed in INITIAL_TARIFFS:
                self._version(db, "tariff", normalize_tariff(seed))
            self._version(db, "user_evidence", {
                "commissioned_at": COMMISSIONED_AT, "ha_interval_evidence_starts": HISTORY_OBSERVED_AT,
                "usable_capacity_before_reserve_kwh": "10", "nominal_capacity_kwh": "10",
                "reserve_percent_reported": None, "panels": None, "pv_kwp": None, "controller": None,
                "qualification": "SOURCE EXPORT EXAMPLE: no installation metadata/evidence supplied; configure your own independently verified values",
                "baseline_import_gbp_kwh": "0.25", "baseline_standing_gbp_day": "0.40",
            })

    def _version(self, db, kind, payload, registered=None):
        raw = encode(payload)
        ident = hashlib.sha256((kind + raw).encode()).hexdigest()
        db.execute("INSERT OR IGNORE INTO versions VALUES (?,?,?,?)", (ident, kind, registered or now(), raw))
        return ident

    def versions(self, kind):
        with self.connect() as db:
            return [{**json.loads(row["payload"]), "id": row["id"], "ledger_registered_at": row["registered_at"]} for row in db.execute("SELECT * FROM versions WHERE kind=? ORDER BY rowid", (kind,))]

    def tariff_versions(self):
        audit = self.versions("tariff")
        replaced = {v["replaces"] for v in audit if v.get("replaces")}
        return [v for v in audit if v["id"] not in replaced]

    def configure(self, inputs, activations, edits=()):
        """Atomically validate and append reviewed physical inputs/tariff evidence."""
        validate_inputs(inputs)
        if not isinstance(activations, list):
            raise ValueError("Tariff activations must be a JSON array")
        prepared = []
        for item in activations:
            if not isinstance(item, dict) or set(item) - {"template", "effective_from", "effective_to", "evidence"}:
                raise ValueError("Invalid activation keys")
            template = TEMPLATES.get(item.get("template"))
            if template is None or not item.get("evidence", "").strip():
                raise ValueError("Known tariff template and dated evidence required")
            start = timestamp(item["effective_from"])
            if start < timestamp(COMMISSIONED_AT):
                raise ValueError("Activation predates commissioning")
            end = timestamp(item["effective_to"]) if item.get("effective_to") else None
            if end and end <= start:
                raise ValueError("Tariff end must follow start")
            prepared.append(normalize_tariff({**template, **item, "record_id": item["template"] + ":" + iso(start), "effective_from": iso(start), "effective_to": iso(end) if end else None, "assumed": False}))
        for edit in edits:
            prepared.append(normalize_tariff(edit))
        with self.connect() as db:
            existing = [{**json.loads(r["payload"]), "id": r["id"]} for r in db.execute("SELECT * FROM versions WHERE kind='tariff'")]
            for p in prepared:
                raw_existing = [{k: v for k, v in old.items() if k != "id"} for old in existing]
                if p in raw_existing:
                    continue
                replaced = {v["replaces"] for v in existing if v.get("replaces")}
                if p.get("replaces"):
                    target = next((v for v in existing if v["id"] == p["replaces"]), None)
                    if not target or target["id"] in replaced or target["kind"] != p["kind"]:
                        raise ValueError("Correction target missing/already superseded; review latest ledger")
                # Earlier open versions are superseded from the next effective
                # start, not overwritten. Conflicting same-start rows are refused.
                for old in existing:
                    if old["id"] not in replaced and old["id"] != p.get("replaces") and old["kind"] == p["kind"] and old["effective_from"] == p["effective_from"]:
                        raise ValueError("Conflicting tariff at same effective timestamp; ledger unchanged")
                ident = self._version(db, "tariff", p)
                existing.append({**p, "id": ident})
            latest = db.execute("SELECT payload FROM versions WHERE kind='inputs' ORDER BY rowid DESC LIMIT 1").fetchone()
            current_inputs = json.loads(latest[0]) if latest else None
            if current_inputs is not None:
                current_inputs.pop("input_revision_registered_at", None)
            if current_inputs != inputs:
                self._version(db, "inputs", {**inputs, "input_revision_registered_at": now()})

    def configure_model(self, model):
        if not model:
            return
        validate_inputs(model)
        if not model.get("effective_from") or not model.get("loss_evidence") or not model.get("hardware_evidence"):
            raise ValueError("Dated schedule model and explicit evidence required")
        with self.connect() as db:
            latest = db.execute("SELECT payload FROM versions WHERE kind='schedule_model' ORDER BY rowid DESC LIMIT 1").fetchone()
            previous = json.loads(latest[0]) if latest else None
            if previous != model:
                self._version(db, "schedule_model", model)

    def collect(self, sample):
        """Keep invalid/stale/gapped/reset observations too, without bridging them."""
        with self.connect() as db:
            previous = db.execute("SELECT payload FROM samples ORDER BY id DESC LIMIT 1").fetchone()
            result = db.execute("INSERT OR IGNORE INTO samples(timestamp,payload) VALUES (?,?)", (sample["timestamp"], encode(sample)))
            if not result.rowcount or not previous:
                return
            prev = json.loads(previous[0])
            elapsed = (timestamp(sample["timestamp"]) - timestamp(prev["timestamp"])).total_seconds()
            interval = {"start": prev["timestamp"], "end": sample["timestamp"], "initial_soc": prev.get("soc"), "terminal_soc": sample.get("soc"), "reserve_percent": prev.get("reserve_percent"), "actual_mode": prev.get("mode", "Unknown"), "source": "live cumulative counters", "source_entities": sample.get("sources", {})}
            errors = []
            # Legacy rows with no structured key errors remain conservative.
            for observation in (prev, sample):
                if "key_errors" not in observation:
                    errors.extend(observation.get("errors", []))
            if prev.get("sources") != sample.get("sources"):
                errors.append("Source mapping changed; unrelated counters are not joined")
            if prev.get("collection_session") != sample.get("collection_session"):
                errors.append("Collector restarted/reloaded; no energy attribution across downtime")
            support = [v for sample_row in (prev, sample) for k, v in sample_row.get("source_timestamps", {}).items() if k in KEYS]
            interval["measurement_start"] = iso(min(map(timestamp, support))) if support else prev["timestamp"]
            interval["measurement_end"] = iso(max(map(timestamp, support))) if support else sample["timestamp"]
            if not 0 < elapsed <= 180:
                errors.append("Collector gap exceeds 180 seconds, or non-monotonic timestamp")
            interval["key_errors"] = {}
            for key in KEYS:
                issues = list(errors)
                for observation in (prev, sample):
                    issues.extend(observation.get("key_errors", {}).get(key, []))
                    if key not in observation.get("energy", {}):
                        issues.append(f"Missing {key} counter")
                a, b = prev.get("source_timestamps", {}).get(key), sample.get("source_timestamps", {}).get(key)
                if a and b and timestamp(b) <= timestamp(a):
                    issues.append(f"{key} source has not reported since previous sample")
                if not issues:
                    delta = dec(sample["energy"][key]) - dec(prev["energy"][key])
                    if delta < 0:
                        issues.append(f"{key} counter reset/decrease; no reset interpolation")
                    else:
                        interval[key] = str(delta)
                interval["key_errors"][key] = sorted(set(issues))
            interval["column_errors"] = {"saving": [], "schedule": []}
            for column, keys in (("saving", ("load", "import", "export")), ("schedule", KEYS)):
                for observation in (prev, sample):
                    stamps = [timestamp(observation["source_timestamps"][key]) for key in keys if key in observation.get("source_timestamps", {})]
                    if stamps and (max(stamps) - min(stamps)).total_seconds() > 30:
                        interval["column_errors"][column].append(f"{column} counter timestamp skew exceeds 30s")
            interval["errors"] = sorted({issue for group in interval["key_errors"].values() for issue in group})
            db.execute("INSERT INTO intervals(start,end,quality,payload) VALUES (?,?,?,?)", (interval["start"], interval["end"], "gap" if interval["errors"] else "matched", encode(interval)))

    def observe_settings(self, observations):
        with self.connect() as db:
            for row in observations:
                # Timestamp/status/value/source are part of immutable identity.
                timestamp(row["timestamp"])
                raw = encode(row)
                ident = hashlib.sha256(raw.encode()).hexdigest()
                db.execute("INSERT OR IGNORE INTO setting_observations VALUES (?,?,?,?)",
                           (ident, row["key"], row["timestamp"], raw))

    def setting_observations(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM setting_observations ORDER BY timestamp,id")]

    def _inputs_at(self, versions, instant):
        candidates = [v for v in versions if v.get("effective_from") and timestamp(v["effective_from"]) <= instant]
        if not candidates:
            raise Pending("No dated verified physical/meter-boundary input version for this interval")
        return max(enumerate(candidates), key=lambda pair: (timestamp(pair[1]["effective_from"]), pair[0]))[1]

    def report(self):
        from .reporting import build_report
        result = build_report(self, datetime.now(timezone.utc))
        raw = encode(result)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO reports(timestamp,digest,payload) VALUES (?,?,?)", (now(), digest, raw))
        return result

    def backfill_status(self):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM history_batches ORDER BY rowid DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else {"status": "Not yet imported", "source": "HA Recorder read-only history API"}

    def import_history(self, rows, sources, start, end):
        from .history import VERSION, reconstruct
        from .settings import reserve_rows
        self.observe_settings(reserve_rows(rows))
        intervals = reconstruct(rows, sources, start, end)
        summary = {"source": VERSION, "requested_start": iso(start), "requested_end": iso(end),
                   "raw_rows": len(rows), "candidate_intervals": len(intervals),
                   "eligible_load_intervals": sum(not item["key_errors"]["load"] for item in intervals),
                   "eligible_accounting_intervals": sum(not any(item["key_errors"][k] for k in ("load", "import", "export")) for item in intervals),
                   "status": "Imported retained Recorder observations; coverage is interval-specific",
                   "limitations": "Unchanged states held only during observed plant heartbeat continuity; no independent historical last_reported freshness trace. Explicit gaps, unavailable states, restart fences and resets are excluded; no daily generation extrapolation."}
        raw_id = hashlib.sha256(encode({"summary": summary, "sources": sources, "interval_ids": [i["id"] for i in intervals]}).encode()).hexdigest()
        with self.connect() as db:
            for row in rows:
                raw = encode(row)
                ident = hashlib.sha256(raw.encode()).hexdigest()
                db.execute("INSERT OR IGNORE INTO history_rows VALUES (?,?,?,?)", (ident, row["entity_id"], row["timestamp"], raw))
            for interval in intervals:
                db.execute("INSERT OR IGNORE INTO history_intervals VALUES (?,?,?,?)", (interval["id"], interval["start"], interval["end"], encode(interval)))
            db.execute("INSERT OR IGNORE INTO history_batches VALUES (?,?,?)", (raw_id, now(), encode(summary)))
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('settings_history_imported',?)", (now(),))
        return summary

    def backup(self):
        target = self.path.parent / ("backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3")
        destination = sqlite3.connect(target)
        try:
            with self.connect() as db:
                db.backup(destination)
        finally:
            destination.close()
        return str(target)
