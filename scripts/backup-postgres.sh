#!/usr/bin/env bash
# OpenKhazana PostgreSQL Automated Backup Script (14-day rolling retention)
# Add to VPS crontab (daily at 03:30 UTC):
#   30 3 * * * /opt/openkhazana/scripts/backup-postgres.sh >> /var/log/openkhazana-backup.log 2>&1

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/openkhazana/backups}"
COMPOSE_FILE="${COMPOSE_FILE:-/opt/openkhazana/docker-compose.prod.yml}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
POSTGRES_USER="${POSTGRES_USER:-openkhazana}"
POSTGRES_DB="${POSTGRES_DB:-openkhazana}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +"%Y%m%d_%H%M%S")"
OUT_FILE="$BACKUP_DIR/openkhazana_${STAMP}.sql.gz"

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Starting PostgreSQL backup -> $OUT_FILE"
docker compose -f "$COMPOSE_FILE" exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl | gzip -9 > "$OUT_FILE"

if [ ! -s "$OUT_FILE" ]; then
  echo "ERROR: Backup file is empty!" >&2
  rm -f "$OUT_FILE"
  exit 1
fi

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Backup complete ($(du -h "$OUT_FILE" | cut -f1)). Pruning files older than ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -type f -name "openkhazana_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete
echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Done."
