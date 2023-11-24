#!/usr/local/bin/python
import requests
import psutil
from sys import argv
from subprocess import Popen
#import json
from datetime import datetime,timezone
from config import channel_ids_to_match,look_ahead,title_filter,description_filter,members_only

url = "https://holo.dev/api/v1/lives/open"

class json_object:
    def __init__(self,live):
        self.id=live.get('id')
        self.title=live.get('title')
        date_format = "%Y-%m-%dT%H:%M:%S.%fZ"
        self.start_at = datetime.strptime(live.get('start_at'), date_format)
        self.created_at = datetime.strptime(live.get('created_at'), date_format)
        self.json_channel_id = live.get('channel_id')
        self.thumbnail_url = live.get('cover')
        
        self.video_id = live.get('room')
        self.platform = live.get('platform')
        self.channel_id = live.get('channel')
        
        self.duration = live.get('duration') if live.get('duration') is not None else -1
        self.description = None
        
        #For members only
        self.availability = None
    def time_until_start(self):
        current_datetime = datetime.now(timezone.utc)
        time_difference = self.start_at - current_datetime.replace(tzinfo=None)
        return time_difference.total_seconds()
    
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
    #Otherwise get description
    if live.description is None:
        from yt_dlp import YoutubeDL
        from config import cookies_file
        options = {
            "cookiefile": cookies_file,
            "quiet": True
        }
        with YoutubeDL(options) as ydl: 
            info_dict = ydl.extract_info("https://youtu.be/{0}".format(live.id), download=False)
            ##Could be expanded based on demand
            #video_id = info_dict.get("id", None)
            #video_title = info_dict.get('title', None)
            live.description = info_dict.get('description', None)
            live.availability = info_dict.get('availability', None)
    
    #if still one after retrieval, disregard description filter
    if live.description is None:
        return None

    import re
    #Return the result of the regex, if the search fails (such as a syntax error), disregard filter
    try:
        return re.search(descFilter,live.description)
    except:
        return None
    
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

def membershipOnlyFilter(live):
    membersOnly = members_only.get(live.channel_id, None)
    # If config is set to only get member videos for channel, attempt to get availability. 
    # Failures to get availability will result in the video not being retrieved
    if membersOnly:
        return getAvailability(live).casefold() == "subscriber_only".casefold()
    else:
        return True 
    
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
    # List to store the matching live streams
    matching_streams = []

    # Send an HTTP GET request to the URL
    response = requests.get(url)

    # Check if the request was successful (status code 200)
    if response.status_code == 200:
        # Parse the JSON data from the response
        data = response.json()

        videos = []
        # Check if the "lives" key exists in the JSON data
        if 'lives' in data:
            # Add all videos to class list
            for live in data['lives']:
                videos.append(json_object(live))
                
            #Run for each class object made
            for live in videos:            
                # Check if the "channel_id" is in the dictionary
                #print(live.channel_id)
                if live.channel_id in channel_ids_to_match.values():
                    # Find the key (descriptive name) corresponding to the matched channel_id
                    #channel_name = [key for key, value in channel_ids_to_match.items() if value == channel_id][0]
                    
                    if(live.platform == "youtube"):
                        #print(time_difference)
                        if(live.time_until_start() <= look_ahead * 3600 and filtering(live) and membershipOnlyFilter(live)):
                            matching_streams.append(live.video_id)

    # Print the list of matching streams as a JSON representation
    #matching_streams_json = json.dumps(matching_streams)
    bash_array = ' '.join(matching_streams)
    #print(matching_streams_json)
    #print(bash_array)

    return matching_streams



def isIDrunning(command_to_check):
    for process in psutil.process_iter(['pid', 'cmdline']):
        try:
            process_cmdline = process.info['cmdline']
            # Checks if downloadVid.py is running and if it is running with the same ID
            if process_cmdline and process_cmdline[1] == command_to_check[1] and process_cmdline[2] == command_to_check[2]:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

def main(command=None):
    if(command == "spawn"):
        for live in getStreams():
            command = ["/app/downloadVid.py", live]
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
