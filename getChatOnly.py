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

class ChatOnlyDownloader:
    def __init__(
        self,
        json_file: str,
        output_path: str = None,
        config: ConfigHandler = None,
        logger: logging.Logger = None,
        kill_this: threading.Event = None,
    ):
        self.json_file = json_file
        self.output_path = output_path
        self.kill_this = kill_this or threading.Event()
        self.config = config or ConfigHandler()
        self.logger = logger or initialize_logging(self.config, logger_name="chat_downloader")
        self.downloader = LiveStreamDownloader(kill_all=kill_all, logger=self.logger, kill_this=self.kill_this)

    def run(self, use_lock_file=False) -> None:
        # Setup environment
        common.setup_umask()

        # Load info_dict
        try:
            with open(self.json_file, 'r', encoding='utf-8') as f:
                info_dict = json.load(f)
        except Exception as e:
            self.logger.exception("Failed to load JSON file %s", self.json_file, exc_info=e)
            return None

        # Re-initialize logger using video ID
        video_id = info_dict.get('id')
        self.logger = initialize_logging(self.config, logger_name=f"{video_id}-chat")

        # Build options
        options = {
            "ID": video_id,
            "output": self.output_path or str(self.config.get_unarchived_output_path(self.config.get_ytdlp())),
            "temp_folder": self.config.get_unarchived_temp_folder(),
            "cookies": self.config.get_cookies_file(),
            "log_level": self.config.get_log_level(),
            "log_file": self.config.get_log_file(),
        }

        # Determine lock file path
        if os.path.exists("/dev/shm"):
            lock_file_path = f"/dev/shm/chat-{options['ID']}"
        else:
            lock_file_path = os.path.join(
                options["temp_folder"],
                f"chat-{options['ID']}.lockfile"
            )

        # Acquire lock and download
        if use_lock_file:
            try:
                with common.FileLock(lock_file_path) as lock_file:
                    lock_file.acquire()
                    result = self.downloader.download_live_chat(info_dict=info_dict, options=options)
                    lock_file.release()
                    return result
            except (IOError, BlockingIOError) as e:
                self.logger.info("Unable to acquire lock for %s, must be already downloading: %s",
                                lock_file_path, e)
        else:
            return self.downloader.download_live_chat(info_dict=info_dict, options=options)

    

if __name__ == "__main__":
    try:
        # Instantiate ConfigHandler once for the execution flow
        app_config = ConfigHandler()

        # Set up a generic logger for argparse error handling
        
    
    
        # Create the parser
        parser = argparse.ArgumentParser(description="Download chat of a video using info.json")
        parser.add_argument('json', type=str, help='info.json path (required)')
        parser.add_argument('--output-path', type=str, default=None, help='Optional output path')
        args = parser.parse_args()

        logger = initialize_logging(app_config, logger_name="chat_downloader")
        chat_downloader = ChatOnlyDownloader(json_file=args.json, output_path=args.output_path, config=app_config)
        # Call main, passing the config object
        chat_downloader.run(use_lock_file=True)
        
    except Exception as e:
        # Use the logger initialized above for final error logging
        logging.exception("An unhandled error occurred when trying to download chat")