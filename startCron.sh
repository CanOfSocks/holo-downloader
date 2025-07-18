#!/bin/bash

set -e

USERNAME=holouser

# Function to recreate the cron file and add specified lines for a given user
recreate_cron_file() {
    local user="${1:-$(whoami)}"
    local cron_content
    cron_content=$(cat <<END
PATH=/app:/usr/local/bin:/usr/bin
SHELL=/bin/bash
#BASH_ENV=/root/project_env.sh
END
)
    cron_content+=$'\n0 */3 * * * su -c \"/usr/sbin/update-ca-certificates\" '$user' > /proc/1/fd/1 2>/proc/1/fd/2\n'

    if [ -n "${VIDEOSCHEDULE}" ]; then
        cron_content+="${VIDEOSCHEDULE} su -c \"/app/getVids.py --command 'spawn' --frequency '${VIDEOSCHEDULE}'\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        cron_content+="*/2 * * * * su -c \"/app/getVids.py --command 'spawn'\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${MEMBERSCHEDULE}" ]; then
        cron_content+="${MEMBERSCHEDULE} su -c \"/app/getMembers.py --command 'spawn' --frequency '${MEMBERSCHEDULE}'\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        cron_content+="*/5 * * * * su -c \"/app/getMembers.py --command 'spawn'\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${COMMUNITYSCHEDULE}" ]; then
        cron_content+="${COMMUNITYSCHEDULE} su -c \"/app/communityPosts.py\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else 
        cron_content+="0 */3 * * * su -c \"/app/communityPosts.py\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${UNARCHIVEDSCHEDULE}" ]; then
        cron_content+="${UNARCHIVEDSCHEDULE} su -c \"/app/getVids.py --command 'spawn' --unarchived --frequency '${UNARCHIVEDSCHEDULE}'\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        cron_content+="*/30 * * * * su -c \"/app/getVids.py --command 'spawn' --unarchived\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${UPDATEYTDLP}" ]; then
        cron_content+="0 0 * * * su -c \"/usr/local/bin/pip install -U yt-dlp && sed -i '/if fmt.get(\\\\'targetDurationSec\\\\'):$/,/    continue$/s/^/#/' \\\"\$(pip show yt-dlp | grep Location | awk '{print \\\$2}')/yt_dlp/extractor/youtube.py\\\"\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    echo "$cron_content" | crontab -
}


main() {
    if [[ -n "$PUID" && -n "$PGID" ]]; then
        # Create group if it doesn't exist
        if ! getent group "$PGID" >/dev/null; then
            groupadd -g "$PGID" "$USERNAME"
        fi

        # Create user if it doesn't exist
        if ! id -u "$USERNAME" >/dev/null 2>&1; then
            useradd -u "$PUID" -g "$PGID" -m -s /bin/bash "$USERNAME"
        fi

        chown "$PUID:$PGID" /app

        echo "Creating crontab for user $USERNAME"
        recreate_cron_file "$USERNAME"

        echo "Running discord_web.py as $USERNAME"
        su -c "python /app/discord_web.py '0' 'starting'" "$USERNAME"
    else
        echo "No PUID and PGID set, installing crontab for root"
        recreate_cron_file
        python /app/discord_web.py '0' 'starting'
    fi
    
    while true; do
        cron -f
        sleep 1
    done
}

main "$@"
