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
start_app() {
    local user="${1:-$(whoami)}"
    local cron_content
    su -c "${UMASK:+umask $UMASK;} python /app/web.py" $user
}


main() {
    while true; do
        echo "[$(date)] Starting app"
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
            
            su -c "python /app/discord_web.py '0' 'starting'" "$USERNAME"
            start_app "$USERNAME"
        else
            echo "No PUID and PGID set, installing crontab for root"
            
            python /app/discord_web.py '0' 'starting'
            start_app
        fi
        sleep 1        
    done
}

main "$@"
