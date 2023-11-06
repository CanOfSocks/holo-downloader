import requests
#import json
from datetime import datetime,timezone
from config import channel_ids_to_match,look_ahead

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
    def time_until_start(self):
        current_datetime = datetime.now(timezone.utc)
        time_difference = self.start_at - current_datetime.replace(tzinfo=None)
        return time_difference.total_seconds()
        
        


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
                    if(look_ahead is None):
                        look_ahead = 24
                    if(live.time_until_start() <= look_ahead * 3600):
                        matching_streams.append(live.video_id)

# Print the list of matching streams as a JSON representation
#matching_streams_json = json.dumps(matching_streams)
bash_array = ' '.join(matching_streams)
#print(matching_streams_json)
print(bash_array)
