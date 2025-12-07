#!/bin/sh
set -e

USERNAME=holouser
PUID=${PUID:-}
PGID=${PGID:-}

if [ -n "$PUID" ] && [ -n "$PGID" ]; then
  # create group if needed
  if ! getent group "$PGID" >/dev/null 2>&1; then
    addgroup -g "$PGID" "$USERNAME"
  fi

  # create user if needed
  if ! id -u "$USERNAME" >/dev/null 2>&1; then
    adduser -u "$PUID" -G "$USERNAME" -S -D -H -s /bin/bash "$USERNAME"
  fi

  # Fix ownership
  chown "$PUID:$PGID" /app /app/*
fi

python /app/discord_web.py '0' 'starting'

if [ -n "$PUID" ] && [ -n "$PGID" ]; then
  exec su - "$USERNAME" -c "python /app/web.py"
else
  exec python /app/web.py
fi
