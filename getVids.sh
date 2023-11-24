#!/bin/bash
lives=($(python /app/getJson.py "bash"))

echo "Getting: ${lives[*]}"
for id in ${lives[@]}; do
  setsid /app/downloadVid.sh "${id}" &
done
