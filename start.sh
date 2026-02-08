#!/bin/sh
set -e

USERNAME=holouser
PUID=${PUID:-}
PGID=${PGID:-}

# Gunicorn Settings
APP_MODULE="web:app"  # 'filename_without_py:flask_variable_name'
PORT=${PORT:-5000}    
# We use 1 worker to preserve Global State/Scheduler
# We use multiple threads to keep the UI responsive
WORKERS=1
THREADS=10

# 1. Capture the current version
OLD_VER=$(python -m pip show yt-dlp | awk '/Version:/ {print $2}')

# 2. Run the update
python -m pip install --disable-pip-version-check --root-user-action "ignore" --quiet --no-cache-dir --pre -U yt-dlp

# 3. Capture the new version
NEW_VER=$(python -m pip show yt-dlp | awk '/Version:/ {print $2}')

# 4. Compare and run sed if they differ
if [ "$OLD_VER" != "$NEW_VER" ]; then
    echo "yt-dlp updated from $OLD_VER to $NEW_VER. Patching files..."
    
    sed -i "s/socs.value.startswith('CAA')/str(socs).startswith('CAA')/g" /usr/local/lib/python*/site-packages/chat_downloader/sites/youtube.py
    
    YT_PATH=$(pip show yt-dlp | awk '/Location/ {print $2}')
    sed -i "/if[[:space:]]\+fmt_stream\.get('targetDurationSec'):/,/^[[:space:]]*continue/s/^[[:space:]]*/&#/" "$YT_PATH/yt_dlp/extractor/youtube/_video.py"
fi

if [ -n "$PUID" ] && [ -n "$PGID" ]; then
    if ! getent group "$PGID" >/dev/null 2>&1; then
        GROUPNAME="holoweb-$PGID"
        groupadd -g "$PGID" "$GROUPNAME"
    else
        GROUPNAME=$(getent group "$PGID" | cut -d: -f1)
    fi

    if ! id -u "$USERNAME" >/dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -m -N -s /bin/sh "$USERNAME"
    fi

    chown "$PUID:$PGID" /app /app/*
fi

# Run pre-start script
python /app/discord_web.py '0' 'starting'



cd /app

# Start the main application with Gunicorn
# --timeout 0: Disables workers being killed for taking too long (essential for downloads)
# --access-logfile / --error-logfile -: Sends logs to Docker stdout/stderr

# Define base arguments
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