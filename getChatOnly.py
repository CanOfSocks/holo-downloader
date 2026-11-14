"""Live chat only download from a saved info.json (used by the unarchived monitor)."""
import json
import logging
import threading
from typing import Optional

from common import initialize_logging, kill_all
from getConfig import ConfigHandler
from livestream_dl.download_Live import LiveStreamDownloader


class ChatOnlyDownloader:
    def __init__(self, json_file: str, output_path: Optional[str] = None, config: Optional[ConfigHandler] = None,
                 logger: Optional[logging.Logger] = None, kill_this: Optional[threading.Event] = None):
        self.json_file: str = json_file
        self.output_path: Optional[str] = output_path
        # Pass the owning job's event so cancelling the job also stops the chat download
        self.kill_this: threading.Event = kill_this or threading.Event()
        self.config: ConfigHandler = config or ConfigHandler()
        self.logger = logger or initialize_logging(self.config, logger_name="chat_downloader")
        self.downloader = LiveStreamDownloader(kill_all=kill_all, logger=self.logger, kill_this=self.kill_this)

    def main(self):
        try:
            with open(self.json_file, 'r', encoding='utf-8') as f:
                info_dict = json.load(f)
        except Exception:
            self.logger.exception(f"Failed to load JSON file {self.json_file}")
            return None

        options = {
            "ID": info_dict.get('id'),
            "output": self.output_path or str(self.config.get_unarchived_output_path(self.config.get_ytdlp())),
            "temp_folder": self.config.get_unarchived_temp_folder(),
            "cookies": self.config.get_cookies_file(),
            "proxy": self.config.get_proxy(),
            "log_level": self.config.get_log_level(),
            "log_file": self.config.get_log_file(),
        }
        # Formats aren't needed for chat and can be large
        self.downloader.remove_format_segment_playlist_from_info_dict(info_dict)
        self.logger.info(f"Starting chat download for {info_dict.get('id')}")
        return self.downloader.download_live_chat(info_dict=info_dict, options=options)
