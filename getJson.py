#!/usr/local/bin/python
import requests
import argparse
import common
import json
from datetime import datetime
from getConfig import ConfigHandler
import logging
from typing import Optional, List, Dict, Any
import queue

# URL for the API endpoint (remains constant)
url = "https://holo.dev/api/v1/lives/open"

# 2. json_object Class
class json_object:
    """A wrapper class to unify the structure of Holo.dev API responses."""
    def __init__(self, live: Dict[str, Any]):
        date_format = "%Y-%m-%dT%H:%M:%S.%fZ"
        
        self.holodev_id = live.get('id')
        self.title = live.get('title')
        self.start_at = datetime.strptime(live.get('start_at'), date_format)
        self.created_at = datetime.strptime(live.get('created_at'), date_format)
        self.json_channel_id = live.get('channel_id') # Original channel ID from JSON
        self.thumbnail_url = live.get('cover')
        
        self.id = live.get('room')          # Video ID
        self.platform = live.get('platform')
        self.channel_id = live.get('channel') # YouTube Channel ID (UC...)
        
        self.duration = live.get('duration') if live.get('duration') is not None else -1
        self.description = None
        self.availability = None
    
    def get(self, property: str, default: Any = None) -> Any:
        return getattr(self, property, default)

# 3. getStreams function updated to accept config
def getStreams(unarchived: bool = False, config: ConfigHandler = None, logger: logging = None, return_dict: bool = False) -> List[str]:
    # Instantiate ConfigHandler if it's not provided
    if config is None:
        config = ConfigHandler()

    if logger is None:
        logger = common.initialize_logging(config, "GetJson")
        
    matching_streams: List[str] = []

    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
    except requests.exceptions.RequestException as e:
        logging.exception(f"Error retrieving streams")
        return []

    # Parse the JSON data from the response
    try:
        data = response.json()
        logger.debug(f"Received videos:\n{json.dumps(data)}")
    except json.JSONDecodeError as e:
        logger.exception(f"Error decoding JSON response")
        return []

    videos: List[json_object] = []
    
    if 'lives' in data:
        for live in data['lives']:
            videos.append(json_object(live))
            
        # Get dictionary depending on which search (unarchived/normal) 
        # Access config properties via the passed/instantiated config object
        if unarchived:
            channel_ids_to_match = config.unarchived_channel_ids_to_match 
        else:
            channel_ids_to_match = config.channel_ids_to_match
        
        if channel_ids_to_match is None:
            return []
        
        for live in videos:
            # Check if the "channel_id" is in the dictionary values
            if live.channel_id in channel_ids_to_match.values():
                
                if live.platform == "youtube":
                    
                    # NOTE: common.withinFuture and common.filtering need the config object.
                    # Assuming they are refactored to handle config=None or accept it, 
                    # but since we have a valid config object here, we pass it.
                    
                    if unarchived:
                        # Assuming common.withinFuture is updated to accept config
                        if common.withinFuture(config, live.start_at.timestamp()):
                            if return_dict:
                                matching_streams.append({"id": live.get('id'), "channel_id": live.get("channel")})
                            else:
                                matching_streams.append(live.get('id'))
                    else:
                        # Assuming common.filtering is updated to accept config
                        if (common.withinFuture(config, live.start_at.timestamp()) and 
                            common.filtering(live, live.get('channel_id'), config)):
                            if return_dict:
                                matching_streams.append({"id": live.get('id'), "channel_id": live.get("channel")})
                            else:
                                matching_streams.append(live.get('id'))
                            
    return matching_streams

# 4. main function updated to accept config
def main(command: Optional[str] = None, unarchived: bool = False, frequency: Optional[str] = None, config: ConfigHandler = None, logger: logging = None, queue: queue.Queue = None, return_dict: bool = False) -> list[str] | str:
    # Instantiate ConfigHandler if it's not provided
    if config is None:
        config = ConfigHandler()

    if logger is None:
        logger = common.initialize_logging(config, "GetJson")
        
    streams = getStreams(unarchived, config, return_dict)
    
    if unarchived:
        # Assuming common.combine_unarchived is updated to accept config
        streams = common.combine_unarchived(streams, config)

    if queue is not None:
        for stream in streams:
            queue.put(stream)
        
    # Assuming common.vid_executor is updated to accept config
    return common.vid_executor(streams, command, config, unarchived, frequency=frequency) 

# 5. Execution block updated
if __name__ == "__main__":
    try:
        # Instantiate ConfigHandler once for the execution flow
        app_config = ConfigHandler()

        # Initialize logging using the instance
        logger = common.initialize_logging(app_config) 
    
    
        parser = argparse.ArgumentParser(description="Process a command and optionally an unarchive value.")

        parser.add_argument('--command', type=str, choices=['spawn', 'bash', 'print'], default=None, help='The command (optional, default: None)')
        parser.add_argument('--unarchived', action='store_true', help='Flag to indicate unarchived (default: False)')
        parser.add_argument('--frequency', type=str, default=None, help='Optional CRON frequency string for scheduling')

        # Parse the arguments
        args = parser.parse_args()

        # Call main, passing the config object
        main(command=args.command, unarchived=args.unarchived, frequency=args.frequency, config=app_config, logger=logger)
        
    except Exception as e:
        logging.exception("An unexpected error occurred when attempting to fetch videos from Holo.dev")
        raise