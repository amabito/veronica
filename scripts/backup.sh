#!/usr/bin/env bash
# scripts/backup.sh -- Backup PostgreSQL database and export Veronica data as JSON.
#
# Usage:
#   ./scripts/backup.sh [--output-dir DIR] [--api-url URL] [--api-key KEY]
#
# Environment variables (override defaults):
#   BACKUP_DIR      -- directory where backups are stored (default: ./backups)
#   VERONICA_URL    -- Veronica API base URL (default: http://localhost:8000)
#   VERONICA_API_KEY -- API key for authentication
#
# Requires: docker compose (for pg_dump), curl

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BACKUP_DIR="${BACKUP_DIR:-./backups}"
VERONICA_URL="${VERONICA_URL:-http://localhost:8000}"
VERONICA_API_KEY="${VERONICA_API_KEY:-}"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
DEST="${BACKUP_DIR}/${TIMESTAMP}"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir) BACKUP_DIR="$2"; DEST="${BACKUP_DIR}/${TIMESTAMP}"; shift 2 ;;
        --api-url)    VERONICA_URL="$2"; shift 2 ;;
        --api-key)    VERONICA_API_KEY="$2"; shift 2 ;;
        *) echo "ERROR: unknown argument $1" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
mkdir -p "${DEST}"
echo "[backup] Starting backup to ${DEST}"

# ---------------------------------------------------------------------------
# 1. PostgreSQL dump
# ---------------------------------------------------------------------------
echo "[backup] Running pg_dump via docker compose..."
if docker compose exec -T postgres pg_dump \
    --username=veronica \
    --dbname=veronica \
    --format=custom \
    --no-password \
    > "${DEST}/postgres.dump"; then
    echo "[backup] PostgreSQL dump: OK ($(du -sh "${DEST}/postgres.dump" | cut -f1))"
else
    echo "[backup] WARNING: pg_dump failed (veronica may use in-memory store only)" >&2
fi

# ---------------------------------------------------------------------------
# 2. JSON export via GET /export
# ---------------------------------------------------------------------------
echo "[backup] Exporting data from ${VERONICA_URL}/export ..."
CURL_ARGS=(
    --silent
    --show-error
    --fail
    --max-time 60
    --output "${DEST}/export.json"
)
if [[ -n "${VERONICA_API_KEY}" ]]; then
    CURL_ARGS+=(--header "X-Veronica-Key: ${VERONICA_API_KEY}")
fi
if curl "${CURL_ARGS[@]}" "${VERONICA_URL}/export"; then
    POLICY_COUNT=$(python3 -c "import json; d=json.load(open('${DEST}/export.json')); print(len(d.get('policies',[])))" 2>/dev/null || echo "?")
    EVENT_COUNT=$(python3 -c "import json; d=json.load(open('${DEST}/export.json')); print(len(d.get('events',[])))" 2>/dev/null || echo "?")
    echo "[backup] JSON export: OK (policies=${POLICY_COUNT}, events=${EVENT_COUNT})"
else
    echo "[backup] ERROR: Failed to export from ${VERONICA_URL}/export" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Write manifest
# ---------------------------------------------------------------------------
cat > "${DEST}/manifest.json" <<MANIFEST
{
  "timestamp": "${TIMESTAMP}",
  "veronica_url": "${VERONICA_URL}",
  "files": ["postgres.dump", "export.json", "manifest.json"]
}
MANIFEST

echo "[backup] Manifest written"
echo "[backup] Done: ${DEST}"
echo "  postgres.dump -- PostgreSQL custom-format dump"
echo "  export.json   -- Veronica policies + events (JSON)"
echo "  manifest.json -- Backup metadata"
