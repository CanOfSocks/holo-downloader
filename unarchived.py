#!/usr/local/bin/python
#import yt_dlp
import os
import json
from getConfig import ConfigHandler
#import requests
#import base64
import subprocess
import argparse
#from yt_dlp.utils import DownloadError

#import psutil
#import sys
from shutil import move
import discord_web
from json import load

from common import FileLock

import traceback

from livestream_dl import getUrls
import logging

getConfig = ConfigHandler()
from livestream_dl.download_Live import setup_logging
setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file(), file_options=getConfig.get_log_file_options())

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
"""
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
            logging.info("JSON {0} ({1}) is older than 6 hours, removing...".format(os.path.basename(existing_file), existing_file))
            os.remove(existing_file)
    # Return False if removed, otherwise True
            return False
    elif (current_time - os.path.getmtime(existing_file) / 3600) > 6:
        os.remove(existing_file)
        return False
    return True            
"""
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
                logging.debug("Created {0}".format(os.path.abspath(json_out_path)))

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
        if "Video has been processed, please use yt-dlp directly" in str(e):
            logging.debug("({0}) {1}".format(id, e))
        else:
            logging.exception("Experienced value error while checking {0}: {1}".format(id, e))
    except Exception as e:
        logging.exception("Unexpected exception occurred: {0}\n{1}".format(e,traceback.format_exc()))

    #existing_file = os.path.join(getConfig.get_unarchived_temp_folder(),"{0}.info.json".format(id))
    if os.path.exists(json_out_path):
        # If removed (returns false), the also remove thumbnail if it exists
        if not check_ytdlp_age(json_out_path):
            if os.path.exists(jpg_out_path):
                os.remove(jpg_out_path)
            if os.path.exists(chat_out_path):
                os.remove(chat_out_path)
            
        
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
"""        
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
"""
def main(id=None):
    # Get script name
    ##script_name = sys.argv[0]

    if id is None:
        raise Exception("No video ID provided, unable to continue")
    """
    if is_script_running(script_name, id):
        logging.debug("{0} already running, exiting...".format(id))
        return 0
    """
    if os.path.exists("/dev/shm"):
        lock_file_path = "/dev/shm/unarchived-{0}".format(id)
    else:
        lock_file_path = os.path.join(getConfig.get_temp_folder(), "unarchived-{0}.lockfile".format(id))
    with FileLock(lock_file_path) as lock_file:
        try:
            lock_file.acquire()
            is_video_private(id)
            """
            if result is not None and isinstance(result, tuple):
                out_folder = os.path.dirname(options.get("output"))
                os.makedirs(out_folder)
                shutil.move(result[0], out_folder)
            """
            lock_file.release()
        except (IOError, BlockingIOError):
            logging.error("Unable to aquire lock for {0}, must be already downloading".format(lock_file_path))
    


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
