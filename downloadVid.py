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
from common import FileLock, setup_umask, kill_all, initialize_logging

import argparse
import logging
from typing import Optional, Tuple, Dict, Any

from livestream_dl import download_Live

'''
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
'''
# --- Core Functions Updated with Dependencies ---
class VideoDownloader():
    def __init__(self, id, config: ConfigHandler = None, logger: logging.Logger = None, kill_this: threading.Event = None):
        if not id:
            raise ValueError("No video ID provided, unable to continue")
        
        self.id = id        
        
        self.kill_this: threading.Event = kill_this or threading.Event()

        if config is None:
            config = ConfigHandler()
        self.config: ConfigHandler = config

        if logger is None:
            logger = initialize_logging(config, logger_name=id)
        self.logger: logging = logger
        
        self.livestream_downloader = download_Live.LiveStreamDownloader(kill_all=kill_all, logger=logger, kill_this=self.kill_this)
        

        self.info_dict = {}
        self.outputFile = None
        

    def createTorrent(self, output: str) -> None:
        """Creates a torrent file for the given output path using the provided config."""
        if not self.config.getTorrent():
            return
        fullPath = self.config.getTempOutputPath(output)
        folder = Path(fullPath).parent
        
        # Use config methods to build the command
        subprocess.run(self.config.torrentBuilder(fullPath, folder), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            
    def downloader(self) -> None:
        """
        Handles the main video segment download process.
        """
        if not self.info_dict or self.outputFile is None:
            raise Exception(("Unable to retrieve information about video {0}".format(self.id)))

        # Options retrieved using the passed config object
        options = self.config.get_livestream_dl_options(info_dict=self.info_dict, output_template=self.outputFile)
        
        # Start additional information downloaders (Discord notification)
        # NOTE: Assuming discord_web.main is updated to accept the config object
        discord_notify = threading.Thread(target=discord_web.main, kwargs={"id": self.id, "status": "recording", "config": self.config, "logger": self.logger}, daemon=True)
        discord_notify.start() 
        
        try:            
            self.livestream_downloader.stats["status"] = "Recording"
            self.livestream_downloader.download_segments(info_dict=self.info_dict, resolution=self.config.get_quality(), options=options)
            
            if self.kill_this.is_set():
                self.livestream_downloader.stats["status"] = "Cancelled"
            else:
                self.livestream_downloader.stats["status"] = "Finished"
        except KeyboardInterrupt as e:
            self.livestream_downloader.stats["status"] = "Cancelled"
            self.logger.warning("Download of {0} was cancelled".format(self.id))
            return
        except Exception as e:
            self.logger.exception("Error occured {0}".format(self.id))
            self.livestream_downloader.stats["status"] = "Error"
            sleep(1.0)
            raise Exception(("{2} - Error downloading video: {0}, Code: {1}".format(self.id, e, asctime())))
        finally:
            # Wait for remaining processes, up to 60s
            discord_notify.join(timeout=60.0)
            
        # Final notification using the config object
        discord_web.main(id=self.id, status="done", config=self.config)
        return

    def download_video_info(self, video_url: str) -> Tuple[str, Dict[str, Any]]:
        """
        Fetches video metadata and prepares the output file template.
        
        Uses the passed config and logger objects.
        """
        options = {
            'outtmpl': self.config.get_ytdlp(),
            'quiet': True,
            'no_warnings': True      
        }
        
        import json
        from livestream_dl import getUrls
        
        with yt_dlp.YoutubeDL(options) as ydl:
            additional_ytdlp_options = None
            if self.config.get_ytdlp_options():
                additional_ytdlp_options = json.loads(self.config.get_ytdlp_options())
                
            # Call get_Video_Info with config dependencies
            info_dict, live_status = getUrls.get_Video_Info(
                id=video_url, 
                wait=(10,900), 
                cookies=self.config.get_cookies_file(), 
                proxy=self.config.get_proxy(), 
                additional_options=additional_ytdlp_options, 
                include_dash=self.config.get_include_dash(), 
                include_m3u8=self.config.get_include_m3u8()
            )
            outputFile = str(ydl.prepare_filename(info_dict))
                
        self.logger.debug("({0}) Info.json: {1}".format(video_url, json.dumps(info_dict)))
        self.logger.info("Output file: {0}".format(outputFile))

        return outputFile, info_dict
        
    def main(self, use_lock_file=False) -> None:
        """
        Main function for handling video download with file locking.
        """
        
        def run_download():
            # Use the config object for pre-download notification
            discord_web.main(self.id, "waiting", config=self.config)
            self.livestream_downloader.stats["status"] = "Waiting"
            try:
                # Pass config and logger
                self.outputFile, self.info_dict = self.download_video_info(self.id)
                self.logger.debug("Output file: {0}".format(self.outputFile))
                
                if self.outputFile is None:
                    raise LookupError(("Unable to retrieve information about video {0}".format(id)))
                
                # Pass config and logger
                self.downloader()
                
            except Exception as e:
                self.logger.exception("Error downloading video")
                # Pass config for error notification
                discord_web.main(self.id, "error", message=f"{type(e).__name__}: {str(e)}"[-500:], config=self.config)

        
        if use_lock_file:
            # Run "run_download" within lock file
            if os.path.exists("/dev/shm/"):
                    lock_file_path = "/dev/shm/videoDL-{0}".format(self.id)
            else:
                lock_file_path = os.path.join(self.config.get_temp_folder(), "videoDL-{0}.lockfile".format(self.id))
            try:
                
                with FileLock(lock_file_path) as lock_file:
                    lock_file.acquire()
                    
                    run_download()

                    lock_file.release()
            except (IOError, BlockingIOError) as e:
                self.logger.info("Unable to acquire lock for {0}, must be already downloading: {1}".format(lock_file_path, e))
        else:
            run_download()

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

        main_logger = initialize_logging(config=app_config, logger_name=f"{args.ID}")

        downloader = VideoDownloader(id=args.ID, config=app_config, logger=main_logger)
        # Call main, passing the config object and the logger
        downloader.main(use_lock_file=True)
        
    except Exception as e:
        # Use the initialized logger for final error handling
        logging.exception("An unhandled error occurred when attempting to download a video")

        raise

