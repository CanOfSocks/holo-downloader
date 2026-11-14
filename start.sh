#!/bin/sh
set -e

echo "Holo-downloader commit: $COMMIT_HASH | Livestream_dl commit: $LIVESTREAM_DL_COMMIT_HASH"

# Optional local DNS cache
if [ "$DNSCACHE" = "true" ]; then
    UPSTREAMS=$(awk '/^nameserver/ && $2 != "127.0.0.1" {print $2}' /etc/resolv.conf)
    if [ -n "$UPSTREAMS" ]; then
        SERVER_ARGS=""
        for ns in $UPSTREAMS; do SERVER_ARGS="$SERVER_ARGS --server=$ns"; done

        if dnsmasq --listen-address=127.0.0.1 --bind-interfaces --no-resolv \
                   --cache-size="${DNSCACHE_SIZE:-1000}" \
                   --min-cache-ttl="${DNSCACHE_MIN_TTL:-60}" \
                   $SERVER_ARGS; then
            # Keep search/options lines, swap nameservers for the local cache
            grep -v '^nameserver' /etc/resolv.conf > /tmp/resolv.new
            echo "nameserver 127.0.0.1" >> /tmp/resolv.new
            cat /tmp/resolv.new > /etc/resolv.conf
            echo "DNS cache enabled (upstream: $UPSTREAMS)"
        else
            echo "dnsmasq failed to start, continuing without DNS cache"
        fi
    fi
fi

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
    python -m pip install --disable-pip-version-check --root-user-action "ignore" --quiet --no-cache-dir --pre -U yt-dlp[default]
fi

export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/app/.cache}"
mkdir -p "$XDG_CACHE_HOME"

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
    chown -R "$PUID:$PGID" "$XDG_CACHE_HOME"
fi

# The "starting" Discord notification is sent by web.py when the app starts
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