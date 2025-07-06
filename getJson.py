#!/usr/local/bin/python
import requests

import argparse
import common
#import json
from datetime import datetime
from getConfig import ConfigHandler
import logging

getConfig = ConfigHandler()

from livestream_dl.download_Live import setup_logging
setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file(), file_options=getConfig.get_log_file_options())

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
                channel_ids_to_match = getConfig.unarchived_channel_ids_to_match        
            else:
                channel_ids_to_match = getConfig.channel_ids_to_match
            
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
        logging.error("Error retrieving streams: {0}".format(response.status_code))
    # Print the list of matching streams as a JSON representation
    #matching_streams_json = json.dumps(matching_streams)
    return matching_streams


def main(command=None, unarchived=False, frequency=None):
    streams = getStreams(unarchived)
    if unarchived:
        streams = common.combine_unarchived(streams)
    common.vid_executor(streams, command, unarchived, frequency=frequency)  


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process a command and optionally an unarchive value.")

    # Add the required positional argument 'command'
    parser.add_argument('command', type=str, help='The command value')

    # Add an optional named argument '--unarchive' with default as None
    parser.add_argument('--unarchived', action='store_true', help='Flag to indicate unarchived (default: False)')

    # Parse the arguments
    args = parser.parse_args()

    # Access the arguments
    command = args.command
    unarchived = args.unarchive  # Will be None if not provided

    main(command=command, unarchived=unarchived)
