#!/bin/bash
PROCESS_NAME=$(basename "$0")
# Check if a process with the same script name is already running
COUNT=$(pgrep -c -f "$PROCESS_NAME $1")
#If more than one instance of script, it is already running so exit
if [ "$COUNT" -gt 1 ]; then
    echo "Downloader for $1 is already running, exiting...      Count=$COUNT"
    exit
fi

#Traps
trap "exit" INT TERM
trap "kill 0" EXIT



success="False"

tempdir="/app/temp"
donedir="/app/Done"

python /app/discord-web.py "$1" "waiting"

ytdlpOptions=$(python /app/getConfig.py "yt-dlp_options")
# Waits for the stream to begin and creates a variable with the stream title and ID
partialoutput=$(yt-dlp --wait-for-video 1-300 -R 25 --skip-download $ytdlpOptions --print "%(filename)s" --no-warnings "$1" 2>&1 | tail -n 1) || (python /app/discord-web.py "$1" && exit)
echo "$partialoutput"
python /app/discord-web.py "$1" "recording"
output="$tempdir/$partialoutput"

getChat=$(python /app/getConfig.py "get_chat")
if [[ "$getChat" == "True" ]]; then
        # Begin downloading live chat for the stream once it begins in parallel
        {
                cookies=$(python /app/getConfig.py "cookies")
                chat_downloader $cookies --logging critical -o "$output.live_chat.json" "https://www.youtube.com/watch?v=$1" 2>&1 > /dev/null || \
chat_downloader --logging critical -o "$output.live_chat.json" "https://www.youtube.com/watch?v=$1" 2>&1 > /dev/null || \
yt-dlp $cookies --wait-for-video 1-15 --write-sub --sub-lang "live_chat" --sub-format "json" --live-from-start --skip-download -o "$output" "$1" \
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
fi
infoOptions=$(python /app/getConfig.py "info_options")
if ! [ -z "${infoOptions}" ]; then
        # Download the metadata (.info.json) and thumbnail in parallel
        {
                yt-dlp --wait-for-video 1-15 -R 25 --live-from-start $infoOptions --skip-download -o "$output" "$1" \
&& (test -f "$output.info.json" && perl -pi -e "s/((?:[0-9]{1,3}\.){3}[0-9]{1,3})|((?:[a-f0-9]{1,4}:){7}[a-f0-9]{1,4})/0\.0\.0\.0/g" "$output.info.json") || echo "Error gathering video data (e.g. info.json) chat for $1"
        } &
        info_pid=$!
fi

#Download the video/audio (from the start), preferring VP9 codec
ytarchiveOptions=$(python /app/getConfig.py "ytarchive_options")
quality=$(python /app/getConfig.py "quality")
ytarchive $ytarchiveOptions --error --output "$output" "https://www.youtube.com/watch?v=$1" "$quality" \
&& success="True" || (python /app/discord-web.py "$1" "error" ; kill -2 $chat_pid $info_pid)

# Wait for all above processes to complete
wait

mux_file=$(python /app/getConfig.py "mux_file")
tempfolder=$(dirname "${tempdir}/${partialoutput}")
if [[ "$success" == "True" ]]; then
    echo "ytarchive for $1 was successful"
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

#Force check for output txt file because it doesn't sometimes for some reason
elif [ -s "$output.ffmpeg.txt" ] && [[ "$mux_file" == "False" ]]; then  
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
