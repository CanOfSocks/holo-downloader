#!/bin/bash
script_name="$0"

# Check if a process with the same script name is already running
if (pgrep -f "$script_name $*" | grep -v $$) > /dev/null; then
    #echo "Downloader for $1 is already running, exiting..."
    exit
fi
#Traps
trap "exit" INT TERM
trap "kill 0" EXIT



success="false"

tempdir="/app/temp"
donedir="/app/Done"

python /app/discord-web.py "$1" "waiting"
# Waits for the stream to begin and creates a variable with the stream title and ID
partialoutput=$(yt-dlp --cookies /app/cookies.txt --wait-for-video 1-300 -R 25 --skip-download -o "%(channel)s/[%(upload_date)s] %(fulltitle)s - %(channel)s (%(id)s)/[%(upload_date)s] %(fulltitle)s - %(channel)s (%(id)s)" --print "%(filename)s" --no-warnings "$1" 2>&1 | tail -n 1) || (python /app/discord-web.py "$1" && exit)

python /app/discord-web.py "$1" "recording"
output="$tempdir/$partialoutput"
# Begin downloading live chat for the stream once it begins in parallel
{
        chat_downloader --cookies /app/cookies.txt --logging critical -o "$output.live_chat.json" "https://www.youtube.com/watch?v=$1" 2>&1 > /dev/null || \
chat_downloader --logging critical -o "$output.live_chat.json" "https://www.youtube.com/watch?v=$1" 2>&1 > /dev/null || \
yt-dlp --cookies /app/cookies.txt --wait-for-video 1-15 --write-sub --sub-lang "live_chat" --sub-format "json" --live-from-start --skip-download -o "$output" "$1" \
|| echo "Error downloading chat for $1"
        # If the stream is privated at the end (or some other event cuts your access to the stream),
        # the chat file will not be closed correctly, and the .part extension can be removed
        if [[ -f "$output.live_chat.json.part" ]]
        then
                mv "$output.live_chat.json.part" "$output.live_chat.json"
        fi
        # Compress the live_chat.json file, this can save 80-90% of the space usually taken by these files
        zip -9 -m "$output.live_chat.zip" "$output.live_chat.json"  || echo "Error compressing chat for $1"
} &
chat_pid=$!
# Download the metadata (.info.json) and thumbnail in parallel
{
        yt-dlp --cookies /app/cookies.txt --wait-for-video 1-15 -R 25 --live-from-start --write-info-json --write-thumbnail --convert-thumbnails png --write-description --skip-download -o "$output" "$1" \
&& perl -pi -e "s/((?:[0-9]{1,3}\.){3}[0-9]{1,3})|((?:[a-f0-9]{1,4}:){7}[a-f0-9]{1,4})/0\.0\.0\.0/g" "$output.info.json" || echo "Error gathering video data (e.g. info.json) chat for $1"
} &
info_pid=$!
mux_file=$(python -c 'from config import mux_file
print(mux_file)')
# Download the video/audio (from the start), preferring VP9 codec
if [[ "$mux_file" == "False" ]]; then
    ytarchive --cookies /app/cookies.txt -t --vp9 --retry-stream 15 --threads 4 --add-metadata --no-frag-files --write-mux-file --error --output "$output" "https://www.youtube.com/watch?v=$1" "best" \
&& success="true" || (python /app/discord-web.py "$1" "error" ; kill -2 $chat_pid $info_pid)
else
    ytarchive --cookies /app/cookies.txt -t --vp9 --retry-stream 15 --threads 4 --add-metadata --no-frag-files --error --output "$output" "https://www.youtube.com/watch?v=$1" "best" \
&& success="true" || (python /app/discord-web.py "$1" "error" ; kill -2 $chat_pid $info_pid)
fi

# Wait for all above processes to complete
wait


tempfolder=$(dirname "${tempdir}/${partialoutput}")
if [ "$success" = "true" ]; then
#    tempfolder="${tempdir}/${partialoutput%/*}"
    outfolder=$(dirname "${donedir}/${partialoutput}")
    #Make parent folder
    parent=$(dirname "${outfolder}")
    mkdir -p "$parent"
    sleep 10
    echo "Moving ${tempfolder} to ${outfolder}"
    mv -f "${tempfolder}" "${outfolder}" && python /app/discord-web.py "$1" "done" || python /app/discord-web.py "$1" "error"
    
    #Give API some time to update after a success
    sleep 50
fi
rm -R "$tempfolder"
