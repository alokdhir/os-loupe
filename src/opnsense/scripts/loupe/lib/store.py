# Copyright (C) 2026 Alok K. Dhir
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
# OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""SQLite storage: 1-minute, 5-minute and hourly traffic per device and server, hourly and daily summaries per
device and service with each day's naming evidence, devices, and names seen.

The traffic resolutions are written on every flush; the summaries are built from the hourly rows (loupd);
old rows are pruned by age.
"""
import json
import os
import sqlite3
import time

# traffic per time slot, device, server, port and name (flows_1m, flows_5m, flows_1h)
FLOWS = """
CREATE TABLE IF NOT EXISTS {t} (
    bucket INTEGER NOT NULL,        -- unix time, start of the slot
    ip TEXT NOT NULL,               -- LAN device address
    mac TEXT NOT NULL DEFAULT '',   -- the device at that address then
    server TEXT NOT NULL,           -- remote address
    port INTEGER NOT NULL,          -- remote port
    proto TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',  -- server name ('' = unknown)
    source TEXT NOT NULL DEFAULT '',-- sni | quic | dns | dns*
    inbound INTEGER NOT NULL DEFAULT 0, -- 1: the remote side opened the connection
    up INTEGER NOT NULL DEFAULT 0,
    down INTEGER NOT NULL DEFAULT 0,
    pkts INTEGER NOT NULL DEFAULT 0,
    conns INTEGER NOT NULL DEFAULT 0,
    last INTEGER NOT NULL DEFAULT 0,    -- when loupd last added to this row (0: before this was kept)
    PRIMARY KEY (bucket, ip, mac, server, port, proto, name)
) WITHOUT ROWID
"""
FLOW_COLUMNS = "bucket, ip, mac, server, port, proto, name, source, inbound, up, down, pkts, conns, last"
# 1-minute detail for the last hours, 5-minute and hourly for the settings' retention; indexed by address (the
# device page by address) and by name (searches)
FLOW_INDEXES = {"flows_1m": ("ip",), "flows_5m": ("ip", "name"), "flows_1h": ("ip", "name")}


def flow_indexes(t):
    return [f"CREATE INDEX IF NOT EXISTS {t}_{c} ON {t} ({c}, bucket)" for c in FLOW_INDEXES[t]]


SCHEMA = ";\n".join(FLOWS.format(t=t).strip() + ";\n" + ";\n".join(flow_indexes(t)) for t in FLOW_INDEXES) + """;

