#!/bin/bash

# Function to recreate the cron file and add specified lines
recreate_cron_file() {
    # Initialize the CRON_CONTENT variable with static entries
    CRON_CONTENT=$(cat <<END
PATH=/app:/usr/local/bin:/usr/bin
SHELL=/bin/bash
#BASH_ENV=/root/project_env.sh
END
)
    CRON_CONTENT+=$'\n'
    # Append each schedule line to CRON_CONTENT if the corresponding environment variable is set
    if [ -n "${VIDEOSCHEDULE}" ]; then
        CRON_CONTENT+="${VIDEOSCHEDULE} /app/getVids.py 'spawn' --frequency '${VIDEOSCHEDULE}'> /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        CRON_CONTENT+="*/2 * * * * /app/getVids.py 'spawn' > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${MEMBERSCHEDULE}" ]; then
        CRON_CONTENT+="${MEMBERSCHEDULE} /app/getMembers.py 'spawn' --frequency '${MEMBERSCHEDULE}'> /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        CRON_CONTENT+="*/5 * * * * /app/getMembers.py 'spawn' > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${COMMUNITYSCHEDULE}" ]; then
        CRON_CONTENT+="${COMMUNITYSCHEDULE} /app/communityPosts.py > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else 
        CRON_CONTENT+="0 */3 * * * /app/communityPosts.py > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    if [ -n "${UNARCHIVEDSCHEDULE}" ]; then
        CRON_CONTENT+="${UNARCHIVEDSCHEDULE} /app/getVids.py 'spawn' --unarchived --frequency '${UNARCHIVEDSCHEDULE}' > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    else
        CRON_CONTENT+="*/30 * * * * /app/getVids.py 'spawn' '--unarchived' > /proc/1/fd/1 2>/proc/1/fd/2"$'\n'
    fi

    CRON_CONTENT+="@reboot python /app/discord_web.py '0' 'starting'"

    # Recreate the cron file with the specified content
    echo "$CRON_CONTENT" | crontab -
}

# Main entry point
main() {
    # Recreate the cron file with the specified lines
    recreate_cron_file

    # Start the cron daemon in the foreground
    cron -f
}

# Execute main function
main "$@"
