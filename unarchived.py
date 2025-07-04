#!/usr/local/bin/python
import yt_dlp
import os
import json
from getConfig import ConfigHandler
import requests
import base64
import subprocess
import argparse
#from yt_dlp.utils import DownloadError

import psutil
import sys
from shutil import move
import discord_web
from json import load

import traceback

from livestream_dl import getUrls
import logging

getConfig = ConfigHandler()
from livestream_dl.download_Live import setup_logging
setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file())

def check_ytdlp_age(existing_file):    
    from time import time
    current_time = time()
    # Open the file
    data = None
    if os.path.exists(existing_file):
        with open(existing_file, 'r', encoding='utf-8') as file:
            # Load the JSON data from the file
            data = json.load(file)
    if data and 'epoch' in data:
        current_time = time()
        if ((current_time - data['epoch']) / 3600) > 6 or ((current_time - os.path.getmtime(existing_file)) / 3600.0) > 6:
            logging.info("JSON for {0} is older than 6 hours, removing...".format(os.path.basename(existing_file)))
            os.remove(existing_file)
    # Return False if removed, otherwise True
            return False
    elif ((current_time - os.path.getmtime(existing_file)) / 3600.0) > 6:
        os.remove(existing_file)
        return False
    return True

def check_yta_raw_age(existing_file):   
    from time import time
    current_time = time()
    data = None
    if os.path.exists(existing_file):
        with open(existing_file, 'r', encoding='utf-8') as file:
            # Load the JSON data from the file
            data = json.load(file)
    if data and 'createTime' in data:
        current_time = time()
        from datetime import datetime
        if ((current_time - datetime.fromisoformat(data['createTime']).timestamp()) / 3600) > 6 or (current_time - os.path.getmtime(existing_file) / 3600) > 6:
            logging.info("{1}: YTA-raw JSON for {0} is older than 6 hours, removing...".format(os.path.basename(existing_file)))
            os.remove(existing_file)
    # Return False if removed, otherwise True
            return False
    elif (current_time - os.path.getmtime(existing_file) / 3600) > 6:
        os.remove(existing_file)
        return False
    return True            
            
