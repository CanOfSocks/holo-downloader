#!/bin/bash

PROCESS_NAME=$(basename "$0")
# Check if a process with the same script name is already running
COUNT=$(pgrep -f "$PROCESS_NAME $1" | wc -l)

# If more than one instance of the script, it is already running, so exit
if [ "$COUNT" -gt 2 ]; then
    echo "Downloader for $1 is already running, exiting... Count=$COUNT"
    exit 0
fi
python /app/downloadVid.py "$1"
