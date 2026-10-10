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

"""SQLite storage: 5-minute and hourly traffic per device and server, devices, and names seen.

Both resolutions are written on every flush (no rollup job); old rows are pruned by age.
"""
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS flows_5m (
    bucket INTEGER NOT NULL,        -- unix time, start of the 5-minute slot
    ip TEXT NOT NULL,               -- LAN device address
    mac TEXT NOT NULL DEFAULT '',
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
    PRIMARY KEY (bucket, ip, server, port, proto, name)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS flows_5m_ip ON flows_5m (ip, bucket);
CREATE INDEX IF NOT EXISTS flows_5m_name ON flows_5m (name, bucket);

CREATE TABLE IF NOT EXISTS flows_1h (
    bucket INTEGER NOT NULL,
    ip TEXT NOT NULL,
    mac TEXT NOT NULL DEFAULT '',
    server TEXT NOT NULL,
    port INTEGER NOT NULL,
    proto TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    inbound INTEGER NOT NULL DEFAULT 0,
    up INTEGER NOT NULL DEFAULT 0,
    down INTEGER NOT NULL DEFAULT 0,
    pkts INTEGER NOT NULL DEFAULT 0,
    conns INTEGER NOT NULL DEFAULT 0,
    last INTEGER NOT NULL DEFAULT 0,    -- when loupd last added to this row (0: before this was kept)
    PRIMARY KEY (bucket, ip, server, port, proto, name)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS flows_1h_ip ON flows_1h (ip, bucket);
CREATE INDEX IF NOT EXISTS flows_1h_name ON flows_1h (name, bucket);

-- names each device asked for or connected to, per day (also names that moved no bytes we saw)
CREATE TABLE IF NOT EXISTS lookups (
    day INTEGER NOT NULL,
    ip TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

UPSERT = """
INSERT INTO {t} (bucket, ip, mac, server, port, proto, name, source, inbound, up, down, pkts, conns, last)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (bucket, ip, server, port, proto, name) DO UPDATE SET
    mac = CASE WHEN excluded.mac != '' THEN excluded.mac ELSE mac END,
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
        for table in ("flows_5m", "flows_1h"):
            if "last" not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN last INTEGER NOT NULL DEFAULT 0")

    def close(self):
        self.db.close()

    def write(self, flows, lookups, devices, now=None):
        """flows: {(bucket5m, ip, server, port, proto, name): [mac, source, inbound, up, down, pkts, conns]}
        lookups: {(day, ip, name, source): [count, first, last]}
        devices: {mac: {"ip", "ts", "hostname"?, "info"?: {...}}}"""
        db = self.db
        db.execute("BEGIN")
        try:
            now = int(now or time.time())
            rows = [(b, ip, v[0], srv, port, proto, name, v[1], v[2], v[3], v[4], v[5], v[6], now)
                    for (b, ip, srv, port, proto, name), v in flows.items()]
            db.executemany(UPSERT.format(t="flows_5m"), rows)
            db.executemany(UPSERT.format(t="flows_1h"), [(r[0] - r[0] % 3600, *r[1:]) for r in rows])
            db.executemany(
                "INSERT INTO lookups (day, ip, name, source, count, first_seen, last_seen) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT (day, ip, name, source) DO UPDATE SET count = count + excluded.count, "
                "last_seen = max(last_seen, excluded.last_seen)",
                [(d, ip, n, s, *v) for (d, ip, n, s), v in lookups.items()])
            for mac, d in devices.items():
                old = db.execute("SELECT info FROM devices WHERE mac = ?", (mac,)).fetchone()
                info = json.loads(old[0]) if old else {}
                for k, v in (d.get("info") or {}).items():
                    if isinstance(v, list):
                        info[k] = sorted(set(info.get(k, [])) | set(v))
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
        """[(mac, ip, hostname, info dict, [names looked up since `since`])]"""
        names = {}
        for ip, name in self.db.execute("SELECT DISTINCT ip, name FROM lookups WHERE day >= ?", (since,)):
            names.setdefault(ip, []).append(name)
        return [(mac, ip, hn, json.loads(info), names.get(ip, []))
                for mac, ip, hn, info in self.db.execute("SELECT mac, ip, hostname, info FROM devices")]

    def set_device_types(self, rows):
        """rows: [(name, vendor, type, type_source, model, mac)]"""
        self.db.execute("BEGIN")
        self.db.executemany("UPDATE devices SET name = ?, vendor = ?, type = ?, type_source = ?, model = ? WHERE mac = ?",
                            rows)
        self.db.execute("COMMIT")

    def prune(self, now, days_5m=30, days_1h=365, days_lookups=30):
        db = self.db
        db.execute("DELETE FROM flows_5m WHERE bucket < ?", (now - days_5m * 86400,))
        db.execute("DELETE FROM flows_1h WHERE bucket < ?", (now - days_1h * 86400,))
        db.execute("DELETE FROM lookups WHERE day < ?", (now - days_lookups * 86400,))
        db.execute("PRAGMA optimize")
