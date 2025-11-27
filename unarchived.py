#!/usr/local/bin/python
import os
import json
from getConfig import ConfigHandler
import subprocess
import argparse
from shutil import move
import discord_web
from json import load
from common import FileLock, setup_umask, initialize_logging, kill_all
import traceback
from livestream_dl import getUrls
import logging
import threading
import signal
from time import sleep
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

# --- Utility Functions ---

def check_ytdlp_age(existing_file: str, config: ConfigHandler = None, logger: logging.Logger = None) -> bool:
    """Checks if a JSON info file is older than 6 hours and removes it if so."""
    if config is None:
        config = ConfigHandler()
    if logger is None:
        logger = initialize_logging(config, logger_name="checker")

    from time import time
    current_time = time()
    data: Optional[Dict[str, Any]] = None
    
    if os.path.exists(existing_file):
        try:
            with open(existing_file, 'r', encoding='utf-8') as file:
                data = json.load(file)
        except Exception:
            # If JSON is corrupt, treat it as old
            data = None 
            
    file_age_hours = (current_time - os.path.getmtime(existing_file)) / 3600.0
    
    # Check 1: Age using 'epoch' key (if present) or file modification time
    if data and 'epoch' in data:
        data_age_hours = (current_time - data['epoch']) / 3600
        if data_age_hours > 6 or file_age_hours > 6:
            logger.info("JSON {0} is older than 6 hours, removing...".format(os.path.basename(existing_file)))
            os.remove(existing_file)
            return False
    elif file_age_hours > 6:
        # Check 2: Only using file modification time
        logger.info("JSON {0} is older than 6 hours (mod time), removing...".format(os.path.basename(existing_file)))
        os.remove(existing_file)
        return False
        
    return True

def download_private(info_dict_file: str, thumbnail: Optional[str] = None, chat: Optional[str] = None, config: ConfigHandler = None, logger: logging.Logger = None) -> None:
    """Downloads a private or post-live video using pre-fetched info."""
    if config is None:
        config = ConfigHandler()
    if logger is None:
        logger = initialize_logging(config, logger_name="downloader")
        
    with open(info_dict_file, 'r', encoding='utf-8') as file:
        info_dict = json.load(file)
        
    video_id = info_dict.get('id', "")
    logger.info("Attempting to download video: {0}".format(video_id))
    
    # Assuming discord_web.main is updated to accept config
    discord_web.main(video_id, "recording", config=config)
    
    from livestream_dl import download_Live
    # Use the passed/created logger instance
    downloader = download_Live.LiveStreamDownloader(kill_all=kill_all, logger=logger)
    
    options = {
        "ID": video_id,
        "resolution": 'best',
        "video_format": None, "audio_format": None, "threads": 20, "batch_size": 5, "segment_retries": 10,
        "merge": config.get_mux(),
        "output": str(config.get_unarchived_output_path(config.get_ytdlp())),
        "temp_folder": config.get_unarchived_temp_folder(),
        "write_thumbnail": config.get_thumbnail(),
        "embed_thumbnail": config.get_thumbnail(),
        "write_info_json": config.get_info_json(),
        "write_description": config.get_description(),
        "keep_database_file": False, "recovery": True,
        "force_recover_merge": config.get_unarchived_force_merge(),
        "recovery_failure_tolerance": config.get_unarchived_recovery_failure_tolerance(),
        "database_in_memory": False, "direct_to_ts": False, "wait_for_video": None, "json_file": None,
        "remove_ip_from_json": config.get_remove_ip(),
        "log_level": config.get_log_level(),
        "log_file": config.get_log_file(),
        'write_ffmpeg_command': config.get_ffmpeg_command(),
    }
    
    logger.info("Output path: {0}".format(options.get('output')))
    
    if thumbnail and os.path.exists(thumbnail):
        downloader.file_names['thumbnail'] = downloader.FileInfo(thumbnail, file_type='thumbnail')
    if chat is not None and os.path.exists(chat):
        downloader.file_names.update({
            'live_chat': downloader.FileInfo(chat, file_type='live_chat')
        })

    try:
        downloader.download_segments(info_dict=info_dict, resolution='best', options=options) 
    except Exception as e:
        logger.exception(e)
        import traceback
        # Assuming discord_web.main is updated to accept config
        discord_web.main(video_id, "error", message=str("{0}\n{1}".format(e, traceback.format_exc))[-1000:], config=config)
        return
    
    # Assuming discord_web.main is updated to accept config
    discord_web.main(video_id, "done", config=config)
    
    # Clean up temp files
    if os.path.exists(info_dict_file):
        os.remove(info_dict_file)
    if thumbnail and os.path.exists(thumbnail):
        os.remove(thumbnail)

