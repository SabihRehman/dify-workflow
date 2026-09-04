#!/usr/bin/env python3
"""
Sync the raw nginx audit log into a SQLite audit database.

Reads the JSON-lines audit log, and for each state-changing action:
  - decodes the session token to recover the acting account id,
  - resolves that id to a name/email (from Dify's Postgres),
  - maps method+path to a plain-English action,
  - inserts a clean, TOKEN-FREE row into audit.db.

Idempotent: each row's primary key is a content hash, so re-running the
sync never creates duplicates — it only appends genuinely new events.
This lets you run it on a simple interval (cron / a loop) safely.

Usage:
    python3 sync.py                 # default paths (../volumes/audit/audit.log, ./audit.db)
    python3 sync.py <log> <db>      # explicit paths
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time

import enrich
from enrich import decode_account_id, describe_action, resolve_account

try:
    import diff  # workflow change tracking (Route 2)
except Exception:
    diff = None

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG = os.path.join(HERE, "..", "volumes", "audit", "audit.log")
DEFAULT_DB = os.path.join(HERE, "audit.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id             TEXT PRIMARY KEY,   -- content hash, for idempotent inserts
    ts             TEXT NOT NULL,      -- ISO timestamp of the action
    account_id     TEXT,               -- resolved account id (null if unauthenticated)
    account_name   TEXT,
    account_email  TEXT,
    action         TEXT NOT NULL,      -- plain-English action
    method         TEXT NOT NULL,
    path           TEXT NOT NULL,
    status         INTEGER,
    ip             TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts);
CREATE INDEX IF NOT EXISTS idx_audit_account ON audit_events(account_id);
"""


def row_id(entry: dict) -> str:
    """Stable content hash so identical re-reads don't duplicate rows."""
    basis = "|".join(
        str(entry.get(k, "")) for k in ("time", "method", "path", "status", "ip", "cookie", "auth")
    )
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


def sync_once(log_path: str, db_path: str) -> tuple[int, int]:
    """Process the log into the DB once. Returns (rows_added, total_rows)."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    if not os.path.exists(log_path):
        conn.close()
        return 0, 0

    added = 0
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            method, path = entry.get("method", ""), entry.get("path", "")
            if enrich.is_noise(method, path):
                continue  # skip background/session chatter

            account_id = decode_account_id(entry)
            name, email = ("", "")
            if account_id:
                name, email = resolve_account(account_id)

            cur = conn.execute(
                "INSERT OR IGNORE INTO audit_events "
                "(id, ts, account_id, account_name, account_email, action, method, path, status, ip) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    row_id(entry), entry.get("time", ""), account_id, name, email,
                    describe_action(entry.get("method", ""), entry.get("path", "")),
                    entry.get("method", ""), entry.get("path", ""),
                    entry.get("status"), entry.get("ip", ""),
                ),
            )
            added += cur.rowcount

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    conn.close()
    return added, total


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    log_path = args[0] if len(args) > 0 else DEFAULT_LOG
    db_path = args[1] if len(args) > 1 else DEFAULT_DB
    loop = "--loop" in sys.argv
    interval = int(os.environ.get("AUDIT_SYNC_INTERVAL", "5"))

    if not loop:
        added, total = sync_once(log_path, db_path)
        print(f"Synced. New rows added: {added}. Total events in DB: {total}.")
        return

    print(f"Audit sync loop started (every {interval}s). log={log_path} db={db_path}", flush=True)
    while True:
        try:
            enrich._account_cache.clear()  # pick up newly-created accounts between passes
            added, total = sync_once(log_path, db_path)
            if added:
                print(f"+{added} new events (total {total})", flush=True)
            # also record any new published-workflow diffs (what changed in the flow)
            if diff is not None and enrich._PG_DIRECT:
                d = diff.sync_diffs(db_path)
                if d:
                    print(f"+{d} new workflow diff(s)", flush=True)
        except Exception as e:  # never let the loop die
            print(f"sync error: {e}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
