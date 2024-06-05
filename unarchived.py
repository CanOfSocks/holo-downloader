#!/usr/local/bin/python
import yt_dlp
import os
import json
import getConfig
import requests
import base64
from subprocess import Popen, run

def check_ytdlp_age(existing_file):    
    from time import time
    if (os.path.getmtime(existing_file) / 3600) > 6:
        os.remove(existing_file)
        return False
    return True
"""
    # Open the file
    data = None
    with open(existing_file, 'r') as file:
        # Load the JSON data from the file
        data = json.load(file)
    if data and 'epoch' in data:
        current_time = time()
        if ((current_time - data['epoch']) / 3600) > 6 or (os.path.getmtime(existing_file) / 3600) > 6:
            print("JSON for {0} is older than 6 hours, removing...".format(id))
            os.remove(existing_file)
    # Return False if removed, otherwise True
            return False
    elif (os.path.getmtime(existing_file) / 3600) > 6:
        os.remove(existing_file)
        return False
    return True
"""
def check_yta_raw_age(existing_file):   
    from time import time
    if (os.path.getmtime(existing_file) / 3600) > 6:
        os.remove(existing_file)
        return False
    return True
"""
    with open(existing_file, 'r') as file:
        # Load the JSON data from the file
        data = json.load(file)
    if data and 'createTime' in data:
        current_time = time()
        from datetime import datetime
        if ((current_time - datetime.fromisoformat(data['createTime']).timestamp()) / 3600) > 6 or (os.path.getmtime(existing_file) / 3600) > 6:
            print("YTA-raw JSON for {0} is older than 6 hours, removing...".format(id))
            os.remove(existing_file)
    # Return False if removed, otherwise True
            return False
    elif (os.path.getmtime(existing_file) / 3600) > 6:
        os.remove(existing_file)
        return False
    return True            
"""
def is_video_private(id):
    url = "https://www.youtube.com/watch?v={0}".format(id)
    ydl_opts = {
        'retries': 25,
        'skip_download': True,
        'cookiefile': getConfig.getCookiesFile(),        
        'quiet': True,
        'no_warnings': True,
        'extractor_args': 'youtube:player_client=web;skip=dash;formats=incomplete,duplicate'
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info_dict = ydl.extract_info(url, download=False)
            # Check if the video is private
            if info_dict.get('live_status') == 'is_live':
                # Save best thumbnail as base64 for yta-raw
                best_thumb = None
                best_preference = None
                for item in info_dict.get('thumbnails'):
                    # Check if the URL ends with .jpg and if its preference is higher than the current best
                    if item['url'].endswith('.jpg') and (best_preference is None or item['preference'] > best_preference):
                        best_thumb = item['url']
                        best_preference = item['preference']
                if best_thumb:
                    info_dict['b64_img'] = get_image(best_thumb)
                    info_dict['best_thumb'] = best_thumb
                
                
                json_out_path = os.path.join(getConfig.getUnarchivedTempFolder(),"{0}.info.json".format(id))
                os.makedirs(os.path.dirname(json_out_path), exist_ok=True)
                with open(json_out_path, 'w', encoding='utf-8') as json_file:
                    json.dump(info_dict, json_file, ensure_ascii=False, indent=4)
                #create_yta_json(id)
                return

        except yt_dlp.utils.DownloadError as e:
            # If an error occurs, we can assume the video is private or unavailable
            if 'video is private' in str(e):
                print("Video {0} is private".format(id))
                #if os.
                try:
                    create_yta_json(id)
                    # Add delay before age check
                    return
                except e:
                    print("Error processing {0} - {1}".format(id,e))
            elif 'This live event will begin in' in str(e) or 'Premieres in' in str(e):
                pass
            elif "This video is available to this channel's members on level" in str(e):
                pass
            else:
                raise e
    existing_file = os.path.join(getConfig.getUnarchivedTempFolder(),"{0}.info.json".format(id))
    if os.path.exists(existing_file):
        check_ytdlp_age(existing_file)
    elif os.path.exists(os.path.join(getConfig.getUnarchivedTempFolder(),"{0}-yta.info.json".format(id))):
        existing_file = os.path.join(getConfig.getUnarchivedTempFolder(),"{0}-yta.info.json".format(id))
        check_yta_raw_age(existing_file)
            

def get_image(url):
    response = requests.get(url)

    # Check if the request was successful
    if response.status_code == 200:
        # Get the content of the response
        image_content = response.content

        # Convert the image content to a base64 string
        base64_image = base64.b64encode(image_content).decode()

        return f"data:image/jpeg;base64,{base64_image}"

# FROM https://github.com/Spicadox/auto-ytarchive-raw/blob/master/getjson.py
def create_yta_json(id):
    from datetime import datetime, timezone
    ytdlp_json = os.path.join(getConfig.getUnarchivedTempFolder(),"{0}.info.json".format(id))
    data = None
    if os.path.exists(ytdlp_json) and check_ytdlp_age(ytdlp_json):        
        with open(ytdlp_json, 'r', encoding='utf-8') as file:
            # Load the JSON data from the file
            data = json.load(file)
    else:
        return
    PRIORITY = {
        "VIDEO": [
            337, 315, 266, 138, # 2160p60
            313, 336, # 2160p
            308, # 1440p60
            271, 264, # 1440p
            335, 303, 299, # 1080p60
            312, 311, # Premium 1080p and 720p
            248, 169, 137, # 1080p
            334, 302, 298, # 720p60
            247, 136, # 720p
            231, 135, # 480p
            230, 134, # 360p
            229, 133, # 240p
            269, 160  # 144p 
        ],
        "AUDIO": [
            251, 141, 171, 140, 250, 249, 139, 234, 233
        ]
    }
    VERSION = "1.7"
    best = {
        "video": {},
        "audio": {},
        "metadata": {
                "channelName": data.get('channel'),
                "channelURL": data.get('channel_url'),
                "description": data.get('description'),
                "id": data.get('id'),
                # Fallback to release_timestamp to timestamp to epoch and make it zero (1/1/1970) if neither
                "startTimestamp": datetime.fromtimestamp(data.get('release_timestamp', data.get('timestamp', data.get('epoch',datetime.now().timestamp()))), tz=timezone.utc).isoformat(),
                "thumbnail": data.get('b64_img'),
                "thumbnailUrl": data.get('best_thumb'),
                "title": data.get('fulltitle')
            },
        "other": {},
        "version": VERSION,
        "createTime": datetime.fromtimestamp(data.get('epoch', datetime.now().timestamp()), tz=timezone.utc).isoformat()
    }    
    #Get best available format for video and audio
    for video_format in PRIORITY['VIDEO']:
        video_format = str(video_format)
        #if best['video'] is None:
        for ytdlp_format in data['formats']:
            if video_format == ytdlp_format['format_id'] and ytdlp_format['protocol'] == 'https':
                best['video'][video_format] = ytdlp_format['url']
                break
    for audio_format in PRIORITY['AUDIO']:
        audio_format = str(audio_format)
        #if best['audio'] is None:
        for ytdlp_format in data['formats']:
            if audio_format == ytdlp_format['format_id'] and ytdlp_format['protocol'] == 'https':
                best['audio'][audio_format] = ytdlp_format['url']
                break   
    yta_json = os.path.join(getConfig.getUnarchivedTempFolder(),"{0}-yta.info.json".format(id))
    with open(yta_json, 'w', encoding='utf-8') as json_file:
        json.dump(best, json_file, ensure_ascii=False, indent=4)
        
    output_path = yt_dlp.YoutubeDL({}).prepare_filename(info_dict=data, outtmpl=os.path.join(getConfig.getUnarchivedFolder(),getConfig.get_ytdlp()))
    
        
    download_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'runYTAraw.py')
    command = ["python", download_script, yta_json, output_path, ytdlp_json]
    #print(' '.join(command))
    Popen(command, start_new_session=True)
    #run(command)
    
def main(id=None):
    # If system args were also none, raise exception
    if id is None:
        raise Exception("No video ID provided, unable to continue")
    
    is_video_private(id)
