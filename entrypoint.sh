#!/bin/bash

set -e

USERNAME=holouser

# Only create user and switch if both PUID and PGID are set
if [[ -n "$PUID" && -n "$PGID" ]]; then
    # Create group if it doesn't exist
    if ! getent group "$PGID" >/dev/null; then
        groupadd -g "$PGID" "$USERNAME"
    fi

    # Create user if it doesn't exist
    if ! id -u "$PUID" >/dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -m -s /bin/bash "$USERNAME"
    fi

    # Optional: Fix permissions
    chown "$PUID:$PGID" /app
    # Run command as created user
    echo "Running /app/startCron.sh as $USERNAME (UID=$PUID)"
    su -c "/app/startCron.sh" "$USERNAME"
else
    # Run command as current user (probably root)
    echo "No user specified, running as root"
    "/app/startCron.sh"
fi

