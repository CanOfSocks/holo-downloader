#!/bin/bash
PROCESS_NAME=$(basename "$0")
# Check if a process with the same script name is already running
COUNT=$(pgrep -c -f "$PROCESS_NAME $1")
#If more than one instance of script, it is already running so exit
if [ "$COUNT" -gt 1 ]; then
    #echo "Downloader for $1 is already running, exiting...      Count=$COUNT"
    return 0
    exit
fi
python /app/downloadVid.py "$1"
wait
