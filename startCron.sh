#!/bin/bash

# Function to recreate the cron file and add specified lines
recreate_cron_file() {
    # Define the content to be added to the cron file
    CRON_CONTENT=$(cat <<END
PATH=/app:/usr/local/bin:/usr/bin
SHELL=/bin/bash
#BASH_ENV=/root/project_env.sh
${VIDEOSCHEDULE:='*/2 * * * *'} /app/getVids.py "spawn" > /proc/1/fd/1 2>/proc/1/fd/2
${MEMBERSCHEDULE:='*/5 * * * *'} /app/getMembers.py "spawn" > /proc/1/fd/1 2>/proc/1/fd/2
@reboot python /app/discord_web.py "0" "starting"
END
)

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