def is_video_private(id):
    json_out_path = os.path.join(getConfig.get_unarchived_temp_folder(),"{0}.info.json".format(id))
    chat_out_path = os.path.join(getConfig.get_unarchived_temp_folder(),"{0}.live_chat.zip".format(id))
    jpg_out_path = os.path.join(getConfig.get_unarchived_temp_folder(),"{0}.jpg".format(id))
    #jpg_out_path= "{0}.jpg".format(id)

    try:
        additional_ytdlp_options = None
        if getConfig.get_ytdlp_options():
            additional_ytdlp_options = json.loads(getConfig.get_ytdlp_options())
        info_dict, live_status = getUrls.get_Video_Info(id=id,  wait=(5, 1800), cookies=getConfig.get_cookies_file(), proxy=getConfig.get_proxy(), additional_options=additional_ytdlp_options)
        if info_dict.get('live_status') == 'is_live' or info_dict.get('live_status') == 'post_live':
            os.makedirs(os.path.dirname(json_out_path), exist_ok=True)
            with open(json_out_path, 'w', encoding='utf-8') as json_file:
                json.dump(info_dict, json_file, ensure_ascii=False, indent=4)   
                logging.debug(os.path.abspath(json_out_path)) 

            if getConfig.get_unarchived_chat_dl() and info_dict.get('live_status') == 'is_live':
                chat_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'getChatOnly.py')
                command = ["python", chat_script, '--output-path', chat_out_path, '--', json_out_path]
                #Popen(command)
                subprocess.Popen(command, start_new_session=True)
            from livestream_dl.download_Live import download_auxiliary_files
            file = download_auxiliary_files(info_dict=info_dict, options={'write_thumbnail': True})[0].get('thumbnail',None)
            if file is not None and not str(file.suffix).endswith("jpg"):
                subprocess.run([
                    "ffmpeg", "-y",
                    '-hide_banner', '-nostdin', '-loglevel', 'error',
                    "-i", file.absolute(),  # Input file
                    "-q:v", "2",
                    jpg_out_path        # Output file
                ], check=True)
                logging.debug("Deleting: ".format(file.absolute()))
                file.unlink()
            return
    except PermissionError as e:
        logging.debug("Experienced permission error while checking {0}: {1}".format(id, e))
        if os.path.exists(json_out_path):
            download_private(info_dict_file=json_out_path, thumbnail=jpg_out_path, chat=chat_out_path)   
    except ValueError as e:
        logging.exception("Experienced value error while checking {0}: {1}".format(id, e))
    except Exception as e:
        logging.exception("Unexpected exception occurred: {0}\n{1}".format(e,traceback.format_exc()))

    """
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info_dict = ydl.extract_info(url, download=False)
            info_dict = ydl.sanitize_info(info_dict)
            # Check if the video is private
            if info_dict.get('live_status') == 'is_live' or info_dict.get('live_status') == 'post_live':
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
                
                os.makedirs(os.path.dirname(json_out_path), exist_ok=True)
                with open(json_out_path, 'w', encoding='utf-8') as json_file:
                    json.dump(info_dict, json_file, ensure_ascii=False, indent=4)
                #create_yta_json(id)
                return

        except yt_dlp.utils.DownloadError as e:
            # If an error occurs, we can assume the video is private or unavailable
            if 'video is private' in str(e) or "Private video. Sign in if you've been granted access to this video" in str(e):
                print("Video {0} is private".format(id))
                #if os.
                try:
                    create_yta_json(id, json_out_path)
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
    """
    #existing_file = os.path.join(getConfig.get_unarchived_temp_folder(),"{0}.info.json".format(id))
    if os.path.exists(json_out_path):
        # If removed (returns false), the also remove thumbnail if it exists
        if not check_ytdlp_age(json_out_path):
            if os.path.exists(jpg_out_path):
                os.remove(jpg_out_path)
            if os.path.exists(chat_out_path):
                os.remove(chat_out_path)
            

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
def create_yta_json(id, ytdlp_json = None):
    from datetime import datetime, timezone
    if ytdlp_json is None:
        ytdlp_json = os.path.join(getConfig.get_unarchived_temp_folder(),"{0}.info.json".format(id))
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
            244, 135, # 480p
            243, 134, # 360p
            242, 133, # 240p
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
                #break
    for audio_format in PRIORITY['AUDIO']:
        audio_format = str(audio_format)
        #if best['audio'] is None:
        for ytdlp_format in data['formats']:
            if audio_format == ytdlp_format['format_id'] and ytdlp_format['protocol'] == 'https':
                best['audio'][audio_format] = ytdlp_format['url']
                #break   
    yta_json = os.path.join(getConfig.get_unarchived_temp_folder(),"{0}-yta.info.json".format(id))
    with open(yta_json, 'w', encoding='utf-8') as json_file:
        json.dump(best, json_file, ensure_ascii=False, indent=4)
        
    output_path = yt_dlp.YoutubeDL({}).prepare_filename(info_dict=data, outtmpl=os.path.join(getConfig.get_unarchived_temp_folder(),getConfig.get_ytdlp()))
    
        
    #download_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'runYTAraw.py')
    #command = ["python", download_script, yta_json, output_path, ytdlp_json]
    run_yta_raw(yta_json, output_path, ytdlp_json)
    
def run_yta_raw(json_file, output_path = None, ytdlp_json = None):
    data = None
    with open(json_file, 'r', encoding='utf-8') as file:
            # Load the JSON data from the file
            data = load(file)
    if data:
        discord_web.main(data['metadata']['id'], "recording")
    if os.name == 'nt':
        command = ['ytarchive-raw-go-windows-amd64']
    else:
        command = ['ytarchive-raw-go']
    command += [ '--threads', '20', '--overwrite-temp', '-i', json_file, '--log-level', 'error']
    if getConfig.get_unarchived_temp_folder():
        command += ['--temp-dir', str(getConfig.get_unarchived_temp_folder())]
    #if not getConfig.getMux():
    #    command += ["--merger", "download-only"]
    if output_path:
        output = output_path
    elif getConfig.get_output_template_yta_raw():
        output = ['--output', os.path.join(getConfig.get_unarchived_temp_folder(), getConfig.get_ytdlp())]
    else:
        output = ['--output', os.path.join(getConfig.get_unarchived_temp_folder(), '[%(upload_date)s] %(title)s [%(channel)s] (%(id)s)')]
    command += ['--output', output]
    logging.debug("Command: {0}".format(' '.join(command)))
    try:
        
        result = subprocess.run(command, check=True, text=True)
    except subprocess.CalledProcessError as e:
        logging.debug(e.stdout.decode())
        logging.debug(e.stderr.decode())
        discord_web.main(data['metadata']['id'], "error", message=str(e.stderr)[-1000:])
        raise Exception(("Error downloading unarchived video, Code: {0}".format(e.returncode)))
    if result.returncode == 0:        
        if ytdlp_json and output_path:
            move(ytdlp_json, '{0}.info.json'.format(output))
            from common import replace_ip_in_json
            replace_ip_in_json('{0}.info.json'.format(output))
        os.remove(json_file)
        if data:
            discord_web.main(data['metadata']['id'], "done")
        logging.info("Finished downloading yta video: {0}".format(json_file))
        
