#!/bin/bash
set -e

USERNAME=holouser

# Optionally read from env
PUID=${PUID:-}
PGID=${PGID:-}

# If PUID/PGID are defined, ensure group & user exist
if [[ -n "$PUID" && -n "$PGID" ]]; then
  # create group if needed
  if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -g "$PGID" "$USERNAME"
  fi

  # create user if needed
  if ! id -u "$USERNAME" >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -m -s /bin/bash "$USERNAME"
  fi

  # Fix ownership of /app
  chown -R "$PUID:$PGID" /app
fi

# (Optional) any pre-start hooks, e.g. notifications
python /app/discord_web.py '0' 'starting'

# Finally: execute the main app as $USERNAME (or root if no PUID/PGID)
if [[ -n "$PUID" && -n "$PGID" ]]; then
  exec su -c "python /app/web.py" "$USERNAME"
else
  exec python /app/web.py
fi
