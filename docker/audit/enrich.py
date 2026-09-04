#!/usr/bin/env python3
"""
Audit log enricher for Dify.

Reads the raw nginx audit log (JSON lines produced by the `audit` log_format),
and turns each entry into a human-readable row:

    2026-08-15 21:44 · SabihRehman <sabih811@gmail.com> · published a workflow
        POST /console/api/apps/<id>/workflows/publish → 200

It does three things the raw log can't:
  1. Decodes the JWT in the `auth` field to recover the acting account id.
  2. Resolves that account id to a name/email (looked up from Postgres).
  3. Maps the method+path to a plain-English action.

The raw replayable token is DROPPED from the enriched output — only the
identity it resolves to is kept, so the enriched log is safe to retain.

Usage:
    # pipe the raw log in:
    docker compose exec -T nginx cat /var/log/nginx/audit.log | python3 enrich.py

    # or point at a file:
    python3 enrich.py /path/to/audit.log
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys

# --- method + path  ->  plain-English action -------------------------------
# First matching pattern wins. Add rows here as you care about more actions.
ACTION_PATTERNS: list[tuple[str, str, str]] = [
    ("POST", r"^/console/api/apps/[^/]+/workflows/publish", "published the workflow"),
    ("POST", r"^/console/api/apps/[^/]+/workflows/draft", "saved a workflow draft"),
    ("POST", r"^/console/api/apps/[^/]+/name", "renamed the app"),
    ("PUT", r"^/console/api/apps/[^/]+$", "updated the app"),
    ("DELETE", r"^/console/api/apps/[^/]+$", "deleted the app"),
    ("POST", r"^/console/api/apps/import", "imported an app (DSL)"),
    ("POST", r"^/console/api/apps$", "created an app"),
    ("POST", r"^/console/api/workspaces/current/members/invite-email", "invited a member"),
    ("DELETE", r"^/console/api/workspaces/current/members/[^/]+$", "removed a member"),
    ("PUT", r"^/console/api/workspaces/current/members/[^/]+/update-role", "changed a member's role"),
    ("POST", r"^/console/api/datasets/[^/]+/documents", "uploaded a document to a dataset"),
    ("DELETE", r"^/console/api/datasets/[^/]+/documents/[^/]+$", "deleted a document"),
    ("DELETE", r"^/console/api/datasets/[^/]+$", "deleted a knowledge dataset"),
    ("POST", r"^/console/api/datasets", "created a knowledge dataset"),
    ("POST", r"^/console/api/login", "logged in"),
    ("POST", r"^/console/api/logout", "logged out"),
]

# Pure-noise endpoints — background/session chatter, not real audit actions.
# sync.py skips these so the log stays meaningful.
NOISE_PATTERNS: list[tuple[str, str]] = [
    ("POST", r"^/console/api/refresh-token"),
    ("POST", r"^/console/api/workspaces/current$"),
    ("GET", r".*"),  # safety: never audit reads even if they slip through
]


def is_noise(method: str, path: str) -> bool:
    clean = path.split("?", 1)[0]
    return any(method == m and re.match(p, clean) for m, p in NOISE_PATTERNS)


def _app_id_from_path(path: str) -> str | None:
    m = re.search(r"/apps/([0-9a-fA-F-]{36})", path)
    return m.group(1) if m else None


# --- app id -> name (cached lookup, same dual-mode as accounts) --------------
_app_cache: dict[str, str] = {}


def resolve_app(app_id: str) -> str:
    if app_id in _app_cache:
        return _app_cache[app_id]
    name = ""
    try:
        out = _psql_query(f"SELECT name FROM apps WHERE id = '{app_id}';").strip()
        if out:
            name = out.splitlines()[0]
    except Exception:
        pass
    _app_cache[app_id] = name
    return name


def describe_action(method: str, path: str) -> str:
    clean = path.split("?", 1)[0]
    label = None
    for m, pattern, lbl in ACTION_PATTERNS:
        if method == m and re.match(pattern, clean):
            label = lbl
            break
    if label is None:
        label = f"{method} {clean}"  # fallback: raw method+path

    # If the action targets a specific app, name it (e.g. ... in "Dr. Soju").
    app_id = _app_id_from_path(clean)
    if app_id:
        name = resolve_app(app_id)
        if name:
            label += f' in “{name}”'
    return label


def _decode_jwt_user_id(token: str) -> str | None:
    """Decode a raw JWT (no scheme) and return its user_id claim, unverified.

    We only need the identity claim for display; the request itself was already
    authenticated by Dify. JWT payload = middle segment, base64url-encoded JSON.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)  # pad to multiple of 4
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return None
    return payload.get("user_id")


def decode_account_id(entry: dict) -> str | None:
    """Recover the acting account id from an audit entry.

    Browser console sessions carry the JWT in the session cookie (extracted by
    nginx into `token`, working over both HTTP and HTTPS); API/bearer callers
    carry it in the `Authorization` header. Try both. (`cookie` kept for
    backward-compat with logs written before the HTTPS-safe change.)
    """
    cookie_token = (entry.get("token") or entry.get("cookie") or "").strip()
    if cookie_token:
        uid = _decode_jwt_user_id(cookie_token)
        if uid:
            return uid
    auth_header = (entry.get("auth") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return _decode_jwt_user_id(auth_header.split(" ", 1)[1].strip())
    return None


# --- account id -> name/email (cached lookup against Postgres) --------------
# Two lookup modes:
#   * container: PGHOST set -> talk to Postgres directly with the psql client
#     (uses the standard PG* env vars the container is given).
#   * host/dev:  no PGHOST  -> `docker compose exec db_postgres psql ...`.
_account_cache: dict[str, tuple[str, str]] = {}
_PG_DIRECT = bool(os.environ.get("PGHOST"))


def _psql_query(sql: str) -> str:
    if _PG_DIRECT:
        cmd = ["psql", "-tAF", "\t", "-c", sql]  # PGHOST/PGUSER/PGPASSWORD/... from env
    else:
        cmd = [
            "docker", "compose", "exec", "-T", "db_postgres",
            "psql", "-U", "postgres", "-d", "dify", "-tAF", "\t", "-c", sql,
        ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()


def resolve_account(account_id: str) -> tuple[str, str]:
    if account_id in _account_cache:
        return _account_cache[account_id]
    name, email = "unknown", ""
    try:
        out = _psql_query(f"SELECT name, email FROM accounts WHERE id = '{account_id}';")
        if out and "\t" in out:
            name, email = out.split("\t", 1)
    except Exception:
        pass
    _account_cache[account_id] = (name, email)
    return name, email


def main() -> None:
    source = open(sys.argv[1]) if len(sys.argv) > 1 else sys.stdin
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        account_id = decode_account_id(entry)
        if account_id:
            name, email = resolve_account(account_id)
            who = f"{name} <{email}>" if email else name
        else:
            who = "unauthenticated / invalid token"

        action = describe_action(entry.get("method", ""), entry.get("path", ""))
        when = entry.get("time", "").replace("T", " ")[:16]
        status = entry.get("status", "")
        ip = entry.get("ip", "")

        print(f"{when} · {who} · {action}")
        print(f"    {entry.get('method','')} {entry.get('path','')} → {status}  (from {ip})")


if __name__ == "__main__":
    main()
