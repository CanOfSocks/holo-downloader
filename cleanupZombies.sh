#!/bin/sh

# Get the process IDs (PIDs) of zombie processes
zombie_pids=$(ps aux | awk '$8=="Z"{print $2}')
sleep 5
# Kill the zombie processes
for pid in $zombie_pids; do
    kill -9 $pid
done