def is_video_private(id: str, config: ConfigHandler = None, logger: logging.Logger = None) -> None:
    """Checks video status, downloads info, thumbnail, and triggers download_private if required."""
    if config is None:
        config = ConfigHandler()
    if logger is None:
        # Use a logger specific to the video ID
        logger = initialize_logging(config, logger_name=id) 

    temp_folder = config.get_unarchived_temp_folder()
    json_out_path = os.path.join(temp_folder,"{0}.info.json".format(id))
    chat_out_path = os.path.join(temp_folder,"{0}.live_chat.zip".format(id))
    jpg_out_path = os.path.join(temp_folder,"{0}.jpg".format(id))

    from livestream_dl.download_Live import LiveStreamDownloader
    # Use the passed/created logger instance
    downloader = LiveStreamDownloader(kill_all=kill_all, logger=logger)
    
    try:
        additional_ytdlp_options = None
        if config.get_ytdlp_options():
            additional_ytdlp_options = json.loads(config.get_ytdlp_options())
            
        # Call getUrls.get_Video_Info with config dependencies
        info_dict, live_status = getUrls.get_Video_Info(
            id=id, 
            wait=(5, 1800), 
            cookies=config.get_cookies_file(), 
            proxy=config.get_proxy(), 
            additional_options=additional_ytdlp_options
        )
        
        # --- Processing Live/Post-Live Videos ---
        if info_dict.get('live_status') in ['is_live', 'post_live']:
            os.makedirs(os.path.dirname(json_out_path), exist_ok=True)
            with open(json_out_path, 'w', encoding='utf-8') as json_file:
                json.dump(info_dict, json_file, ensure_ascii=False, indent=4) 
                logger.debug("Created {0}".format(os.path.abspath(json_out_path)))

            if config.get_unarchived_chat_dl() and info_dict.get('live_status') == 'is_live':
                # Assuming getChatOnly.py is updated to handle config dependencies
                chat_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'getChatOnly.py')
                command = ["python", chat_script, '--output-path', chat_out_path, '--', json_out_path]
                subprocess.Popen(command, start_new_session=True)
            
            aux_options = {
                'write_thumbnail': True,
                'temp_folder': temp_folder,                  
            }
            
            # Download auxiliary files (thumbnail)
            downloaded_aux = downloader.download_auxiliary_files(info_dict=info_dict, options=aux_options)
            file: Optional[Path] = downloaded_aux[0].get('thumbnail', None)
            
            if file is not None and file.exists() and not str(file.suffix).endswith(".jpg"):
                # Convert thumbnail to JPG using ffmpeg
                subprocess.run([
                    "ffmpeg", "-y", '-hide_banner', '-nostdin', '-loglevel', 'error',
                    "-i", file.absolute(), 
                    "-q:v", "2",
                    jpg_out_path 
                ], check=True)
                logger.debug("Deleting original thumbnail: {0}".format(file.absolute()))
                file.unlink()
            elif file is not None and file.exists():
                 # Rename if already a jpg but in temp folder
                 os.rename(file.absolute(), jpg_out_path)
                 
            return # Exit successfully if video is live/post-live
            
    except getUrls.VideoInaccessibleError as e:     
        logger.info("Experienced Video Inaccessible Error error while checking {0}: {1}".format(id, e))
        if os.path.exists(json_out_path):
            # If info.json exists, attempt to download it as a private video
            download_private(info_dict_file=json_out_path, thumbnail=jpg_out_path, chat=chat_out_path, config=config, logger=logger) 
    except getUrls.VideoProcessedError as e:
        logger.info("({0}) {1}".format(id, e))
    except Exception as e:
        logger.exception("Unexpected exception occurred: {0}".format(e))
        try:
            # Assuming discord_web.main is updated to accept config
            discord_web.main(id, "error", message=str("{0}\n{1}".format(e, traceback.format_exc))[-1000:], config=config)
        except Exception as e_discord:
            logger.exception("Unable to send discord error message")

    # --- Cleanup ---
    if os.path.exists(json_out_path):
        # Check if files are too old and should be deleted
        if not check_ytdlp_age(json_out_path, config=config, logger=logger):
            if os.path.exists(jpg_out_path):
                os.remove(jpg_out_path)
            if os.path.exists(chat_out_path):
                os.remove(chat_out_path)
        
def main(id: Optional[str] = None, config: ConfigHandler = None, logger: logging.Logger = None) -> None:
    """
    Main function for the unarchived stream checker. Sets up lock and calls processing logic.
    """
    if id is None:
        raise Exception("No video ID provided, unable to continue")
        
    # 1. Ensure config and logger objects are available
    if config is None:
        config = ConfigHandler()
    
    # 2. Setup the logger specific to this video ID
    if logger is None:
        logger = initialize_logging(config, logger_name=id)
        
    # 3. Determine lock file path
    if os.path.exists("/dev/shm"):
        lock_file_path = "/dev/shm/unarchived-{0}".format(id)
    else:
        # Use the configured temp folder
        lock_file_path = os.path.join(config.get_temp_folder(), "unarchived-{0}.lockfile".format(id))
    
    # 4. Acquire file lock and run processing logic
    try:
        with FileLock(lock_file_path) as lock_file:
            lock_file.acquire()
            # Pass the config and logger objects to the worker function
            is_video_private(id, config=config, logger=logger)
            lock_file.release()
            
    except (IOError, BlockingIOError) as e:
        logger.warning("Unable to acquire lock for {0}, must be already downloading: {1}".format(lock_file_path, e))

if __name__ == "__main__":

    try:
    
        # 2. Instantiate ConfigHandler once
        app_config = ConfigHandler()
        
        
        # 3. Setup umask globally (as in original script)
        setup_umask() 
        
        # We delay the logger creation until the ID is known (inside main), 
        # but need a basic logger for the argparse block's exception.
        # We use the logging system's root logger here, which is set up by initialize_logging if called.
    
    
        # Create the parser
        parser = argparse.ArgumentParser(description="Process an video by ID")
        parser.add_argument('ID', type=str, help='The video ID (required)')

        # Parse the arguments
        args = parser.parse_args()
        
        logger = initialize_logging(app_config, f"{args.ID}-unarchived")
        # Call main, passing the config object
        # The main function will create the final logger instance specific to the ID
        main(id=args.ID, config=app_config)
        
    except Exception as e:
        logging.exception("An unhandled error occurred when trying to run the unarchived stream checker")
        raise
