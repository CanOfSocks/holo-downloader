#!/usr/local/bin/python
from sys import argv
import yt_dlp

def withinFuture(releaseTime,lookahead):
    #Assume true if value missing
    if(not releaseTime or not lookahead):
        return True
    from datetime import datetime, timedelta, timezone
    release = datetime.fromtimestamp(releaseTime, timezone.utc)    
    limit = datetime.now(timezone.utc) + timedelta(hours=lookahead)
    if(release <= limit):
        return True
    else:
        return False
def get_upcoming_or_live_videos(channel_id):
    import getConfig
    
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'force_generic_extractor': True,
        'cookiefile': getConfig.getCookiesFile(),
        'sleep_interval_requests': 1,
        'no_warnings': True,
        'playlist_items': '1:10',
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        url = f"https://www.youtube.com/channel/{channel_id}/membership"
        info = ydl.extract_info(url, download=False)
        
        upcoming_or_live_videos = []
        for video in info['entries']:
            #print(video)
            if video.get('live_status') == 'is_live' or video.get('live_status') == 'post_live' or (video.get('live_status') == 'is_upcoming' and withinFuture(video.get('release_timestamp'),getConfig.getLookAhead())):
                #print("live_status = {0}".format(video.get('live_status')))
                upcoming_or_live_videos.append(video['id'])


        return set(upcoming_or_live_videos)

def getVideos(members_only, command=None):
    from subprocess import Popen
    from random import uniform
    from time import sleep
    import discord_web
    all_lives = []
    for channel in members_only:
        sleep(uniform(5.0, 10.0))
        try:
            #print("Looking for: {0}".format(channel))
            lives = get_upcoming_or_live_videos(members_only[channel])
            if(command == "spawn"):
                for live in lives:
                    process = ["python", "/app/downloadVid.py", live]
                    Popen(process, start_new_session=True)
            all_lives += list(lives)
        except Exception as e:
            print(("Error fetching membership streams for {0}. Check cookies. \n{1}".format(channel,e)))
            discord_web.main(members_only[channel], "membership-error")
    if(command == "spawn"):
        #Assume processes were spawned
        return
    elif(command == "bash"):
        bash_array = ' '.join(all_lives)
        print(bash_array)
        return bash_array
    else:
        print(all_lives)
        return all_lives

def main(command=None):
    try:
        from config import members_only
        getVideos(members_only, command)
    except ImportError:
        pass
if __name__ == "__main__":
    try:
        command = argv[1]
    except IndexError:
        command = None

    main(command)
