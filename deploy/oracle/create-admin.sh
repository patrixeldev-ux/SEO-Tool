#!/usr/bin/env bash
# Create an account (a new organisation with you as its owner). Asks for the password; nothing is stored on screen.
#   deploy/oracle/create-admin.sh                       asks for email and organisation
#   deploy/oracle/create-admin.sh you@example.com "My Agency"
set -euo pipefail
# shellcheck source=deploy/oracle/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

email="${1:-}"
org="${2:-}"
[ -n "$email" ] || read -rp "Email address: " email
[ -n "$org" ] || read -rp "Organisation name: " org
[ -t 0 ] || die "Run this in a terminal (it asks for the password)."
dc exec api python -m searchos.create_user --email "$email" --org "$org"
