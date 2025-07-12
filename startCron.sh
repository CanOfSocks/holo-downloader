#!/bin/bash

set -e

USERNAME=holouser

# Function to recreate the cron file and add specified lines
recreate_cron_file() {
    # Initialize the CRON_CONTENT variable with static entries
    CRON_CONTENT=$(cat <<END
PATH=/app:/usr/local/bin:/usr/bin
SHELL=/bin/bash
#BASH_ENV=/root/project_env.sh
END
)
    CRON_CONTENT+=$'0 */3 * * * /usr/sbin/update-ca-certificates\n'
    # Append each schedule line to CRON_CONTENT if the corresponding environment variable is set
    if [ -n "${VIDEOSCHEDULE}" ]; then
        CRON_CONTENT+="${VIDEOSCHEDULE} /app/getVids.py --command 'spawn' --frequency '${VIDEOSCHEDULE}'> /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        CRON_CONTENT+="*/2 * * * * /app/getVids.py --command 'spawn' > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${MEMBERSCHEDULE}" ]; then
        CRON_CONTENT+="${MEMBERSCHEDULE} /app/getMembers.py --command 'spawn' --frequency '${MEMBERSCHEDULE}'> /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        CRON_CONTENT+="*/5 * * * * /app/getMembers.py --command 'spawn' > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${COMMUNITYSCHEDULE}" ]; then
        CRON_CONTENT+="${COMMUNITYSCHEDULE} /app/communityPosts.py > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else 
        CRON_CONTENT+="0 */3 * * * /app/communityPosts.py > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${UNARCHIVEDSCHEDULE}" ]; then
        CRON_CONTENT+="${UNARCHIVEDSCHEDULE} /app/getVids.py --command 'spawn' --unarchived --frequency '${UNARCHIVEDSCHEDULE}' > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        CRON_CONTENT+="*/30 * * * * /app/getVids.py --command 'spawn' '--unarchived' > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${UPDATEYTDLP}" ]; then
        CRON_CONTENT+="0 0 * * * /usr/local/bin/pip install -U yt-dlp && sed -i '/if fmt.get(\\'targetDurationSec\\'):$/,/    continue$/s/^/#/' \"\$(pip show yt-dlp | grep Location | awk '{print \$2}')/yt_dlp/extractor/youtube.py\""$'\n'
    fi
    
    #CRON_CONTENT+="@reboot python /app/discord_web.py '0' 'starting'"

    # Recreate the cron file with the specified content
    echo "$CRON_CONTENT" | crontab -
}

# Main entry point
main() {
    # Recreate the cron file with the specified lines
    recreate_cron_file
    python /app/discord_web.py '0' 'starting'
    # Start the cron daemon in the foreground
    while true; do
        cron -f
        sleep 1
    done
}

# Execute main function
if [[ -n "$PUID" && -n "$PGID" && -z "$GOSU_DONE" ]]; then
    echo "Detected PUID=$PUID and PGID=$PGID"

    if ! getent group "$USERNAME" > /dev/null; then
        groupadd -g "$PGID" "$USERNAME"
    fi

    if ! id -u "$USERNAME" > /dev/null 2>&1; then
        useradd -m -u "$PUID" -g "$PGID" -s /bin/bash "$USERNAME"
    fi

    echo "Switching to user $USERNAME"
    # Set marker to avoid re-entry and re-run script as user
    export GOSU_DONE=1
    exec gosu "$USERNAME" "$0" "$@"
else
    if [[ "$GOSU_DONE" == "1" ]]; then
        echo "Running as $USERNAME (already switched)"
    else
        echo "PUID/PGID not set — running as default container user"
    fi
    main "$@"
fi
