# Dictionary of channel IDs you want to match with descriptive names
channel_ids_to_match = {
    "Gawr Gura Ch. hololive-EN": "UCoSrY_IQQVpmIRZ9Xf-y93g",
    "Watson Amelia Ch. hololive-EN": "UCyl1z3jo3XHR1riLFKG5UAg",
    "Mori Calliope Ch. hololive-EN": "UCL_qhgtOy0dy1Agp8vkySQg",
    
    "Fauna": "UCO_aKKYxn4tvrqPjcTzZ6EQ"
    
    # Add more channels as needed
}

title_filter = {
    "UCoSrY_IQQVpmIRZ9Xf-y93g": "(?i).asmr|unarchive|karaoke|unarchived|no archive|WATCH-A-LONG|WATCHALONG|watch-along|birthday|offcollab|off-collab|off collab|SINGING.",
}

description_filter = {
    "UCoSrY_IQQVpmIRZ9Xf-y93g": ".Calliope.",
}

members_only ={
    "Gawr Gura Ch. hololive-EN": "UCoSrY_IQQVpmIRZ9Xf-y93g",
}

community_tab ={
    "Gawr Gura Ch. hololive-EN": "UCoSrY_IQQVpmIRZ9Xf-y93g",
}

webhook_url = "https://discord.com/api/webhooks/xxxxxxxxxxxxxxxx/yyyyyyyyyyyyyyyyyyyy"

unarchived_channel_ids_to_match = {
    "Ninomae Ina'nis Ch. hololive-EN": "UCMwGHR0BTZuLsmjY_NT5Pwg",
}

### Video fetch method

fetch_method = "ytdlp"

### Download options

# Output folder needs a depth of at least 2 (a slash for directory should be present in this variable)
# This should be in yt-dlp format (https://github.com/yt-dlp/yt-dlp#output-template)
# If not detected, the given template will be duplicated e.g. %(fulltitle)s/%(fulltitle)s
output_folder = "%(channel)s/[%(upload_date)s] %(fulltitle)s - %(channel)s (%(id)s)/[%(upload_date)s] %(fulltitle)s - %(channel)s (%(id)s)"

mux_file = True

download_threads = 4

# Extra options for ytarchive
ytarchive_options = "--vp9 --add-metadata --no-frag-files"

video_quality = "best"

# Only get video, overwrites all other settings in this section, defaults match github config.py
video_only = False

download_chat = True

thumbnail = True

info_json = True
remove_ip = True

description = True



### Function options
#Hours to start waiting for video to go live, otherwise ignore
look_ahead = 48

# Cookies file location relative to inside the container
cookies_file = "/app/cookies.txt"

### Torrent options
torrent = True

torrentOptions = [
    '-c', 'CanOfSocks'
]

### Community Tab options

# Archive file to only retrieve new files
comm_tab_archive = '/app/com-tab-archive.txt'

# Temporary and final directory
# Don't change for container version
tempdir = '/app/temp/'
donedir = '/app/Done/'
membersdir = "/app/Members"
communitydir = "/app/CommunityPosts"

unarchiveddir = "/app/unarchived"
unarchivedtempdir = "/app/temp/unarchived"
