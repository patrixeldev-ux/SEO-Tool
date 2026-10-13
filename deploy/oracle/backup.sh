#!/usr/bin/env bash
# Dump the database to ~/searchos-backups/searchos-YYYYMMDD-HHMMSS.sql.gz and keep the newest 14.
# Daily at 03:17 (add with `crontab -e`):
#   17 3 * * * /home/ubuntu/seo-tool/deploy/oracle/backup.sh >> /home/ubuntu/searchos-backups/backup.log 2>&1
# Restore:  gunzip -c FILE.sql.gz | docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T postgres psql -U searchos searchos
set -euo pipefail
# shellcheck source=deploy/oracle/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BACKUP_DIR="${BACKUP_DIR:-$HOME/searchos-backups}"
KEEP="${KEEP:-14}"
user="$(env_get POSTGRES_USER searchos)"
db="$(env_get POSTGRES_DB searchos)"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
tmp="$BACKUP_DIR/.searchos-$stamp.sql.gz.tmp"
out="$BACKUP_DIR/searchos-$stamp.sql.gz"

if ! dc exec -T postgres pg_dump -U "$user" -d "$db" --no-owner | gzip -9 >"$tmp"; then
  rm -f "$tmp"
  die "pg_dump failed."
fi
gzip -t "$tmp" || { rm -f "$tmp"; die "The backup file is damaged."; }
[ "$(wc -c <"$tmp")" -gt 200 ] || { rm -f "$tmp"; die "The backup is empty."; }
mv "$tmp" "$out"
chmod 600 "$out"

# Keep the newest $KEEP backups.
# shellcheck disable=SC2012
ls -1t "$BACKUP_DIR"/searchos-*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f --
log "Backup written: $out ($(du -h "$out" | cut -f1))"
