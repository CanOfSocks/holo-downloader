#!/usr/local/bin/python
import yt_dlp
import os
import threading
from getConfig import ConfigHandler
from pathlib import Path
import subprocess
import discord_web
import traceback
from time import sleep, asctime
# Import FileLock, setup_umask, AND the shared kill_all event from common
from common import FileLock, setup_umask, kill_all 

import argparse
import logging
from typing import Optional, Tuple, Dict, Any

# --- Logging Initialization Helper (Define locally for modularity) ---

def initialize_logging(config: ConfigHandler, logger_name: Optional[str] = None) -> logging.Logger:
    """Initializes logging based on the provided ConfigHandler instance."""
    from livestream_dl.download_Live import setup_logging
    name = logger_name if logger_name else __name__
    return setup_logging(
        log_level=config.get_log_level(), 
        console=True, 
        file=config.get_log_file(), 
        file_options=config.get_log_file_options(),
        logger_name=name
    )

# --- Core Functions Updated with Dependencies ---

def createTorrent(output: str, config: ConfigHandler) -> None:
    """Creates a torrent file for the given output path using the provided config."""
    if not config.getTorrent():
        return
    fullPath = config.getTempOutputPath(output)
    folder = Path(fullPath).parent
    
    # Use config methods to build the command
    subprocess.run(config.torrentBuilder(fullPath, folder), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        
def downloader(id: str, outputTemplate: str, info_dict: Dict[str, Any], config: ConfigHandler, logger: logging.Logger) -> None:
    """
    Handles the main video segment download process.
    
    Uses the kill_all event imported from common.
    """
    if id is None or outputTemplate is None:
        raise Exception(("Unable to retrieve information about video {0}".format(id)))
        
    from livestream_dl import download_Live
    
    # Options retrieved using the passed config object
    options = config.get_livestream_dl_options(info_dict=info_dict, output_template=outputTemplate)
    
    # Start additional information downloaders (Discord notification)
    # NOTE: Assuming discord_web.main is updated to accept the config object
    discord_notify = threading.Thread(target=discord_web.main, kwargs={"id": id, "status": "recording", "config": config, "logger": logger}, daemon=True)
    discord_notify.start() 
    
    try:
        # Pass the imported kill_all event and the logger instance
        downloader = download_Live.LiveStreamDownloader(kill_all=kill_all, logger=logger)
        downloader.download_segments(info_dict=info_dict, resolution=config.get_quality(), options=options)
    except Exception as e:
        logger.exception("Error occured {0}".format(id))
        sleep(1.0)
        raise Exception(("{2} - Error downloading video: {0}, Code: {1}".format(id, e, asctime())))
    finally:
        # Wait for remaining processes
        discord_notify.join()
        
    # Final notification using the config object
    discord_web.main(id=id, status="done", config=config)
    return

def download_video_info(video_url: str, config: ConfigHandler, logger: logging.Logger) -> Tuple[str, Dict[str, Any]]:
    """
    Fetches video metadata and prepares the output file template.
    
    Uses the passed config and logger objects.
    """
    options = {
        'outtmpl': config.get_ytdlp(),
        'quiet': True,
        'no_warnings': True      
    }
    
    import json
    from livestream_dl import getUrls
    
    with yt_dlp.YoutubeDL(options) as ydl:
        additional_ytdlp_options = None
        if config.get_ytdlp_options():
            additional_ytdlp_options = json.loads(config.get_ytdlp_options())
            
        # Call get_Video_Info with config dependencies
        info_dict, live_status = getUrls.get_Video_Info(
            id=video_url, 
            wait=(1,300), 
            cookies=config.get_cookies_file(), 
            proxy=config.get_proxy(), 
            additional_options=additional_ytdlp_options, 
            include_dash=config.get_include_dash(), 
            include_m3u8=config.get_include_m3u8()
        )
        outputFile = str(ydl.prepare_filename(info_dict))
            
    logger.debug("({0}) Info.json: {1}".format(video_url, json.dumps(info_dict)))
    logger.info("Output file: {0}".format(outputFile))
    return outputFile, info_dict
    
def main(id: Optional[str] = None, config: ConfigHandler = None, logger: logging.Logger = None) -> None:
    """
    Main function for handling video download with file locking.
    """
    # 1. Ensure config and logger objects are available
    if config is None:
        config = ConfigHandler()
    if logger is None:
        logger = initialize_logging(config, logger_name=id or "main_download")

    if id is None:
        raise ValueError("No video ID provided, unable to continue")
    
    # 2. Determine lock file path using the config object
    if os.path.exists("/dev/shm/"):
        lock_file_path = "/dev/shm/videoDL-{0}".format(id)
    else:
        lock_file_path = os.path.join(config.get_temp_folder(), "videoDL-{0}.lockfile".format(id))

    # 3. Acquire file lock and run download logic
    try:
        with FileLock(lock_file_path) as lock_file:
            lock_file.acquire()
            
            # Use the config object for pre-download notification
            discord_web.main(id, "waiting", config=config)
            
            try:
                # Pass config and logger
                outputFile, info_dict = download_video_info(id, config, logger)
                logger.debug("Output file: {0}".format(outputFile))
                
                if outputFile is None:
                    raise LookupError(("Unable to retrieve information about video {0}".format(id)))
                
                # Pass config and logger
                downloader(id, outputFile, info_dict, config, logger)
                
            except Exception as e:
                logger.exception("Error downloading video")
                # Pass config for error notification
                discord_web.main(id, "error", message=f"{type(e).__name__}: {str(e)}"[-500:], config=config)

            lock_file.release()
    except (IOError, BlockingIOError) as e:
        logger.info("Unable to acquire lock for {0}, must be already downloading: {1}".format(lock_file_path, e))
    

if __name__ == "__main__":
    try:
        # 1. Setup umask globally
        setup_umask() 

        # 2. Instantiate ConfigHandler once
        app_config = ConfigHandler()
        
        # We will initialize the logger inside main for the video ID, but need a general one for errors here
        # Initialize a logger that will catch initial parsing/execution errors
        
    
    
        # Create the parser
        parser = argparse.ArgumentParser(description="Process an video by ID")
        parser.add_argument('ID', type=str, help='The video ID (required)')

        # Parse the arguments
        args = parser.parse_args()

        main_logger = initialize_logging(app_config, logger_name=f"{args.ID}")

        # Call main, passing the config object and the logger
        main(id=args.ID, config=app_config, logger=main_logger)
        
    except Exception as e:
        # Use the initialized logger for final error handling
        logging.exception("An unhandled error occurred when attempting to download a video")
        raise