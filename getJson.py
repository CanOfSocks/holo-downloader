import requests
import json
from datetime import datetime
from config import channel_ids_to_match

url = "https://holo.dev/api/v1/lives/open"



# List to store the matching live streams
matching_streams = []

# Send an HTTP GET request to the URL
response = requests.get(url)

# Check if the request was successful (status code 200)
if response.status_code == 200:
    # Parse the JSON data from the response
    data = response.json()

    # Check if the "lives" key exists in the JSON data
    if 'lives' in data:
        for live in data['lives']:
            channel_id = live.get('channel')
            # Check if the "channel_id" is in the dictionary
            #print(channel_id)
            if channel_id in channel_ids_to_match.values():
                # Find the key (descriptive name) corresponding to the matched channel_id
                channel_name = [key for key, value in channel_ids_to_match.items() if value == channel_id][0]
                if(live.get('platform') == "youtube"):
                    date_format = "%Y-%m-%dT%H:%M:%S.%fZ"
                    parsed_date = datetime.strptime(live.get('start_at'), date_format)
                    current_datetime = datetime.utcnow()
                    time_difference = parsed_date - current_datetime 
                    #print(time_difference)
                    if(time_difference.total_seconds() <= 48 * 3600):
                        matching_streams.append(live.get('room'))

# Print the list of matching streams as a JSON representation
#matching_streams_json = json.dumps(matching_streams)
bash_array = ' '.join(matching_streams)
#print(matching_streams_json)
print(bash_array)