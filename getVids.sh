#!/bin/bash
lives=($(python /app/getJson.py "bash"))

echo "Getting: ${lives[*]}"
for id in ${lives[@]}; do
  exec /app/downloadVid.sh "${id}" &
done
