#!/usr/bin/env python3
"""
Workflow change tracker (Route 2).

Dify stores every PUBLISHED workflow version's full graph in the `workflows`
table. This module reads consecutive published versions per app, computes a
field-level diff (which node changed, what field, old -> new), and records a
human-readable summary into the audit DB's `workflow_diffs` table.

This answers "what did the user actually change in the flow?" — not just that
a publish happened, but which nodes/prompts/models were added, removed, or
edited between one published version and the next.

Idempotent: keyed by the version id, so re-running never duplicates.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess

from enrich import resolve_account

# Node-wrapper keys that are pure canvas cosmetics — never a real "change".
_IGNORE_DATA_KEYS = {"selected"}
_MAX_VAL = 160  # truncate long field values in the summary


def _pg(sql: str) -> str:
    """Run a single-column query via psql (PG* env vars). Returns raw stdout."""
    return subprocess.run(
        ["psql", "-tAc", sql], capture_output=True, text=True, timeout=30
    ).stdout


def published_versions(app_id: str) -> list[dict]:
    """All published versions of an app, oldest first."""
    out = _pg(
        "SELECT id||'|'||created_by||'|'||created_at FROM workflows "
        f"WHERE app_id='{app_id}' AND version <> 'draft' ORDER BY created_at ASC;"
    )
    versions = []
    for line in out.splitlines():
        line = line.strip()
        if line.count("|") >= 2:
            vid, by, ts = line.split("|", 2)
            versions.append({"id": vid, "created_by": by, "created_at": ts})
    return versions


def load_graph(version_id: str) -> dict:
    raw = _pg(f"SELECT graph FROM workflows WHERE id='{version_id}';").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"nodes": [], "edges": []}


def _trunc(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(s.split())  # collapse whitespace/newlines
    return s if len(s) <= _MAX_VAL else s[:_MAX_VAL] + "…"


def _field_changes(old: dict, new: dict, prefix: str = "") -> list[tuple[str, str, str]]:
    """Recursively list (field_path, old_value, new_value) for differing keys."""
    changes: list[tuple[str, str, str]] = []
    keys = (set(old) | set(new)) - _IGNORE_DATA_KEYS
    for k in sorted(keys):
        path = f"{prefix}{k}"
        ov, nv = old.get(k), new.get(k)
        if ov == nv:
            continue
        if isinstance(ov, dict) and isinstance(nv, dict):
            changes.extend(_field_changes(ov, nv, prefix=path + "."))
        else:
            changes.append((path, _trunc(ov) if ov is not None else "(none)",
                            _trunc(nv) if nv is not None else "(none)"))
    return changes


def diff_graphs(prev: dict, curr: dict) -> dict:
    pn = {n["id"]: n for n in prev.get("nodes", []) if isinstance(n, dict) and "id" in n}
    cn = {n["id"]: n for n in curr.get("nodes", []) if isinstance(n, dict) and "id" in n}

    def title(node):  # human label for a node
        return node.get("data", {}).get("title") or node.get("id", "?")

    added = [title(cn[i]) for i in cn if i not in pn]
    removed = [title(pn[i]) for i in pn if i not in cn]
    changed = []
    for i in cn:
        if i not in pn:
            continue
        fc = _field_changes(pn[i].get("data", {}), cn[i].get("data", {}))
        if fc:
            changed.append({"title": title(cn[i]), "changes": fc})

    # combined id -> title across both versions (an edge's node may be in either)
    id_to_title = {}
    for g in (prev, curr):
        for n in g.get("nodes", []):
            if isinstance(n, dict) and "id" in n:
                id_to_title[n["id"]] = n.get("data", {}).get("title") or n["id"]

    def edge_key(e):  # stable identity for set comparison
        return f"{e.get('source')}|{e.get('target')}|{e.get('sourceHandle','')}"

    def edge_desc(e):  # human-readable: node titles instead of ids
        s = id_to_title.get(e.get("source"), e.get("source"))
        t = id_to_title.get(e.get("target"), e.get("target"))
        return f"{s} → {t}"

    pe = {edge_key(e): e for e in prev.get("edges", []) if isinstance(e, dict)}
    ce = {edge_key(e): e for e in curr.get("edges", []) if isinstance(e, dict)}

    return {
        "added_nodes": added,
        "removed_nodes": removed,
        "changed_nodes": changed,
        "added_edges": sorted(edge_desc(ce[k]) for k in ce if k not in pe),
        "removed_edges": sorted(edge_desc(pe[k]) for k in pe if k not in ce),
    }


def summarize(d: dict) -> tuple[str, int]:
    """Human-readable multi-line summary + a total change count."""
    lines: list[str] = []
    for t in d["added_nodes"]:
        lines.append(f"+ added node: {t}")
    for t in d["removed_nodes"]:
        lines.append(f"- removed node: {t}")
    for cn in d["changed_nodes"]:
        lines.append(f"~ node \"{cn['title']}\":")
        for field, ov, nv in cn["changes"]:
            lines.append(f"    · {field}: {ov}  →  {nv}")
    for e in d["added_edges"]:
        lines.append(f"+ connected: {e}")
    for e in d["removed_edges"]:
        lines.append(f"- disconnected: {e}")
    count = (len(d["added_nodes"]) + len(d["removed_nodes"]) +
             sum(len(c["changes"]) for c in d["changed_nodes"]) +
             len(d["added_edges"]) + len(d["removed_edges"]))
    return ("\n".join(lines) if lines else "No structural changes detected."), count


SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_diffs (
    version_id      TEXT PRIMARY KEY,
    app_id          TEXT,
    ts              TEXT,
    author_name     TEXT,
    author_email    TEXT,
    prev_version_id TEXT,
    n_changes       INTEGER,
    summary         TEXT
);
CREATE INDEX IF NOT EXISTS idx_diff_ts ON workflow_diffs(ts);
"""


def sync_diffs(db_path: str) -> int:
    """Compute + store diffs for every consecutive published version pair. Idempotent."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # Which apps have published workflows?
    app_ids = [x.strip() for x in _pg(
        "SELECT DISTINCT app_id FROM workflows WHERE version <> 'draft';"
    ).splitlines() if x.strip()]

    added = 0
    for app_id in app_ids:
        versions = published_versions(app_id)
        for i in range(1, len(versions)):
            prev, curr = versions[i - 1], versions[i]
            # skip if already recorded
            if conn.execute("SELECT 1 FROM workflow_diffs WHERE version_id=?",
                            (curr["id"],)).fetchone():
                continue
            d = diff_graphs(load_graph(prev["id"]), load_graph(curr["id"]))
            summary, n = summarize(d)
            name, email = resolve_account(curr["created_by"])
            conn.execute(
                "INSERT OR IGNORE INTO workflow_diffs "
                "(version_id, app_id, ts, author_name, author_email, prev_version_id, n_changes, summary) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (curr["id"], app_id, curr["created_at"], name, email, prev["id"], n, summary),
            )
            added += 1
    conn.commit()
    conn.close()
    return added


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "audit.db"
    print(f"Recorded {sync_diffs(db)} new workflow diff(s).")
