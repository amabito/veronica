#!/usr/bin/env bash
# scripts/restore.sh -- Restore PostgreSQL database from a backup directory.
#
# Usage:
#   ./scripts/restore.sh <backup-dir>
#
# Example:
#   ./scripts/restore.sh ./backups/20260310T120000
#
# What this does:
#   1. Restores PostgreSQL from postgres.dump (pg_restore)
#   2. Reports the JSON export contents for reference
#
# Note: POST /import is not yet implemented. To re-load events and policies
# from export.json, use the Veronica API directly or replay the backup data.
#
# Requires: docker compose

set -euo pipefail

# ---------------------------------------------------------------------------
# Validate arguments
# ---------------------------------------------------------------------------
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <backup-dir>" >&2
    echo "Example: $0 ./backups/20260310T120000" >&2
    exit 1
fi

BACKUP_DIR="$1"

if [[ ! -d "${BACKUP_DIR}" ]]; then
    echo "ERROR: Backup directory not found: ${BACKUP_DIR}" >&2
    exit 1
fi

DUMP_FILE="${BACKUP_DIR}/postgres.dump"
EXPORT_FILE="${BACKUP_DIR}/export.json"
MANIFEST_FILE="${BACKUP_DIR}/manifest.json"

# ---------------------------------------------------------------------------
# Show manifest
# ---------------------------------------------------------------------------
if [[ -f "${MANIFEST_FILE}" ]]; then
    echo "[restore] Manifest:"
    cat "${MANIFEST_FILE}"
    echo ""
fi

# ---------------------------------------------------------------------------
# 1. Restore PostgreSQL
# ---------------------------------------------------------------------------
if [[ -f "${DUMP_FILE}" ]]; then
    echo "[restore] Restoring PostgreSQL from ${DUMP_FILE} ..."
    # Drop existing connections and recreate database
    docker compose exec -T postgres psql \
        --username=veronica \
        --dbname=postgres \
        --command="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='veronica' AND pid <> pg_backend_pid();" \
        2>/dev/null || true

    docker compose exec -T postgres dropdb \
        --username=veronica \
        --if-exists \
        veronica 2>/dev/null || true

    docker compose exec -T postgres createdb \
        --username=veronica \
        veronica

    docker compose exec -T postgres pg_restore \
        --username=veronica \
        --dbname=veronica \
        --no-password \
        --verbose \
        < "${DUMP_FILE}"

    echo "[restore] PostgreSQL restore: OK"
else
    echo "[restore] WARNING: ${DUMP_FILE} not found, skipping PostgreSQL restore" >&2
fi

# ---------------------------------------------------------------------------
# 2. Report JSON export contents
# ---------------------------------------------------------------------------
if [[ -f "${EXPORT_FILE}" ]]; then
    POLICY_COUNT=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(len(d.get('policies',[])))" "${EXPORT_FILE}" 2>/dev/null || echo "?")
    EVENT_COUNT=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(len(d.get('events',[])))" "${EXPORT_FILE}" 2>/dev/null || echo "?")
    echo "[restore] JSON export found: policies=${POLICY_COUNT}, events=${EVENT_COUNT}"
    echo "[restore] NOTE: POST /import is not yet implemented."
    echo "          To reload policies, use PUT /policies/{chain_id} for each entry in export.json."
else
    echo "[restore] WARNING: ${EXPORT_FILE} not found" >&2
fi

echo "[restore] Done"
