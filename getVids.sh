#!/bin/bash
lives=($(python /app/getJson.py))

echo "Getting: ${lives[*]}"
for id in ${lives[@]}; do
  exec /app/downloadVid.sh "${id}" &
done
