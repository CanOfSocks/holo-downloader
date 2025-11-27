from livestream_dl.download_Live import LiveStreamDownloader
import common
from getConfig import ConfigHandler
import json
import argparse
import os
import logging
import threading
import signal
from time import sleep
from typing import Optional, Any
from common import initialize_logging, kill_all


def main(json_file: str, output_path: Optional[str] = None, config: ConfigHandler = None, logger: logging = None):


    # Instantiate ConfigHandler if it's not provided
    if config is None:
        config = ConfigHandler()

    if logger is None:
        logger = initialize_logging(config, logger_name="chat_downloader")

    common.setup_umask()

    info_dict: dict

    # Load info_dict and initialize logger specific to the video ID
    try:
        with open(json_file, 'r', encoding='utf-8') as file:
            info_dict = json.load(file)
    except Exception as e:
        # Initialize a basic logger if file reading fails before ID is known
        
        logger.exception(e)
        return
    
    # Initialize logger using video ID
    logger = initialize_logging(config, logger_name="{0}-chat".format(info_dict.get('id'))) 

    options = {
        "ID": info_dict.get('id'),
        "output": output_path if output_path is not None else str(config.get_unarchived_output_path(config.get_ytdlp())),
        "temp_folder": config.get_unarchived_temp_folder(),
        "cookies": config.get_cookies_file(),
        "log_level": config.get_log_level(),
        "log_file": config.get_log_file(),
    }
    
    if os.path.exists("/dev/shm"):
        lock_file_path = "/dev/shm/chat-{0}".format(options.get("ID"))
    else:
        lock_file_path = os.path.join(options.get("temp_folder"), "chat-{0}.lockfile".format(options.get("ID")))
    
    try:
        with common.FileLock(lock_file_path) as lock_file:
            lock_file.acquire()
            
            # The LiveStreamDownloader gets the unique logger instance
            downloader = LiveStreamDownloader(kill_all=kill_all, logger=logger)
            
            result: Any = downloader.download_live_chat(info_dict=info_dict, options=options)
            lock_file.release()
            return result
            
    except (IOError, BlockingIOError) as e:
        logger.info("Unable to acquire lock for {0}, must be already downloading: {1}".format(lock_file_path, e))
        
    return None
    

if __name__ == "__main__":
    try:
        # Instantiate ConfigHandler once for the execution flow
        app_config = ConfigHandler()

        # Set up a generic logger for argparse error handling
        logger = initialize_logging(app_config, logger_name="chat_downloader")
    
    
        # Create the parser
        parser = argparse.ArgumentParser(description="Download chat of a video using info.json")
        parser.add_argument('json', type=str, help='info.json path (required)')
        parser.add_argument('--output-path', type=str, default=None, help='Optional output path')
        args = parser.parse_args()

        # Call main, passing the config object
        main(json_file=args.json, output_path=args.output_path, config=app_config, logger=logger)
        
    except Exception as e:
        # Use the logger initialized above for final error logging
        logging.exception("An unhandled error occurred when trying to download chat")