#!/bin/sh
set -e

USERNAME=holouser
PUID=${PUID:-}
PGID=${PGID:-}

# 1. Check if PUID and PGID are set before attempting user/group management
if [ -n "$PUID" ] && [ -n "$PGID" ]; then
    # 2. Check if a group with the given PGID already exists
    if ! getent group "$PGID" >/dev/null 2>&1; then
        # Group doesn't exist, create it. Use a name based on the PGID.
        # This is more robust than trying to use the USERNAME as the group name
        # when a PGID is provided that doesn't match the username's default.
        GROUPNAME="holoweb-$PGID"
        addgroup -g "$PGID" "$GROUPNAME"
    else
        # Group exists, find its name.
        GROUPNAME=$(getent group "$PGID" | cut -d: -f1)
    fi

    # 3. Create the user with the specified PUID and the determined GROUPNAME
    if ! id -u "$USERNAME" >/dev/null 2>&1; then
        # -S: System account, -D: No password, -H: No home directory
        # The -G option in adduser (busybox) specifies the *main* group name, not the ID.
        adduser -u "$PUID" -G "$GROUPNAME" -S -D -H -s /bin/sh "$USERNAME"
    fi

    # Fix ownership
    # Use the group name (or ID) determined above for robustness
    chown "$PUID:$GROUPNAME" /app /app/*
fi

# Run the pre-start script as root (or the initial user)
python /app/discord_web.py '0' 'starting'

# Start the main application
if [ -n "$PUID" ] && [ -n "$PGID" ]; then
    # 4. Use 'su-exec' or 'gosu' for safe privilege drop in Alpine,
    # or a simpler busybox 'su' without a login shell.
    # The 'su -' logic is safer if the user has a shell configured.
    # /bin/sh is the default minimal shell on Alpine.
    exec su - "$USERNAME" -c "python /app/web.py"
else
    exec python /app/web.py
fi