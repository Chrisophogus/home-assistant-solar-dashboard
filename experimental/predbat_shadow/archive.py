"""Private shadow archive primitives; no HTTP, HA or control dependencies."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3


def stamp(value):
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('Timezone required')
    return t.astimezone(timezone.utc)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Archive:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, day TEXT, site TEXT, slot TEXT, retry INTEGER, at TEXT, outcome TEXT);
          CREATE TABLE IF NOT EXISTS artifacts(hash TEXT PRIMARY KEY, kind TEXT, received TEXT, issued TEXT, body TEXT);
          CREATE TABLE IF NOT EXISTS states(id INTEGER PRIMARY KEY, at TEXT UNIQUE, parent INTEGER, stored_lower TEXT, stored_upper TEXT, coverage_hash TEXT, plan_hash TEXT, evidence TEXT);
        ''')

    def close(self):
        self.db.close()

    def reserve_request(self, now, site, slot, *, retry=False):
        """Commit budget BEFORE HTTP. Crashes/timeouts/retries all consume budget.

        Caller must use a trusted acquisition clock and same archive for all sites.
        00/06/12/18 UTC x two sites = 8 primary requests + 2 shared retries.
        """
        t = stamp(now)
        if site not in ('plane-1', 'plane-2') or slot not in ('00', '06', '12', '18'):
            raise ValueError('Unregistered site/slot')
        if int(slot) > t.hour:
            raise ValueError('Future slot')
        day = t.date().isoformat()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            total = self.db.execute('SELECT count(*) FROM attempts WHERE day=?', (day,)).fetchone()[0]
            repeated = self.db.execute('SELECT count(*) FROM attempts WHERE day=? AND site=? AND slot=?', (day, site, slot)).fetchone()[0]
            retries = self.db.execute('SELECT count(*) FROM attempts WHERE day=? AND retry=1', (day,)).fetchone()[0]
            if total >= 10 or (retry and (retries >= 2 or not repeated)) or (not retry and repeated):
                raise ValueError('Request budget/duplicate slot rejected')
            cur = self.db.execute('INSERT INTO attempts(day,site,slot,retry,at,outcome) VALUES(?,?,?,?,?,?)', (day,site,slot,int(retry),now,'reserved-before-send'))
            self.db.execute('COMMIT')
            return cur.lastrowid
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def finish_request(self, attempt, outcome):
        self.db.execute('UPDATE attempts SET outcome=? WHERE id=?', (outcome,attempt))

    def put(self, kind, body, received, issued=None):
        """Archive exact JSON payload + envelope, never credentials or site coordinates.

        API acquisition must redact credentials/location metadata before invoking.
        issued=None explicitly means provider issue timestamp was not supplied.
        Receipt time is always the earliest usable local availability boundary.
        """
        received = stamp(received).isoformat()
        if issued:
            issued = stamp(issued).isoformat()
        if issued and stamp(issued) > stamp(received):
            raise ValueError('Future provider issue timestamp')
        value = canonical({'kind':kind,'received':received,'issued':issued,'body':body})
        key = sha256(value.encode()).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?)', (key,kind,received,issued,value))
        return key

    def available(self, key, decision):
        row = self.db.execute('SELECT body FROM artifacts WHERE hash=?', (key,)).fetchone()
        if not row:
            raise ValueError('Unknown artifact')
        if sha256(row[0].encode()).hexdigest() != key:
            raise ValueError('Artifact integrity failure')
        value = json.loads(row[0])
        if stamp(value['received']) > stamp(decision):
            raise ValueError('Hindsight input rejected')
        return value

    def seed(self, at, lower, upper, evidence):
        """One explicit initial-state assumption per trajectory, never per replan."""
        from decimal import Decimal as D
        if not 0 <= D(lower) <= D(upper):
            raise ValueError('Invalid inventory interval')
        if self.db.execute('SELECT count(*) FROM states').fetchone()[0]:
            raise ValueError('Cannot re-anchor existing trajectory')
        self.db.execute('INSERT INTO states(at,parent,stored_lower,stored_upper,coverage_hash,plan_hash,evidence) VALUES(?,NULL,?,?,NULL,NULL,?)', (at,lower,upper,canonical(evidence)))

    def latest(self):
        row = self.db.execute('SELECT id,at,stored_lower,stored_upper,coverage_hash FROM states ORDER BY id DESC LIMIT 1').fetchone()
        if not row:
            raise ValueError('Trajectory not seeded')
        return {'id':row[0],'at':row[1],'lower':row[2],'upper':row[3],'coverage_hash':row[4]}

    def advance(self, parent, end, lower, upper, plan_hash, coverage_hash, evidence):
        """Persist replay output using compare-and-swap, not planner terminal forecast.

        Only the verified common-physics replay coordinator may call this. Unknown
        energy gaps require interval propagation; cannot reset to measured SOC.
        """
        from decimal import Decimal as D
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.latest()
            if old['id'] != parent or stamp(end) <= stamp(old['at']):
                raise ValueError('Stale/duplicate/nonforward replay')
            if not 0 <= D(lower) <= D(upper) or not coverage_hash:
                raise ValueError('Invalid replay output')
            self.available(plan_hash,old['at'])
            self.db.execute('INSERT INTO states(at,parent,stored_lower,stored_upper,coverage_hash,plan_hash,evidence) VALUES(?,?,?,?,?,?,?)', (end,parent,lower,upper,coverage_hash,plan_hash,canonical(evidence)))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
