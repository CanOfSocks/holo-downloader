import os
import json
import logging
import threading
from typing import Optional

from getConfig import ConfigHandler
from common import FileLock, setup_umask, initialize_logging, kill_all
from livestream_dl.download_Live import LiveStreamDownloader

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
        # Use provided logger or create a child logger to avoid messing with root handlers
        self.logger = logger or initialize_logging(self.config, logger_name="chat_downloader")
        self.downloader = LiveStreamDownloader(kill_all=kill_all, logger=self.logger, kill_this=self.kill_this)

    def main(self, use_lock_file=False) -> None:
        # Only setup umask if we are the main thread/process; 
        # skipped here to avoid redundant calls in threaded context
        if threading.current_thread() is threading.main_thread():
             setup_umask()

        # Load info_dict
        try:
            with open(self.json_file, 'r', encoding='utf-8') as f:
                info_dict = json.load(f)
        except Exception as e:
            self.logger.exception(f"Failed to load JSON file {self.json_file}")
            return None

        video_id = info_dict.get('id')

        
        # Build options
        options = {
            "ID": video_id,
            "output": self.output_path or str(self.config.get_unarchived_output_path(self.config.get_ytdlp())),
            "temp_folder": self.config.get_unarchived_temp_folder(),
            "cookies": self.config.get_cookies_file(),
            "log_level": self.config.get_log_level(),
            "log_file": self.config.get_log_file(),
        }

        self.downloader.remove_format_segment_playlist_from_info_dict(info_dict)

        # Determine lock file path
        if os.path.exists("/dev/shm"):
            lock_file_path = f"/dev/shm/chat-{options['ID']}"
        else:
            lock_file_path = os.path.join(
                options["temp_folder"],
                f"chat-{options['ID']}.lockfile"
            )

        # Download Logic
        if use_lock_file:
            try:
                with FileLock(lock_file_path) as lock_file:
                    lock_file.acquire()
                    self.logger.info(f"Starting chat download for {video_id}")
                    return self.downloader.download_live_chat(info_dict=info_dict, options=options)
            except (IOError, BlockingIOError) as e:
                self.logger.info(f"Chat lock active, skipping: {lock_file_path}")
        else:
            return self.downloader.download_live_chat(info_dict=info_dict, options=options)

    

if __name__ == "__main__":
    import argparse
    try:
        # Instantiate ConfigHandler once for the execution flow
        app_config = ConfigHandler()

        # Set up a generic logger for argparse error handling
        
    
    
        # Create the parser
        parser = argparse.ArgumentParser(description="Download chat of a video using info.json")
        parser.add_argument('json', type=str, help='info.json path (required)')
        parser.add_argument('--output-path', type=str, default=None, help='Optional output path')
        args = parser.parse_args()

        logger = initialize_logging(config=app_config, logger_name="chat_downloader")
        chat_downloader = ChatOnlyDownloader(json_file=args.json, output_path=args.output_path, config=app_config)
        # Call main, passing the config object
        chat_downloader.main(use_lock_file=True)
        
    except Exception as e:
        # Use the logger initialized above for final error logging
        logging.exception("An unhandled error occurred when trying to download chat")