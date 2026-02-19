#!/bin/sh
set -e

USERNAME=holouser
PUID=${PUID:-}
PGID=${PGID:-}

# Gunicorn Settings
APP_MODULE="web:app"
PORT=${PORT:-5000}    
WORKERS=1
THREADS=10

# Only run the check and patch logic if UPDATEYTDLP is set to "true"
if [ "$UPDATEYTDLP" = "true" ]; then
    python -m pip install --disable-pip-version-check --root-user-action "ignore" --quiet --no-cache-dir --pre -U yt-dlp
fi

# Alpine-specific User/Group logic
if [ -n "$PUID" ] && [ -n "$PGID" ]; then
    # Check if group GID already exists in /etc/group
    EXISTING_GROUP=$(grep ":x:$PGID:" /etc/group | cut -d: -f1)
    
    if [ -z "$EXISTING_GROUP" ]; then
        GROUPNAME="holoweb-$PGID"
        addgroup -g "$PGID" "$GROUPNAME"
    else
        GROUPNAME="$EXISTING_GROUP"
    fi

    # Check if user exists
    if ! id -u "$USERNAME" >/dev/null 2>&1; then
        # -D: Don't assign password
        # -G: Add to group
        # -u: User ID
        # -s: Shell
        adduser -u "$PUID" -G "$GROUPNAME" -s /bin/sh -D "$USERNAME"
    fi

    chown "$PUID:$PGID" /app /app/*
fi

# Run pre-start script
python /app/discord_web.py '0' 'starting'

mkdir -p ~/.cache && chmod -R 777 ~/.cache

cd /app

# Define Gunicorn arguments
GUNICORN_ARGS=" \
    -c /app/gunicorn.conf.py \
    --bind 0.0.0.0:$PORT \
    --workers $WORKERS \
    --threads $THREADS \
    --worker-tmp-dir /dev/shm \
    --timeout 0 \
    --keep-alive 30 \
    --access-logfile - \
    --error-logfile - "

if [ -n "$UMASK" ]; then
    umask "$UMASK"
    GUNICORN_ARGS="$GUNICORN_ARGS --umask $UMASK"
fi

# Append user/group if variables are set
if [ -n "$PUID" ]; then
    GUNICORN_ARGS="$GUNICORN_ARGS --user $PUID"
fi

if [ -n "$PGID" ]; then
    GUNICORN_ARGS="$GUNICORN_ARGS --group $PGID"
fi

GUNICORN_ARGS="$GUNICORN_ARGS $APP_MODULE"

exec gunicorn $GUNICORN_ARGS