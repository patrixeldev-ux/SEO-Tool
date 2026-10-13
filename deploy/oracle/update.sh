#!/usr/bin/env bash
# Update Search OS: back up the database, pull the latest code, rebuild, migrate and restart.
# Data (database, Redis, certificates) lives in Docker volumes and is never touched.
#   deploy/oracle/update.sh              # pull the current branch, rebuild, restart
#   deploy/oracle/update.sh --no-pull    # only apply changes made to .env.prod (restart)
set -euo pipefail
# shellcheck source=deploy/oracle/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PULL=1
[ "${1:-}" = "--no-pull" ] && PULL=0
cd "$ROOT_DIR"

if [ "$PULL" -eq 1 ]; then
  log "Safety backup before updating"
  "$ROOT_DIR/deploy/oracle/backup.sh" || warn "Backup failed (is the stack running?). Continuing."
  branch="$(git rev-parse --abbrev-ref HEAD)"
  log "Pulling the latest code (branch $branch)"
  git fetch --quiet origin "$branch"
  git merge --ff-only --quiet "origin/$branch" ||
    die "Local changes in $ROOT_DIR block the update. Undo them (git status) and try again."
fi

log "Rebuilding and restarting (database migrations run automatically when the API starts)"
dc up -d --build --remove-orphans

log "Waiting for the API to become healthy"
wait_healthy 600 || { dc logs --tail=40 api >&2 || true; die "The API did not become healthy. Logs above."; }

docker_cmd image prune -f >/dev/null 2>&1 || true
log "Done. Version: $(git rev-parse --short HEAD)"
dc ps
