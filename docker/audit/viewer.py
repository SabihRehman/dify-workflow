#!/usr/bin/env python3
"""
Audit log viewer — a small, password-protected web page for clients.

Reads the SQLite audit database (written by sync.py) and serves a searchable,
filterable, paginated "who did what when" table. Zero third-party dependencies
(Python stdlib only), so it runs with just `python3 viewer.py` and containerizes
with a bare python image.

Config via environment:
    AUDIT_DB        path to the SQLite db      (default: ./audit.db)
    AUDIT_USER      basic-auth username        (default: admin)
    AUDIT_PASSWORD  basic-auth password        (default: changeme)
    AUDIT_PORT      port to listen on          (default: 8090)

Open http://localhost:8090 and log in with AUDIT_USER / AUDIT_PASSWORD.
"""

from __future__ import annotations

import base64
import html
import os
import sqlite3
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("AUDIT_DB", os.path.join(HERE, "audit.db"))
USER = os.environ.get("AUDIT_USER", "admin")
PASSWORD = os.environ.get("AUDIT_PASSWORD", "changeme")
PORT = int(os.environ.get("AUDIT_PORT", "8090"))
PAGE_SIZE = 50

PAGE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Audit Log</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0; background: #0f1115; color: #e6e6e6; }}
  header {{ padding: 20px 28px; border-bottom: 1px solid #262b36; }}
  h1 {{ margin: 0; font-size: 20px; }}
  .sub {{ color: #8b95a5; font-size: 13px; margin-top: 4px; }}
  form {{ padding: 16px 28px; display: flex; gap: 10px; flex-wrap: wrap; align-items: end; border-bottom: 1px solid #262b36; }}
  label {{ display: flex; flex-direction: column; font-size: 12px; color: #8b95a5; gap: 4px; }}
  input, select {{ background: #1a1e27; border: 1px solid #2f3646; color: #e6e6e6; padding: 7px 9px; border-radius: 6px; font-size: 13px; }}
  button {{ background: #3b6ef5; color: #fff; border: 0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; }}
  a.btn {{ background: #2f3646; color: #e6e6e6; text-decoration: none; padding: 8px 14px; border-radius: 6px; font-size: 13px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 10px 14px; border-bottom: 1px solid #1e232d; font-size: 13px; vertical-align: top; }}
  th {{ color: #8b95a5; font-weight: 600; position: sticky; top: 0; background: #12151c; }}
  tr:hover td {{ background: #151a22; }}
  .who {{ font-weight: 600; }}
  .email {{ color: #8b95a5; font-size: 11px; }}
  .path {{ color: #8b95a5; font-family: ui-monospace, monospace; font-size: 11px; }}
  .anon {{ color: #c06a5b; font-style: italic; }}
  .nav {{ margin-top: 10px; font-size: 13px; }}
  .nav a {{ color: #6ea0ff; text-decoration: none; }}
  details {{ margin: 0 0 8px; background: #12151c; border: 1px solid #1e232d; border-radius: 8px; }}
  summary {{ cursor: pointer; padding: 12px 16px; font-size: 13px; }}
  summary .meta {{ color: #8b95a5; }}
  details pre {{ margin: 0; padding: 0 16px 14px 32px; font-family: ui-monospace, monospace; font-size: 12px; white-space: pre-wrap; color: #cdd6e4; }}
  .n {{ display: inline-block; background: #24304a; color: #9db8ff; border-radius: 4px; padding: 1px 7px; font-size: 11px; margin-left: 6px; }}
  .wrap {{ padding: 16px 28px; }}
  .pager {{ padding: 16px 28px; display: flex; gap: 10px; align-items: center; }}
  .badge {{ display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .ok {{ background: #14351f; color: #6ee7a0; }}
  .err {{ background: #3a1c1c; color: #f08a7a; }}
  .empty {{ padding: 40px 28px; color: #8b95a5; }}
</style></head><body>
<header>
  <h1>Audit Log</h1>
  <div class="sub">Who did what, when — across all admin actions. {total} events recorded.</div>
  <div class="nav"><a href="/">Actions</a> · <a href="/changes">Workflow Changes</a></div>
</header>
<form method="get">
  <label>Search action / path<input name="q" value="{q}" placeholder="e.g. publish, delete"></label>
  <label>User<select name="user">{user_options}</select></label>
  <label>From (YYYY-MM-DD)<input name="from" value="{from_}" placeholder="2026-08-01"></label>
  <label>To (YYYY-MM-DD)<input name="to" value="{to}" placeholder="2026-08-31"></label>
  <button type="submit">Filter</button>
  <a class="btn" href="/">Reset</a>
  <a class="btn" href="/export?{export_qs}">Export CSV</a>
</form>
{table}
<div class="pager">
  {prev} <span class="sub">Page {page} of {pages}</span> {next}
</div>
</body></html>"""


_STYLE = """<style>
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0; background: #0f1115; color: #e6e6e6; }
  header { padding: 20px 28px; border-bottom: 1px solid #262b36; }
  h1 { margin: 0; font-size: 20px; }
  .sub { color: #8b95a5; font-size: 13px; margin-top: 4px; }
  .nav { margin-top: 10px; font-size: 13px; }
  .nav a { color: #6ea0ff; text-decoration: none; }
  .wrap { padding: 16px 28px; }
  details { margin: 0 0 8px; background: #12151c; border: 1px solid #1e232d; border-radius: 8px; }
  summary { cursor: pointer; padding: 12px 16px; font-size: 13px; }
  summary .meta { color: #8b95a5; }
  details pre { margin: 0; padding: 0 16px 14px 32px; font-family: ui-monospace, monospace; font-size: 12px; white-space: pre-wrap; color: #cdd6e4; }
  .n { display: inline-block; background: #24304a; color: #9db8ff; border-radius: 4px; padding: 1px 7px; font-size: 11px; margin-left: 6px; }
  .empty { padding: 40px 28px; color: #8b95a5; }
</style>"""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_events ("
        "id TEXT PRIMARY KEY, ts TEXT, account_id TEXT, account_name TEXT, "
        "account_email TEXT, action TEXT, method TEXT, path TEXT, status INTEGER, ip TEXT)"
    )
    return conn


def build_filters(params: dict) -> tuple[str, list]:
    where, args = [], []
    if q := params.get("q", "").strip():
        where.append("(action LIKE ? OR path LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if user := params.get("user", "").strip():
        where.append("account_name = ?")
        args.append(user)
    if frm := params.get("from", "").strip():
        where.append("ts >= ?")
        args.append(frm)
    if to := params.get("to", "").strip():
        where.append("ts <= ?")
        args.append(to + "T23:59:59")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, args


class Handler(BaseHTTPRequestHandler):
    def _authed(self) -> bool:
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode()
                u, _, p = decoded.partition(":")
                return u == USER and p == PASSWORD
            except Exception:
                return False
        return False

    def _require_auth(self) -> bool:
        if self._authed():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Audit Log"')
        self.end_headers()
        self.wfile.write(b"Authentication required")
        return False

    def do_GET(self):
        if not self._require_auth():
            return
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        if parsed.path == "/export":
            self._export(params)
        elif parsed.path == "/changes":
            self._changes()
        elif parsed.path == "/":
            self._page(params)
        else:
            self.send_response(404)
            self.end_headers()

    def _changes(self):
        conn = db()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workflow_diffs ("
            "version_id TEXT PRIMARY KEY, app_id TEXT, ts TEXT, author_name TEXT, "
            "author_email TEXT, prev_version_id TEXT, n_changes INTEGER, summary TEXT)"
        )
        rows = conn.execute(
            "SELECT ts, author_name, author_email, n_changes, summary "
            "FROM workflow_diffs ORDER BY ts DESC LIMIT 200"
        ).fetchall()
        conn.close()
        items = []
        for r in rows:
            who = html.escape(r["author_name"] or "unknown")
            when = html.escape((r["ts"] or "").replace("T", " ")[:19])
            n = r["n_changes"] or 0
            items.append(
                f"<details><summary><b>{who}</b> published a workflow "
                f"<span class='meta'>— {when}</span><span class='n'>{n} change"
                f"{'s' if n != 1 else ''}</span></summary>"
                f"<pre>{html.escape(r['summary'] or '')}</pre></details>"
            )
        body_items = "".join(items) or '<div class="empty">No published changes recorded yet.</div>'
        page = (
            "<!doctype html><html><head><meta charset='utf-8'><title>Workflow Changes</title>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            + _STYLE +
            "</head><body><header><h1>Workflow Changes</h1>"
            "<div class='sub'>What actually changed between published versions — node & field level.</div>"
            "<div class='nav'><a href='/'>Actions</a> · <a href='/changes'>Workflow Changes</a></div>"
            f"</header><div class='wrap'>{body_items}</div></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode())

    def _page(self, params: dict):
        conn = db()
        clause, args = build_filters(params)
        total_all = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        total = conn.execute(f"SELECT COUNT(*) FROM audit_events{clause}", args).fetchone()[0]
        page = max(1, int(params.get("page", "1") or "1"))
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        offset = (page - 1) * PAGE_SIZE
        rows = conn.execute(
            f"SELECT * FROM audit_events{clause} ORDER BY ts DESC LIMIT ? OFFSET ?",
            args + [PAGE_SIZE, offset],
        ).fetchall()
        users = [r[0] for r in conn.execute(
            "SELECT DISTINCT account_name FROM audit_events WHERE account_name != '' ORDER BY account_name"
        ).fetchall()]
        conn.close()

        # user dropdown
        sel = params.get("user", "")
        opts = ['<option value="">All users</option>']
        for u in users:
            s = " selected" if u == sel else ""
            opts.append(f'<option value="{html.escape(u)}"{s}>{html.escape(u)}</option>')

        # table
        if rows:
            trs = []
            for r in rows:
                who = (f'<span class="who">{html.escape(r["account_name"])}</span>'
                       f'<br><span class="email">{html.escape(r["account_email"] or "")}</span>'
                       if r["account_name"] else '<span class="anon">unauthenticated</span>')
                badge = "ok" if (r["status"] or 0) < 400 else "err"
                trs.append(
                    f"<tr><td>{html.escape((r['ts'] or '').replace('T',' ')[:19])}</td>"
                    f"<td>{who}</td>"
                    f"<td>{html.escape(r['action'] or '')}</td>"
                    f"<td>{html.escape(r['method'] or '')} <span class='path'>{html.escape(r['path'] or '')}</span></td>"
                    f"<td><span class='badge {badge}'>{r['status']}</span></td>"
                    f"<td class='path'>{html.escape(r['ip'] or '')}</td></tr>"
                )
            table = ("<table><thead><tr><th>Time</th><th>User</th><th>Action</th>"
                     "<th>Request</th><th>Status</th><th>IP</th></tr></thead><tbody>"
                     + "".join(trs) + "</tbody></table>")
        else:
            table = '<div class="empty">No matching events.</div>'

        # pager links
        base = {k: params.get(k, "") for k in ("q", "user", "from", "to")}
        def link(p):
            qd = {**base, "page": p}
            return "/?" + urllib.parse.urlencode({k: v for k, v in qd.items() if v})
        prev = f'<a class="btn" href="{link(page-1)}">‹ Prev</a>' if page > 1 else ""
        nxt = f'<a class="btn" href="{link(page+1)}">Next ›</a>' if page < pages else ""

        body = PAGE_TEMPLATE.format(
            total=total_all, q=html.escape(base["q"]), from_=html.escape(base["from"]),
            to=html.escape(base["to"]), user_options="".join(opts), table=table,
            page=page, pages=pages, prev=prev, next=nxt,
            export_qs=urllib.parse.urlencode({k: v for k, v in base.items() if v}),
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _export(self, params: dict):
        conn = db()
        clause, args = build_filters(params)
        rows = conn.execute(
            f"SELECT ts, account_name, account_email, action, method, path, status, ip "
            f"FROM audit_events{clause} ORDER BY ts DESC", args).fetchall()
        conn.close()
        lines = ["time,user,email,action,method,path,status,ip"]
        for r in rows:
            vals = [str(r[k] or "").replace(",", " ").replace("\n", " ") for k in r.keys()]
            lines.append(",".join(vals))
        data = "\n".join(lines).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Disposition", "attachment; filename=audit-log.csv")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # quiet the default request logging
        pass


if __name__ == "__main__":
    print(f"Audit viewer on http://0.0.0.0:{PORT}  (db: {DB_PATH}, user: {USER})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
