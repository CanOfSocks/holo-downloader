def get_upcoming_or_live_videos(channel_id):
    import getConfig
    import yt_dlp
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'force_generic_extractor': True,
        'cookiefile': getConfig.getCookiesFile(),
        'sleep_interval_requests': 1,
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        url = f"https://www.youtube.com/channel/{channel_id}/membership"
        info = ydl.extract_info(url, download=False)
        
        upcoming_or_live_videos = []
        for video in info['entries']:
            if video.get('upload_date') == '0' or video.get('is_live') == True or video.get('is_upcoming') or video.get('post_live'):
                upcoming_or_live_videos.append(video['id'])

        return upcoming_or_live_videos

#channel_id = 'UCoSrY_IQQVpmIRZ9Xf-y93g'
#channel_id = 'UCgmPnx-EEeOrZSg5Tiw7ZRQ'
#upcoming_or_live_videos = get_upcoming_or_live_videos(channel_id)
#print("Upcoming or live video IDs:", upcoming_or_live_videos)

def getVideos(members_only, command=None):
    from subprocess import Popen
    from random import uniform
    from time import sleep
    import discord_web
    all_lives = []
    for channel in members_only:
        sleep(uniform(5.0, 10.0))
        try:
            lives = get_upcoming_or_live_videos(members_only[channel])
            if(command == "spawn"):
                for live in lives:
                    process = ["python", "/app/downloadVid.py", live]
                    Popen(process, start_new_session=True)
            all_lives += lives
        except Exception as e:
            print(("Error fetching membership streams for {0}. Check cookies. \n{1}".format(channel,e)))
            discord_web.main(channel, "membership-error")
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
    main()