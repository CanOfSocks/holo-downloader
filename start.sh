#!/bin/sh
set -e

USERNAME=holouser
PUID=${PUID:-}
PGID=${PGID:-}

if [ -n "$PUID" ] && [ -n "$PGID" ]; then
    # If group with PGID doesn't exist, create it
    if ! getent group "$PGID" >/dev/null 2>&1; then
        GROUPNAME="holoweb-$PGID"
        groupadd -g "$PGID" "$GROUPNAME"
    else
        GROUPNAME=$(getent group "$PGID" | cut -d: -f1)
    fi

    # Create user if not exists
    if ! id -u "$USERNAME" >/dev/null 2>&1; then
        # -M: do not create home directory; -N: do not create group with same name; -s: shell
        useradd -u "$PUID" -g "$PGID" -M -N -s /bin/sh "$USERNAME"
    fi

    # Fix ownership of /app (and its contents) — adjust as needed for nested dirs
    chown -R "$PUID:$PGID" /app
fi

# Run pre-start script as root (or the initial user)
python /app/discord_web.py '0' 'starting'

# Start the main application
if [ -n "$PUID" ] && [ -n "$PGID" ]; then
    # 4. Use 'su-exec' or 'gosu' for safe privilege drop in Alpine,
    # or a simpler busybox 'su' without a login shell.
    # The 'su -' logic is safer if the user has a shell configured.
    # /bin/sh is the default minimal shell on Alpine.
    exec su "$USERNAME" -c "python /app/web.py"
else
    exec python /app/web.py
fi