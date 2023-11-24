#!/bin/sh

cleanup_zombies() {
    while true; do
        # Wait for and collect status of child processes
        wait -n
    done
}

# Run the zombie process cleanup in the background
cleanup_zombies &
