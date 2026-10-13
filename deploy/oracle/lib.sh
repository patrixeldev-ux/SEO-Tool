#!/usr/bin/env bash
# Shared helpers for the deploy/oracle scripts. Source it; do not run it.
# shellcheck shell=bash

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$ROOT_DIR/.env.prod"
COMPOSE_FILE="$ROOT_DIR/docker-compose.prod.yml"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# docker may need sudo right after installation (the docker group is not active in the current shell yet).
docker_cmd() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    sudo docker "$@"
  fi
}

# Docker Compose bound to the production file and settings.
dc() {
  [ -f "$ENV_FILE" ] || die "$ENV_FILE not found. Run deploy/oracle/install.sh first."
  GIT_SHA="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || true)" \
    docker_cmd compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

# env_get KEY [DEFAULT]: read one value from .env.prod without executing it.
env_get() {
  local value
  value="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  value="${value%\"}"
  value="${value#\"}"
  printf '%s' "${value:-${2:-}}"
}

# wait_healthy [SECONDS]: wait until the API container reports healthy.
wait_healthy() {
  local limit="${1:-600}" waited=0 state
  while [ "$waited" -lt "$limit" ]; do
    state="$(dc ps api --format '{{.Health}}' 2>/dev/null | head -n 1 || true)"
    [ "$state" = "healthy" ] && return 0
    sleep 5
    waited=$((waited + 5))
  done
  return 1
}
