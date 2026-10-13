#!/usr/bin/env bash
# Follow the logs.   deploy/oracle/logs.sh            all services
#                    deploy/oracle/logs.sh api        one service: caddy api worker beat postgres redis
set -euo pipefail
# shellcheck source=deploy/oracle/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
dc logs -f --tail=200 "$@"
