#!/bin/bash
script_name="$0"

# Check if a process with the same script name is already running
if pgrep -f "$script_name $*" | grep -v $$; then
    echo "Downloader for $1 is already running, exiting..."
    exit
fi

success="false"

tempdir="/app/temp"
donedir="/app/Done"

python /app/discord-web.py "$1" "waiting"
# Waits for the stream to begin and creates a variable with the stream title and ID
partialoutput=$(yt-dlp --cookies /app/cookies.txt --wait-for-video 1-300 --skip-download -o "%(channel)s/[%(upload_date)s] %(title)s - %(channel)s (%(id)s)/[%(upload_date)s] %(title)s - %(channel)s (%(id)s)" --print "%(filename)s" --no-warnings "$1" 2>&1 | tail -n 1) || (python /app/discord-web.py "$1" && exit)

python /app/discord-web.py "$1" "recording"
output="$tempdir/$partialoutput"
# Begin downloading live chat for the stream once it begins in parallel
{
        yt-dlp --cookies /app/cookies.txt --wait-for-video 1-15 --write-sub --sub-lang "live_chat" --sub-format "json" --live-from-start --skip-download -o "$output" "$1"
        # If the stream is privated at the end (or some other event cuts your access to the stream),
        # the chat file will not be closed correctly, and the .part extension can be removed
        if [[ -f "$output.live_chat.json.part" ]]
        then
                mv "$output.live_chat.json.part" "$output.live_chat.json"
        fi
        # Compress the live_chat.json file, this can save 80-90% of the space usually taken by these files
        zip -9 -m "$(printf '%s\n' '$output.live_chat.json' | sed 's/\.json//').zip" "$output.live_chat.json"
} &
# Download the metadata (.info.json) and thumbnail in parallel
{
        yt-dlp --cookies /app/cookies.txt --wait-for-video 1-15 --live-from-start --write-info-json --write-thumbnail --convert-thumbnails png --write-description --skip-download -o "$output" "$1" \
&& perl -pi -e "s/((?:[0-9]{1,3}\.){3}[0-9]{1,3})|((?:[a-f0-9]{1,4}:){7}[a-f0-9]{1,4})/0\.0\.0\.0/g" "$output.info.json"
} &
# Download the video/audio (from the start), preferring VP9 codec
ytarchive --cookies /app/cookies.txt -t --vp9 --retry-stream 15 --threads 4 --no-frag-files --output "$output" "https://www.youtube.com/watch?v=$1" "best" \
&& success="true" || python /app/discord-web.py "$1" "error"
# Wait for all above processes to complete
wait


tempfolder="${tempdir}/${partialoutput%/*}"
if [ "$success" = "true" ]; then
#    tempfolder="${tempdir}/${partialoutput%/*}"
    outfolder="${donedir}/${partialoutput%%/*}"
    mkdir -p "${donedir}/${partialoutput%/*}"
    sleep 10
    mv -f "${tempfolder}" "${outfolder}" && python /app/discord-web.py "$1" "done" || python /app/discord-web.py "$1" "error"
    
    #Give API some time to update after a success
    sleep 50
fi
rm -R "$tempfolder"