def download_private(info_dict_file, thumbnail=None, chat=None):
    with open(info_dict_file, 'r', encoding='utf-8') as file:
        # Load the JSON data from the file
        info_dict = json.load(file)
    logging.info("Attempting to download video: {0}".format(info_dict.get('id', "")))
    discord_web.main(info_dict.get('id'), "recording")
    from livestream_dl import download_Live

    # Add livechat to downloaded files dictionary so it is moved appropriately at the end
    
    options = {
        "ID": info_dict.get('id'),
        "resolution": 'best',
        "video_format": None,
        "audio_format": None,
        "threads": 20,
        "batch_size": 5,
        "segment_retries": 10,
        "merge": getConfig.get_mux(),
        "output": str(getConfig.get_unarchived_output_path(getConfig.get_ytdlp())),
        "temp_folder": getConfig.get_unarchived_temp_folder(),
        "write_thumbnail": getConfig.get_thumbnail(),
        "embed_thumbnail": getConfig.get_thumbnail(),
        "write_info_json": getConfig.get_info_json(),
        "write_description": getConfig.get_description(),
        "keep_database_file": False,
        "recovery": True,
        "database_in_memory": False,
        "direct_to_ts": False,
        "wait_for_video": None,
        "json_file": None,
        "remove_ip_from_json": getConfig.get_remove_ip(),
        "log_level": getConfig.get_log_level(),
        #"log_level": "DEBUG",
        "log_file": getConfig.get_log_file(),
        'write_ffmpeg_command': getConfig.get_ffmpeg_command(),
    }
    logging.info("Output path: {0}".format(options.get('output')))
    if thumbnail and os.path.exists(thumbnail):
        download_Live.file_names['thumbnail'] = download_Live.FileInfo(thumbnail, file_type='thumbnail')
    if chat is not None and os.path.exists(chat):
        download_Live.file_names.update({
            'live_chat': download_Live.FileInfo(chat, file_type='live_chat')
        })

    try:
        download_Live.download_segments(info_dict=info_dict, resolution='best', options=options)   
    except Exception as e:
        logging.exception(e)
        import traceback
        discord_web.main(info_dict.get('id'), "error", message=str("{0}\n{1}".format(e, traceback.format_exc))[-1000:])
        return
    
    

    discord_web.main(info_dict.get('id'), "done")
    
    if os.path.exists(info_dict_file):
        os.remove(info_dict_file)
        
    if os.path.exists(thumbnail):
        os.remove(thumbnail)
        
def is_script_running(script_name, id):
    current = psutil.Process()
    logging.debug("PID: {0}, command line: {1}, argument: {2}".format(current.pid, current.cmdline(), current.cmdline()[2:]))
    current_pid = psutil.Process().pid
    
    for process in psutil.process_iter():
        try:
            process_cmdline = process.cmdline()
            if (
                process.pid != current_pid and
                script_name in process_cmdline and
                id in process_cmdline[2:]   # Needs testing between Windows and Postix to ensure compatibility
            ):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False
    
def main(id=None):
    # Get script name
    script_name = sys.argv[0]

    if id is None:
        raise Exception("No video ID provided, unable to continue")
    
    if is_script_running(script_name, id):
        logging.debug("{0} already running, exiting...".format(id))
        return 0
    
    is_video_private(id)


if __name__ == "__main__":
    # Create the parser
    parser = argparse.ArgumentParser(description="Process an video by ID")

    # Add a required positional argument 'ID'
    parser.add_argument('ID', type=str, help='The video ID (required)')

    # Parse the arguments
    args = parser.parse_args()

    # Access the 'ID' value
    id = args.ID
    main(id=id)
