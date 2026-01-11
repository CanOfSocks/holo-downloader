#!/bin/sh
set -e

USERNAME=holouser
PUID=${PUID:-}
PGID=${PGID:-}

# 1. Setup User/Group
if [ -n "$PUID" ] && [ -n "$PGID" ]; then
    if ! getent group "$PGID" >/dev/null 2>&1; then
        groupadd -g "$PGID" "holoweb-$PGID"
    fi
    if ! id -u "$USERNAME" >/dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -M -N -s /bin/sh "$USERNAME"
    fi
    chown "$PUID:$PGID" /app /app/*
fi

# 2. Signal Handling (The Proxy)
handle_signals() {
    echo "[Entrypoint] Caught signal, forwarding to Python..." >&1
    kill -TERM "$child_pid" 2>/dev/null
}
trap handle_signals TERM INT

# 3. Pre-start (runs as root/current user)
python /app/discord_web.py '0' 'starting' >&1 2>&2

# 4. Build the command string using Shell Expansion
# ${UMASK:+umask $UMASK && } means: If UMASK exists, add the command and the '&&'
RUN_CMD="${UMASK:+umask $UMASK && }exec python /app/web.py"

# 5. Execute and Background
if [ -n "$PUID" ] && [ -n "$PGID" ]; then
    su -s /bin/sh -c "$RUN_CMD" "$USERNAME" >&1 2>&2 &
else
    sh -c "$RUN_CMD" >&1 2>&2 &
fi

child_pid=$!

# 6. Wait for the Python process
# Redirecting wait output to stdout/stderr of PID 1
wait "$child_pid" >&1 2>&2 || true

echo "[Entrypoint] Shutdown complete." >&1