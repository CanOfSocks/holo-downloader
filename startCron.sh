#!/bin/sh

# Choose location for user crontab (BusyBox default is /etc/crontabs)
CRON_DIR="/etc/crontabs"
CRON_FILE="$CRON_DIR/$(whoami)"

# Ensure crontab directory exists
mkdir -p "$CRON_DIR"

# Function to recreate the cron file and add specified lines
recreate_cron_file() {
    echo "Recreating crontab for user $(whoami)..."

    # Initialize CRON_CONTENT variable
    CRON_CONTENT=$(cat <<END
PATH=/app:/usr/local/bin:/usr/bin
SHELL=/bin/sh
END
)

    CRON_CONTENT="$CRON_CONTENT"$'\n'"0 */3 * * * /usr/sbin/update-ca-certificates"

    if [ -n "${VIDEOSCHEDULE}" ]; then
        CRON_CONTENT="$CRON_CONTENT"$'\n'"${VIDEOSCHEDULE} /app/getVids.py --command 'spawn' --frequency '${VIDEOSCHEDULE}' > /proc/1/fd/1 2>/proc/1/fd/2"
    else
        CRON_CONTENT="$CRON_CONTENT"$'\n'"*/2 * * * * /app/getVids.py --command 'spawn' > /proc/1/fd/1 2>/proc/1/fd/2"
    fi

    if [ -n "${MEMBERSCHEDULE}" ]; then
        CRON_CONTENT="$CRON_CONTENT"$'\n'"${MEMBERSCHEDULE} /app/getMembers.py --command 'spawn' --frequency '${MEMBERSCHEDULE}' > /proc/1/fd/1 2>/proc/1/fd/2"
    else
        CRON_CONTENT="$CRON_CONTENT"$'\n'"*/5 * * * * /app/getMembers.py --command 'spawn' > /proc/1/fd/1 2>/proc/1/fd/2"
    fi

    if [ -n "${COMMUNITYSCHEDULE}" ]; then
        CRON_CONTENT="$CRON_CONTENT"$'\n'"${COMMUNITYSCHEDULE} /app/communityPosts.py > /proc/1/fd/1 2>/proc/1/fd/2"
    else 
        CRON_CONTENT="$CRON_CONTENT"$'\n'"0 */3 * * * /app/communityPosts.py > /proc/1/fd/1 2>/proc/1/fd/2"
    fi

    if [ -n "${UNARCHIVEDSCHEDULE}" ]; then
        CRON_CONTENT="$CRON_CONTENT"$'\n'"${UNARCHIVEDSCHEDULE} /app/getVids.py --command 'spawn' --unarchived --frequency '${UNARCHIVEDSCHEDULE}' > /proc/1/fd/1 2>/proc/1/fd/2"
    else
        CRON_CONTENT="$CRON_CONTENT"$'\n'"*/30 * * * * /app/getVids.py --command 'spawn' --unarchived > /proc/1/fd/1 2>/proc/1/fd/2"
    fi

    if [ -n "${UPDATEYTDLP}" ]; then
        CRON_CONTENT="$CRON_CONTENT"$'\n'"0 0 * * * /usr/local/bin/pip install -U yt-dlp && sed -i '/if fmt.get(\\'targetDurationSec\\'):$/,/    continue$/s/^/#/' \"\$(pip show yt-dlp | grep Location | awk '{print \$2}')/yt_dlp/extractor/youtube.py\""
    fi

    # Write crontab file
    echo "$CRON_CONTENT" > "$CRON_FILE"
    chmod 600 "$CRON_FILE"
    echo "Crontab written to $CRON_FILE"
}

main() {
    recreate_cron_file

    # Optional: startup notice
    python /app/discord_web.py '0' 'starting'

    echo "Starting busybox crond in foreground..."
    while true; do
        busybox crond -f -l 8 -c "$CRON_DIR"
        sleep 1
    done
}

main "$@"
