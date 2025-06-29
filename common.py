from subprocess import Popen 
#from config import title_filter,description_filter
from yt_dlp import YoutubeDL
#import getConfig
from getConfig import ConfigHandler
from datetime import datetime, timedelta, timezone    
import os
from time import sleep
import re
import random
import logging

getConfig = ConfigHandler()
from livestream_dl.download_Live import setup_logging
setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file())

def vid_executor(streams, command, unarchived = False, frequency = None):    
    if getConfig.randomise_lists() is True:
        streams = random_sample(streams)
    
    if(command == "spawn"):
        if unarchived == True:
            download_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'unarchived.py')
        else:
            download_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloadVid.py')
        
        if frequency:
            frequency_sec = cron_frequency(frequency)
            sleep_time = frequency_sec/(max(len(streams) + 1),2)
            
        else:
            sleep_time = 60
        
        for i, live in enumerate(streams):
            command = ["python", download_script, '--', live]
            #Popen(command)
            logging.debug("Executing: {0}".format(' '.join(command)))
            Popen(command, start_new_session=True)
            if i < len(streams) - 1:
                logging.debug("Sleeping for {0}".format(sleep_time))
                sleep(random.uniform(max(sleep_time/2, 1.0),max(sleep_time * 1.25, 1.0)))
        return    
        
    elif(command == "bash"):
        bash_array = ' '.join(streams)
        logging.info(bash_array)
        return bash_array            
    else:
        logging.info(streams)
        return streams
    
def titleFilter(live,channel_id):
    titFilter = getConfig.get_title_filter().get(channel_id)
    if titFilter is None:
        return None
    
    #Return the result of the regex, if the search fails (such as a syntax error), disregard filter
    try:
        search = re.search(titFilter,live.get('title'))
        if(search):
            return True
        else:
            return False
    except:
        logging.error("Filter failed")
        return None
    
def descriptionFilter(live,channel_id):
    descFilter = getConfig.get_desc_filter().get(channel_id)    
    #If filter not present, return None
    if descFilter is None:
        return None
    desc = live.get('description', None)
    if desc is None:
        ydl_opts = {
            'quiet': True,
            'force_generic_extractor': True,
            'sleep_interval': 1,
            'sleep_interval_requests': 1,
            'no_warnings': True,
            'cookiefile': getConfig.get_cookies_file(),
            #'verbose': True
            #'match_filter': filters
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            url = "https://www.youtube.com/watch?v={0}".format(live.get('id'))
            info = ydl.extract_info(url, download=False)
            logging.debug(info)
            desc = info.get('description')
    
    #if not desc:
    #    return None

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
       
def withinFuture(releaseTime=None):
    #Assume true if value missing
    lookahead = getConfig.get_look_ahead()
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
        options = {
            "cookiefile": getConfig.get_cookies_file(),
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

def get_upcoming_or_live_videos(channel_id, tab=None):
    #channel_id = str(channel_id)
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        #'force_generic_extractor': True,
        'sleep_interval': 1,
        'sleep_interval_requests': 1,
        'no_warnings': True,
        'cookiefile': getConfig.get_cookies_file(),
        #'playlist_items': '1:10',
        #'verbose': True
        #'match_filter': filters
    }
    
    with YoutubeDL(ydl_opts) as ydl:
        if tab == "membership":
            if channel_id.startswith("UUMO"):
                url = "https://www.youtube.com/playlist?list={0}".format(channel_id)
            elif channel_id.startswith("UC") or channel_id.startswith("UU"):
                url = "https://www.youtube.com/playlist?list={0}".format("UUMO" + channel_id[2:])
            else:
                url = "https://www.youtube.com/channel/{0}/{1}".format(channel_id, tab)
                ydl_opts.update({'playlist_items': '1:10'})
        elif tab == "streams":
            if channel_id.startswith("UU"):
                url = "https://www.youtube.com/playlist?list={0}".format(channel_id)
            elif channel_id.startswith("UC"):
                url = "https://www.youtube.com/playlist?list={0}".format("UU" + channel_id[2:])
            elif channel_id.startswith("UUMO"):
                url = "https://www.youtube.com/playlist?list={0}".format("UU" + channel_id[4:])
            else:
                url = "https://www.youtube.com/channel/{0}/{1}".format(channel_id, tab)
                ydl_opts.update({'playlist_items': '1:10'})
        else:
            url = "https://www.youtube.com/channel/{0}/{1}".format(channel_id, tab)
            ydl_opts.update({'playlist_items': '1:10'})
        info = ydl.extract_info(url, download=False)
        logging.debug(info)
        upcoming_or_live_videos = []
        for video in info['entries']:
            
            if (video.get('live_status') == 'is_live' or video.get('live_status') == 'post_live' or (video.get('live_status') == 'is_upcoming' and withinFuture(video.get('release_timestamp')))) and filtering(video,video.get('channel_id')):
                logging.debug("live_status = {0}".format(video.get('live_status')))
                logging.debug(video)
                upcoming_or_live_videos.append(video.get('id'))


        return list(set(upcoming_or_live_videos))
    
def combine_unarchived(ids):
    yta_pattern = r"^.([0-9A-Za-z_-]{10}[048AEIMQUYcgkosw])-yta\.info\.json$"
    directory = getConfig.get_unarchived_temp_folder()
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
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

def cron_frequency(cron_expr):
    from croniter import croniter
    now = datetime.now()
    cron = croniter(cron_expr, now)
    
    # Get the next two execution times
    next_time = cron.get_next(datetime)
    next_time_after = cron.get_next(datetime)
    
    # Calculate the interval in seconds
    interval_seconds = (next_time_after - next_time).total_seconds()
    
    return interval_seconds

def replace_ip_in_json(file_name):
    pattern = re.compile(r'((?:[0-9]{1,3}\.){3}[0-9]{1,3})|((?:[a-f0-9]{1,4}:){7}[a-f0-9]{1,4})')

    with open(file_name, 'r', encoding="utf8") as file:
        content = file.read()

    modified_content = re.sub(pattern, '0.0.0.0', content)

    with open(file_name, 'w', encoding="utf8") as file:
        file.write(modified_content)
        
def random_sample(data, k=None):
    """Returns a random sample of size k from a list, tuple, or dictionary."""
    if k is None:
        k = len(data)
    if isinstance(data, list):
        return random.sample(data, k)  # Returns a list
    
    elif isinstance(data, tuple):
        return tuple(random.sample(data, k))  # Returns a tuple
    
    elif isinstance(data, dict):
        sampled_items = dict(random.sample(list(data.items()), k))  # Returns a dictionary
        return sampled_items
    
    else:
        raise TypeError("Unsupported data type. Only lists, tuples, and dictionaries are allowed.")
    
import os
import time

if os.name == 'nt':
    import msvcrt
else:
    import fcntl

class FileLock:
    def __init__(self, path):
        self.path = path
        self.file = None

    def acquire(self, timeout=None):
        self.file = open(self.path, 'a+')
        if os.name == 'nt':
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self):
        if self.file:
            if os.name == 'nt':
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()
            self.file = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
