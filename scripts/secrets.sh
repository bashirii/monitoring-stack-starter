#!/usr/bin/env bash
# Creates the two password files the stack needs, once. They are random, kept out of git, and readable only by you.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p secrets
for name in postgres_password grafana_admin_password; do
  if [ ! -s "secrets/$name" ]; then
    # openssl output has no newline problem: a password file must have none at the end
    openssl rand -hex 24 | tr -d '\n' > "secrets/$name"
    echo "created secrets/$name"
  fi
done
# The containers read these files as other users, so the folder is closed and the files are readable.
chmod 700 secrets
chmod 644 secrets/postgres_password secrets/grafana_admin_password
echo "Grafana login: admin / $(cat secrets/grafana_admin_password)"
