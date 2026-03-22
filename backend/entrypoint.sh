#!/bin/sh
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Adjust appuser UID/GID if needed
if [ "$(id -u appuser)" != "$PUID" ] || [ "$(id -g appuser)" != "$PGID" ]; then
    groupmod -o -g "$PGID" appgroup 2>/dev/null || true
    usermod -o -u "$PUID" -g "$PGID" appuser 2>/dev/null || true
    chown appuser:appgroup /data/cutter-jobs 2>/dev/null || true
fi

exec gosu appuser "$@"