-- names each device asked for or connected to, per day (also names that moved no bytes we saw)
CREATE TABLE IF NOT EXISTS lookups (
    day INTEGER NOT NULL,
    ip TEXT NOT NULL,
    mac TEXT NOT NULL DEFAULT '',   -- the device at that address then ('' = not known)
    name TEXT NOT NULL,
    source TEXT NOT NULL,           -- sni | quic | dns
    count INTEGER NOT NULL DEFAULT 0,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (day, ip, name, source)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS devices (
    mac TEXT PRIMARY KEY,
    ip TEXT,
    name TEXT,                        -- display name: DHCP lease / static host entry
    hostname TEXT,                    -- what the device calls itself (DHCP option 12)
    vendor TEXT,
    type TEXT,
    type_source TEXT,
    model TEXT,                       -- e.g. "MacBook Air 15″ (M3)" when the device announces it
    info TEXT NOT NULL DEFAULT '{}',  -- JSON: dhcp, mdns, ... clues for device identification
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL
);

-- traffic per device and service per hour (recent days) and per local day, named with the day's evidence, and that
-- evidence: loupd builds them from flows_1h (lib/report.py), and builds a day again when the service map changes
CREATE TABLE IF NOT EXISTS services_1h (
    hour INTEGER NOT NULL,
    ip TEXT NOT NULL,
    mac TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL,          -- '' = none
    up INTEGER NOT NULL DEFAULT 0,
    down INTEGER NOT NULL DEFAULT 0,
    conns INTEGER NOT NULL DEFAULT 0,
    last INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, ip, mac, service)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS day_names (
    day INTEGER NOT NULL,
    kind TEXT NOT NULL,             -- owner: a service named on this server | tunnel: its VPN | provider: the VPN app
    ip TEXT NOT NULL,               -- the device's address (provider: its MAC, or address without one)
    server TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (day, ip, kind, server, value)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS services_1d (
    day INTEGER NOT NULL,           -- local midnight
    ip TEXT NOT NULL,
    mac TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL,          -- '' = none
    up INTEGER NOT NULL DEFAULT 0,
    down INTEGER NOT NULL DEFAULT 0,
    conns INTEGER NOT NULL DEFAULT 0,
    last INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, ip, mac, service)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS summary_days (
    day INTEGER PRIMARY KEY,
    map TEXT NOT NULL,              -- fingerprint of the service map it was built with
    built INTEGER NOT NULL          -- when
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

KEEP_1M = 4 * 3600       # seconds of 1-minute detail (the short report periods use up to 3 hours)
KEEP_1H_SUMMARY = 3 * 86400     # seconds of the hourly summary (report periods up to 48 hours)

UPSERT = """
INSERT INTO {t} (bucket, ip, mac, server, port, proto, name, source, inbound, up, down, pkts, conns, last)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (bucket, ip, mac, server, port, proto, name) DO UPDATE SET
    up = up + excluded.up, down = down + excluded.down,
    pkts = pkts + excluded.pkts, conns = conns + excluded.conns,
    last = max(last, excluded.last)
"""


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(devices)")}
        for col in ("name", "model"):      # added after the first release
            if col not in cols:
                self.db.execute(f"ALTER TABLE devices ADD COLUMN {col} TEXT")
        rebuilt = False
        for table in FLOW_INDEXES:
            if "last" not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN last INTEGER NOT NULL DEFAULT 0")
            if "mac" not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})") if r[5]}:
                self._key_with_mac(table)
                rebuilt = True
        if rebuilt:
            # each rebuilt table landed in the space the one before it freed, scattered through the file: reports
            # reading a day of it got ~1.7 times slower. Rewrite the file in order once, and give back the log.
            self.db.execute("VACUUM")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if "mac" not in {r[1] for r in self.db.execute("PRAGMA table_info(lookups)")}:
            self._add_lookups_mac()
        self.db.execute("CREATE INDEX IF NOT EXISTS lookups_mac ON lookups (mac, day)")

    def _key_with_mac(self, table):
        """Added later: the MAC is part of a traffic row's key, so a device that takes over an address within a
        time slot gets its own row instead of being added to the other device's. Rows are copied as they are."""
        db = self.db
        db.execute("BEGIN")
        try:
            db.execute(FLOWS.format(t=f"{table}_new"))
            db.execute(f"INSERT INTO {table}_new ({FLOW_COLUMNS}) SELECT {FLOW_COLUMNS} FROM {table}")
            db.execute(f"DROP TABLE {table}")
            db.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
            for sql in flow_indexes(table):
                db.execute(sql)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def _add_lookups_mac(self):
        """Added later: older lookups get the device that used the address that day, from the traffic rows
        (one query per address and day, not per row: a busy device has thousands of traffic rows an hour)."""
        db = self.db
        db.execute("BEGIN")
        try:
            db.execute("ALTER TABLE lookups ADD COLUMN mac TEXT NOT NULL DEFAULT ''")
            for ip, day in db.execute("SELECT DISTINCT ip, day FROM lookups").fetchall():
                row = db.execute("SELECT mac FROM flows_1h WHERE ip = ? AND bucket >= ? AND bucket < ? AND mac != '' "
                                 "GROUP BY mac ORDER BY count(*) DESC LIMIT 1", (ip, day, day + 90000)).fetchone()
                if row:         # 25 h: a day with a clock change is 23 or 25 hours long
                    db.execute("UPDATE lookups SET mac = ? WHERE day = ? AND ip = ?", (row[0], day, ip))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()

    def write(self, flows, lookups, devices, now=None):
        """flows: {(bucket1m, ip, server, port, proto, name): [mac, source, inbound, up, down, pkts, conns]}
        (minute buckets; the 5-minute and hourly rows are derived)
        lookups: {(day, ip, name, source): [count, first, last, mac]}
        devices: {mac: {"ip", "ts", "hostname"?, "info"?: {...}}}"""
        db = self.db
        db.execute("BEGIN")
        try:
            now = int(now or time.time())
            rows = [(b, ip, v[0], srv, port, proto, name, v[1], v[2], v[3], v[4], v[5], v[6], now)
                    for (b, ip, srv, port, proto, name), v in flows.items()]
            db.executemany(UPSERT.format(t="flows_1m"), rows)
            db.executemany(UPSERT.format(t="flows_5m"), [(r[0] - r[0] % 300, *r[1:]) for r in rows])
            db.executemany(UPSERT.format(t="flows_1h"), [(r[0] - r[0] % 3600, *r[1:]) for r in rows])
            db.executemany(
                "INSERT INTO lookups (day, ip, name, source, count, first_seen, last_seen, mac) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT (day, ip, name, source) DO UPDATE SET count = count + excluded.count, "
                "last_seen = max(last_seen, excluded.last_seen), "
                "mac = CASE WHEN excluded.mac != '' THEN excluded.mac ELSE mac END",
                [(d, ip, n, s, *v) for (d, ip, n, s), v in lookups.items()])
            for mac, d in devices.items():
                old = db.execute("SELECT info FROM devices WHERE mac = ?", (mac,)).fetchone()
                info = json.loads(old[0]) if old else {}
                for k, v in (d.get("info") or {}).items():
                    if isinstance(v, list):
                        info[k] = sorted(set(info.get(k, [])) | set(v))
                    elif isinstance(v, dict):           # {item: last seen}: keep the latest of each
                        old = info.get(k) if isinstance(info.get(k), dict) else {}
                        info[k] = {**old, **{i: max(t, old.get(i, 0)) for i, t in v.items()}}
                    else:
                        info[k] = v
                db.execute(
                    "INSERT INTO devices (mac, ip, hostname, info, first_seen, last_seen) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT (mac) DO UPDATE SET ip = coalesce(excluded.ip, ip), "
                    "hostname = coalesce(excluded.hostname, hostname), info = excluded.info, "
                    "last_seen = max(last_seen, excluded.last_seen)",
                    (mac, d.get("ip"), d.get("hostname"), json.dumps(info, sort_keys=True), d["ts"], d["ts"]))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def devices_with_names(self, since):
        """[(mac, ip, hostname, info dict, [names looked up since `since`])] - by MAC; rows without one by address"""
        by_mac, by_ip = {}, {}
        for mac, ip, name in self.db.execute("SELECT DISTINCT mac, ip, name FROM lookups WHERE day >= ?", (since,)):
            if mac:
                by_mac.setdefault(mac, set()).add(name)
            else:
                by_ip.setdefault(ip, set()).add(name)
        return [(mac, ip, hn, json.loads(info), sorted(by_mac.get(mac, set()) | by_ip.get(ip, set())))
                for mac, ip, hn, info in self.db.execute("SELECT mac, ip, hostname, info FROM devices")]

    def set_device_types(self, rows):
        """rows: [(name, vendor, type, type_source, model, mac)]"""
        self.db.execute("BEGIN")
        try:
            self.db.executemany("UPDATE devices SET name = ?, vendor = ?, type = ?, type_source = ?, model = ? "
                                "WHERE mac = ?", rows)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")      # never leave the shared connection inside a transaction
            raise

    def oldest_hour(self):
        return self.db.execute("SELECT min(bucket) FROM flows_1h").fetchone()[0]

    def summary_state(self, since):
        """{day: (service map fingerprint, built at)} for the summarized days from `since`."""
        return {d: (m, b) for d, m, b in self.db.execute("SELECT day, map, built FROM summary_days WHERE day >= ?", (since,))}

    def replace_day(self, day, end, daily, hourly, evidence, fingerprint, now):
        """A whole day [day, end): daily {(ip, mac, service): [up, down, conns, last]}, hourly {(hour, ip, mac,
        service): [...]} (None: not kept for this day), evidence [(kind, ip, server, value)]."""
        db = self.db
        db.execute("BEGIN")
        try:
            db.execute("DELETE FROM services_1d WHERE day = ?", (day,))
            db.executemany("INSERT INTO services_1d (day, ip, mac, service, up, down, conns, last) VALUES (?,?,?,?,?,?,?,?)",
                           [(day, *k, *v) for k, v in daily.items()])
            db.execute("DELETE FROM services_1h WHERE hour >= ? AND hour < ?", (day, end))
            if hourly is not None:
                db.executemany("INSERT INTO services_1h (hour, ip, mac, service, up, down, conns, last) "
                               "VALUES (?,?,?,?,?,?,?,?)", [(*k, *v) for k, v in hourly.items()])
            db.execute("DELETE FROM day_names WHERE day = ?", (day,))
            db.executemany("INSERT INTO day_names (day, kind, ip, server, value) VALUES (?,?,?,?,?)",
                           [(day, *e) for e in evidence])
            db.execute("INSERT OR REPLACE INTO summary_days (day, map, built) VALUES (?,?,?)", (day, fingerprint, int(now)))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def prune(self, now, days_5m=30, days_1h=365, days_lookups=30):
        db = self.db
        db.execute("DELETE FROM flows_1m WHERE bucket < ?", (now - KEEP_1M,))
        db.execute("DELETE FROM flows_5m WHERE bucket < ?", (now - days_5m * 86400,))
        db.execute("DELETE FROM flows_1h WHERE bucket < ?", (now - days_1h * 86400,))
        db.execute("DELETE FROM services_1d WHERE day < ?", (now - days_1h * 86400,))
        db.execute("DELETE FROM summary_days WHERE day < ?", (now - days_1h * 86400,))
        db.execute("DELETE FROM day_names WHERE day < ?", (now - days_1h * 86400,))
        db.execute("DELETE FROM services_1h WHERE hour < ?", (now - KEEP_1H_SUMMARY,))
        db.execute("DELETE FROM lookups WHERE day < ?", (now - days_lookups * 86400,))
        db.execute("PRAGMA optimize")
