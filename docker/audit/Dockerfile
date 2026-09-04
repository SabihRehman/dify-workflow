# Small image for the audit sync loop + viewer (Python stdlib + psql client).
FROM python:3.12-slim

# psql client is used by the sync loop to resolve account ids -> names
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY enrich.py sync.py diff.py viewer.py /app/

# Overridden per-service in docker-compose (sync loop vs. viewer).
CMD ["python3", "viewer.py"]
