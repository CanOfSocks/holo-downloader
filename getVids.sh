#!/bin/bash
lives=($(/app/getJson.py "bash"))

echo "Getting: ${lives[*]}"
for id in ${lives[@]}; do
  exec /app/downloadVid.py "${id}" &
done
