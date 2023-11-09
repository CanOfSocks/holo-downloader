#!/bin/bash
python /app/discord-web.py "0" "starting"
while true
do
  /app/getVids.sh
  sleep 120
done
