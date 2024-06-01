#!/usr/local/bin/python
import requests

from sys import argv
import common
#import json
from datetime import datetime


url = "https://holo.dev/api/v1/lives/open"

class json_object:
    def __init__(self,live):
        self.holodev_id=live.get('id')
        self.title=live.get('title')
        date_format = "%Y-%m-%dT%H:%M:%S.%fZ"
        self.start_at = datetime.strptime(live.get('start_at'), date_format)
        self.created_at = datetime.strptime(live.get('created_at'), date_format)
        self.json_channel_id = live.get('channel_id')
        self.thumbnail_url = live.get('cover')
        
        self.id = live.get('room')
        self.platform = live.get('platform')
        self.channel_id = live.get('channel')
        
        self.duration = live.get('duration') if live.get('duration') is not None else -1
        self.description = None
        
        #For members only
        self.availability = None
    
    def get(self, property, default=None):
        return getattr(self, property, default)
    
   
#def membershipOnlyFilter(live):
#    membersOnly = members_only.get(live.channel_id, None)
#    # If config is set to only get member videos for channel, attempt to get availability. 
#    # Failures to get availability will result in the video not being retrieved
#    if membersOnly:
#        return getAvailability(live).casefold() == "subscriber_only".casefold()
#    else:
#        return True 

    
def getStreams(unarchived=False):
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
                
            # Get dictionary depending on which search (unarchived/normal)    
            if unarchived:
                from config import unarchived_channel_ids_to_match as channel_ids_to_match
            else:
                from config import channel_ids_to_match
            
            if channel_ids_to_match is None:
                return
            #Run for each class object made
            for live in videos:            
                # Check if the "channel_id" is in the dictionary
                #print(live.channel_id)
                if live.channel_id in channel_ids_to_match.values():
                    # Find the key (descriptive name) corresponding to the matched channel_id
                    #channel_name = [key for key, value in channel_ids_to_match.items() if value == channel_id][0]
                    
                    if(live.platform == "youtube"):
                        #print(time_difference)
                        if unarchived:
                            if common.withinFuture(live.start_at.timestamp()):
                                matching_streams.append(live.get('id'))
                        else:
                            if(common.withinFuture(live.start_at.timestamp()) and common.filtering(live, live.get('channel_id'))):
                                matching_streams.append(live.get('id'))
    else:
        print("Error retrieving streams: {0}".format(response.status_code))
    # Print the list of matching streams as a JSON representation
    #matching_streams_json = json.dumps(matching_streams)
    return matching_streams


def main(command=None, unarchived=False):
    streams = getStreams(unarchived)
    if unarchived:
        import unarchived
        import threading
        import time
        threads = []
        for stream in streams:
            t = threading.Thread(target=unarchived.main, args=(stream,), daemon=True)
            threads.append(t)
            t.start()
            time.sleep(3.0)
        for t in threads:
            t.join()
    else:
        common.vid_executor(streams, command)
    


if __name__ == "__main__":
    try:
        command = argv[1]
    except IndexError:
        command = None
    try:
        unarchive = argv[2]
    except IndexError:
        pass

    main(command)
