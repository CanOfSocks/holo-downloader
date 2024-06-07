from subprocess import Popen 
from config import title_filter,description_filter
from yt_dlp import YoutubeDL
import getConfig
from datetime import datetime, timedelta, timezone    
import os
from time import sleep


def vid_executor(streams, command, unarchived = False):    
    if(command == "spawn"):
        if unarchived == True:
            download_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'unarchived.py')
        else:
            download_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloadVid.py')
        for live in streams:
            command = ["python", download_script, live]
            #Popen(command)
            Popen(command, start_new_session=True)
            from time import sleep
            from random import uniform
            sleep(uniform(min(len(streams),30),min(2*len(streams),90)))
    elif(command == "bash"):
        bash_array = ' '.join(streams)
        print(bash_array)
        return bash_array            
    else:
        print(streams)
        return streams
    
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
    
    desc = None
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

    if getConfig.getCookiesFile():
        ydl_opts.update({'cookiefile': getConfig.getCookiesFile()})
    
    with YoutubeDL(ydl_opts) as ydl:
        url = "https://www.youtube.com/watch?v={0}".format(live.get('id'))
        info = ydl.extract_info(url, download=False)
        #print(info)
        desc = info.get('description')
    
    #if not desc:
    #    return None

    import re
    #Return the result of the regex, if the search fails (such as a syntax error), disregard filter
    try:
        if re.search(descFilter,desc):
            return True
        else:
            return False
    except:
        return None
    

def filtering(live,channel_id):
    title = titleFilter(live,channel_id)
    description = descriptionFilter(live,channel_id)
    
    if(title or description):
        return True
    elif(title is None and description is None):
        return True
    else:
        return False
    #If filter exists for both title and description, try to find match for either
#    if title is not None and description is not None:
#        if(title or description):
#            return True
#        else:
#           return False        
    #If only title filter exists, use that
#    elif(title and description is None): #The "and" may not be necessary
#        return True
    #Otherwise use description
#    elif(description and title is None):
#        return True
    #If neither exist, assume true as there is no filter
#    elif(title is None and description is None):
#        return True
    #Otherwise none of the filters passed
#    else:
#        return False
       
def withinFuture(releaseTime=None):
    #Assume true if value missing
    lookahead = getConfig.getLookAhead()
    if(not releaseTime or not lookahead):
        return True
    release = datetime.fromtimestamp(releaseTime, timezone.utc)    
    limit = datetime.now(timezone.utc) + timedelta(hours=lookahead)
    if(release <= limit):
        return True
    else:
        return False
    
def getAvailability(live):
    if live.availability is None:        
        from yt_dlp import YoutubeDL
        from config import cookies_file
        options = {
            "cookiefile": cookies_file,
            "quiet": True
        }
        with YoutubeDL(options) as ydl: 
            #If unable to get availability (such as broken cookies or not a member), then keep as none
            try:
                info_dict = ydl.extract_info("https://youtu.be/{0}".format(live.id), download=False)
                live.description = info_dict.get('description', None)
                live.availability = info_dict.get('availability', None)
            except:
                pass
    return live.availability

def get_upcoming_or_live_videos(channel_id, tab):
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

    if getConfig.getCookiesFile():
        ydl_opts.update({'cookiefile': getConfig.getCookiesFile()})
    
    with YoutubeDL(ydl_opts) as ydl:
        url = "https://www.youtube.com/channel/{0}/{1}".format(channel_id, tab)
        info = ydl.extract_info(url, download=False)
        #print(info)
        upcoming_or_live_videos = []
        for video in info['entries']:
            
            if (video.get('live_status') == 'is_live' or video.get('live_status') == 'post_live' or (video.get('live_status') == 'is_upcoming' and withinFuture(video.get('release_timestamp')))) and filtering(video,channel_id):
                #print("live_status = {0}".format(video.get('live_status')))
                #print(video)
                upcoming_or_live_videos.append(video.get('id'))


        return list(set(upcoming_or_live_videos))
    
def combine_unarchived(ids):
    import os
    import re
    yta_pattern = r"^.{11}-yta\.info\.json$"
    directory = getConfig.getUnarchivedTempFolder()
    files = [f for f in os.listdir(directory) if (os.path.isfile(os.path.join(directory, f)) and os.path.join(directory, f).endswith('.info.json'))]
    id_set = set()
    
    for id in ids:
        id_set.add(id)
    for file in files:
        if re.match(yta_pattern, file):
            continue
        id = file.replace('.info.json', '')
        id_set.add(id)
    
    return list(id_set)
