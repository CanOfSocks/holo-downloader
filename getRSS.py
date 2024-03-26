#!/usr/local/bin/python

from sys import argv
from subprocess import Popen
import feedparser
from datetime import datetime,timezone
from config import channel_ids_to_match,look_ahead,title_filter,description_filter,members_only


class rss_object:
    def __init__(self,live):
        self.id=live.get('yt_videoid')
        self.title=live.get('title')
        self.updated = datetime.fromisoformat(live.get('updated'))
        #self.json_channel_id = live.get('channel_id')
        self.thumbnail_url = live.get('media_thumbnail')[0].get('url')
        self.channel_id = live.get('yt_channelid')
        self.author = live.get('author')
        self.duration = live.get('duration') if live.get('duration') is not None else -1
        self.description = live.get('summary')
        
        #For members only
        self.availability = None
    def time_since_update(self):
        current_datetime = datetime.now(timezone.utc)
        time_difference = self.updated.replace(tzinfo=None) - current_datetime.replace(tzinfo=None)
        # Get absolute value to get from past as well
        return abs(time_difference.total_seconds()) 

def titleFilter(live):
    titFilter = title_filter.get(live.channel_id)
    if titFilter is None:
        return None
    import re
    #Return the result of the regex, if the search fails (such as a syntax error), disregard filter
    try:
        search = re.search(titFilter,live.title)
        if(search):
            return True
        else:
            return False
    except:
        print("Filter failed")
        return None
    
def descriptionFilter(live):
    descFilter = description_filter.get(live.channel_id)    
    #If filter not present, return None
    if descFilter is None:
        return None
    
    if live.description is None:
        return None

    import re
    #Return the result of the regex, if the search fails (such as a syntax error), disregard filter
    try:
        return re.search(descFilter,live.description)
    except:
        return None
    

def filtering(live):
    title = titleFilter(live)
    description = descriptionFilter(live)
    
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


def getStreams():
    matching_streams = []  
    
    videos = []
    for channel in channel_ids_to_match:
        #print(channel)
        RSSFeed = feedparser.parse("https://www.youtube.com/feeds/videos.xml?channel_id={0}".format(channel_ids_to_match.get(channel)))
        print(RSSFeed)
        for vid in RSSFeed.entries:
            videos.append(rss_object(vid))
    #print(videos)
    for live in videos:
        from time import sleep
        #print("Time since update: {0} vs {1}".format(live.time_since_update(),  look_ahead * 3600))
        if(live.time_since_update() <= look_ahead * 3600.0 and filtering(live)):
        #if(live.time_since_update() <= look_ahead * 3600.0):
            import yt_dlp
            from getConfig import getCookiesFile,getLookAhead
            from getMembers import withinFuture
            options = {
                'retries': 25,
                'skip_download': True,
                'cookiefile': getCookiesFile(),        
                'quiet': True,
                'sleep_interval_requests': 1,
                'no_warnings': True       
            }

            with yt_dlp.YoutubeDL(options) as ydl:
                video = ydl.extract_info(live.id, download=False)
                if video.get('live_status') == 'is_live' or video.get('live_status') == 'post_live' or (video.get('live_status') == 'is_upcoming' and withinFuture(video.get('release_timestamp'),getLookAhead())):         
                    matching_streams.append(live.id)
        sleep(1.0)
    return matching_streams


def main(command=None):
    if(command == "spawn"):
        for live in getStreams():
            command = ["python", "/app/downloadVid.py", live]
            #Popen(command)
            Popen(command, start_new_session=True)
    elif(command == "bash"):
        bash_array = ' '.join(getStreams())
        print(bash_array)
        return bash_array            
    else:
        streams = getStreams()
        print(streams)
        return streams


if __name__ == "__main__":
    try:
        command = argv[1]
    except IndexError:
        command = None

    main(command)