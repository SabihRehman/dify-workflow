# Audit System

A self-contained audit trail for this Dify deployment. It records **who did
what, when** across all admin actions, and **what actually changed** in each
published workflow — neither of which Dify's Community edition provides.

It is fully independent of Dify: it never modifies Dify's application code or
images. It only adds nginx logging config and two small side-car containers.
Delete the two `audit_*` services from `docker-compose.yaml` to disable it.

## How it works

```
admin action → Dify nginx logs it → volumes/audit/audit.log (persisted)
                                          │
                         audit_sync (loop, every ~5s)
                           · decodes the session cookie → account
                           · maps method+path → plain-English action
                           · diffs each published workflow version
                           · drops the raw token
                                          ▼
                                  SQLite audit.db
                                          │
                         audit_viewer  ── password-protected web UI
                                          ▼  (port 8090)
                                  browser: Actions + Workflow Changes
```

Pieces (all in this folder):
- `enrich.py` — JWT decode + account-name resolution + action mapping
- `sync.py` — continuous loop: raw log → deduplicated SQLite rows
- `diff.py` — field-level diff of consecutive published workflow versions
- `viewer.py` — zero-dependency web viewer (search, filter, CSV export)
- `Dockerfile` — image shared by the sync loop and the viewer

## Deploy

1. Set credentials in `docker/.env` (never commit real values):
   ```
   AUDIT_USER=admin
   AUDIT_PASSWORD=<a strong password>
   ```
2. Start everything (the `--build` flag builds the two audit images):
   ```
   docker compose up -d --build
   ```
3. Open the viewer at `http://<host>:8090` (login with the values above).
   - **Actions** page: who/what/when for every admin action.
   - **Workflow Changes** page: node/field-level diff of each publish.

## Production notes

- **Serve over HTTPS** behind your reverse proxy at its own subdomain
  (e.g. `audit.example.com`). Do **not** expose port 8090 publicly in the clear.
- The "who" resolution already works over both HTTP (`access_token` cookie) and
  HTTPS (`__Host-access_token` cookie) — no change needed.
- The raw `volumes/audit/audit.log` briefly contains session tokens; restrict
  filesystem access to it. The SQLite db and viewer never store tokens.
- Back up `volumes/audit/audit.db` with your other volumes for retention.
