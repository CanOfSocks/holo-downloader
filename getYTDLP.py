#!/usr/local/bin/python
from sys import argv
import yt_dlp
from getMembers import withinFuture
from config import channel_ids_to_match,look_ahead,title_filter,description_filter,members_only

def titleFilter(live,channel_id):
    titFilter = title_filter.get(channel_id)
    if titFilter is None:
        return None
    import re
    #Return the result of the regex, if the search fails (such as a syntax error), disregard filter
    try:
        search = re.search(titFilter,live.get('title'))
        if(search):
            return True
        else:
            return False
    except:
        print("Filter failed")
        return None
    
def descriptionFilter(live,channel_id):
    descFilter = description_filter.get(channel_id)    
    #If filter not present, return None
    if descFilter is None:
        return None
    
    if live.get('description') is None:
        return None

    import re
    #Return the result of the regex, if the search fails (such as a syntax error), disregard filter
    try:
        return re.search(descFilter,live.get('description'))
    except:
        return None
    

def filtering(live,channel_id):
    title = titleFilter(live,channel_id)
    description = descriptionFilter(live,channel_id)
    
    #If filter exists for both title and description, try to find match for either
    if title is not None and description is not None:
        if(title or description):
            return True
        else:
            return False        
    #If only title filter exists, use that
    elif(title and description is None): #The "and" may not be necessary
        return True
    #Otherwise use description
    elif(description and title is None):
        return True
    #If neither exist, assume true as there is no filter
    elif(title is None and description is None):
        return True
    #Otherwise none of the filters passed
    else:
        return False

def filters(info, *, incomplete):
    from getConfig import getLookAhead
    """Download only videos longer than a minute (or with unknown duration)"""
    video = info.get('live_status')
    if not (video.get('live_status') == 'is_live' or video.get('live_status') == 'post_live' or (video.get('live_status') == 'is_upcoming' and withinFuture(video.get('release_timestamp'),getLookAhead()))):
        return 'Video not live or upcoming'
    if not filtering(video):
        return ('Does not match title and/or description filters')

def get_upcoming_or_live_videos(channel_id):
    from getConfig import getCookiesFile,getLookAhead
    #channel_id = str(channel_id)
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'force_generic_extractor': True,
        'sleep_interval': 1,
        'sleep_interval_requests': 1,
        'no_warnings': True,
        'playlist_items': '1:10',
        #'verbose': True
        #'match_filter': filters
    }

    if getCookiesFile():
        ydl_opts.update({'cookiefile': getCookiesFile()})
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        url = "https://www.youtube.com/channel/{0}/streams".format(channel_id)
        info = ydl.extract_info(url, download=False)
        #print(info)
        upcoming_or_live_videos = []
        for video in info['entries']:
            #print(video)
            if filtering(video,channel_id) and (video.get('live_status') == 'is_live' or video.get('live_status') == 'post_live' or (video.get('live_status') == 'is_upcoming' and withinFuture(video.get('release_timestamp'),getLookAhead()))):
                #print("live_status = {0}".format(video.get('live_status')))
                upcoming_or_live_videos.append(video['id'])


        return set(upcoming_or_live_videos)
    
def getVideos(channel_ids_to_match, command=None, frequency=None):
    from subprocess import Popen
    all_lives = []
    for channel in channel_ids_to_match:
        #print(channel)
        try:
            #print("Looking for: {0}".format(channel))
            lives = get_upcoming_or_live_videos(channel_ids_to_match[channel])
            if(command == "spawn"):
                for live in lives:
                    process = ["python", "/app/downloadVid.py", live]
                    Popen(process, start_new_session=True)
            all_lives += list(lives)
        except Exception as e:
            print(("Error fetching streams for {0}. Check cookies. \n{1}".format(channel,e)))
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

def main(command=None, frequency=None):
    try:
        from config import channel_ids_to_match
        getVideos(channel_ids_to_match, command, frequency)
    except ImportError:
        pass
if __name__ == "__main__":
    try:
        command = argv[1]
    except IndexError:
        command = None
    try:
        frequency = argv[2]
    except IndexError:
        frequency = None

    main(command, frequency)
