#!/bin/sh
set -eu
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump="/tmp/management-${stamp}.dump"
pg_dump --format=custom --no-owner --file="$dump" "$DATABASE_URL"
restic backup "$dump" /app/media --tag management-bot
restic forget --keep-daily 7 --keep-weekly 5 --keep-monthly 12 --prune
rm -f "$dump"
