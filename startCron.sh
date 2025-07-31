#!/bin/bash

set -e

USERNAME=holouser

# Function to send SIGTERM to all processes except this one
terminate() {
    echo "Caught SIGTERM. Terminating all child processes..."
    kill -TERM $(ps -eo pid | grep -v '^ *1$' | grep -v '^ *PID' | tr -d ' ') 2>/dev/null
    wait
}

# Trap SIGTERM and SIGINT
trap terminate SIGTERM SIGINT

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
    cron_content+=$'\n'$"0 */3 * * * su -c \"/usr/sbin/update-ca-certificates\" '$user' > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'

    if [ -n "${VIDEOSCHEDULE}" ]; then
        cron_content+="${VIDEOSCHEDULE} su -c \"${UMASK:+umask $UMASK;} python /app/getVids.py --command 'spawn' --frequency '${VIDEOSCHEDULE}'\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        cron_content+="*/2 * * * * su -c \"${UMASK:+umask $UMASK;} python /app/getVids.py --command 'spawn'\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${MEMBERSCHEDULE}" ]; then
        cron_content+="${MEMBERSCHEDULE} su -c \"${UMASK:+umask $UMASK;} python /app/getMembers.py --command 'spawn' --frequency '${MEMBERSCHEDULE}'\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        cron_content+="*/5 * * * * su -c \"${UMASK:+umask $UMASK;} python /app/getMembers.py --command 'spawn'\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${COMMUNITYSCHEDULE}" ]; then
        cron_content+="${COMMUNITYSCHEDULE} su -c \"${UMASK:+umask $UMASK;} python /app/communityPosts.py\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else 
        cron_content+="0 */3 * * * su -c \"${UMASK:+umask $UMASK;} python /app/communityPosts.py\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${UNARCHIVEDSCHEDULE}" ]; then
        cron_content+="${UNARCHIVEDSCHEDULE} su -c \"${UMASK:+umask $UMASK;} python /app/getVids.py --command 'spawn' --unarchived --frequency '${UNARCHIVEDSCHEDULE}'\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        cron_content+="*/30 * * * * su -c \"${UMASK:+umask $UMASK;} python /app/getVids.py --command 'spawn' --unarchived\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${UPDATEYTDLP}" ]; then
        cron_content+="0 0 * * * su -c \"pip install -U yt-dlp && sed -i '/if fmt.get(\\\\'targetDurationSec\\\\'):$/,/    continue$/s/^/#/' \\\"\$(pip show yt-dlp | grep Location | awk '{print \\\$2}')/yt_dlp/extractor/youtube.py\\\"\" $user > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
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

        chown "$PUID:$PGID" /app /app/*

        echo "Creating crontab for user $USERNAME"
        recreate_cron_file "$USERNAME"

        su -c "python /app/discord_web.py '0' 'starting'" "$USERNAME"
    else
        echo "No PUID and PGID set, installing crontab for root"
        recreate_cron_file
        python /app/discord_web.py '0' 'starting'
    fi
    
    
    while true; do
        echo "[$(date)] Starting cron"
        cron -f
        sleep 1
    done
}

main "$@"
