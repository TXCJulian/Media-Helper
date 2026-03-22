#!/bin/sh
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Adjust appuser UID/GID if needed
if [ "$(id -u appuser)" != "$PUID" ] || [ "$(id -g appuser)" != "$PGID" ]; then
    groupmod -o -g "$PGID" appgroup 2>/dev/null || true
    usermod -o -u "$PUID" -g "$PGID" appuser 2>/dev/null || true
    chown appuser:appgroup /data/cutter-jobs 2>/dev/null || true
    chown -R appuser:appgroup /var/lib/media-renamer 2>/dev/null || true
fi

# Grant GPU access: match host render/video group GIDs for /dev/dri
if [ -d /dev/dri ]; then
    for dev in /dev/dri/renderD* /dev/dri/card*; do
        [ -e "$dev" ] || continue
        dev_gid=$(stat -c '%g' "$dev")
        if ! id -G appuser | tr ' ' '\n' | grep -q "^${dev_gid}$"; then
            # Create or reuse a group with the device's GID, then add appuser
            grp_name=$(getent group "$dev_gid" | cut -d: -f1 || true)
            if [ -z "$grp_name" ]; then
                grp_name="devdri${dev_gid}"
                groupadd -g "$dev_gid" "$grp_name" 2>/dev/null || true
            fi
            usermod -a -G "$grp_name" appuser 2>/dev/null || true
        fi
    done
fi

exec gosu appuser "$@"
