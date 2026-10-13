#!/bin/sh
set -eu
: "${1:?Usage: restore.sh /path/to/database.dump}"
pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" "$1"
