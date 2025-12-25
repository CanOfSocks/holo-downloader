import os
import json

import traceback
import logging
from shutil import move
from time import time, sleep
from typing import Optional, Dict, Any

from getConfig import ConfigHandler
from common import FileLock, setup_umask, initialize_logging, kill_all
from livestream_dl import getUrls
import discord_web
from livestream_dl.download_Live import LiveStreamDownloader, FileInfo
import threading
import argparse

class UnarchivedDownloader:
    def __init__(self, id, config: ConfigHandler = None, logger: logging.Logger = None, kill_this: threading.Event = None,):
        if not id:
            raise Exception("No video ID provided, unable to continue")
        
        self.id = id
        self.kill_this = kill_this or threading.Event()
        self.config = config or ConfigHandler()
        self.logger = logger or initialize_logging(self.config, logger_name="unarchived_downloader")
        self.livestream_downloader = LiveStreamDownloader(kill_all=kill_all, logger=self.logger, kill_this=self.kill_this)
        self.info_dict = {}

    def check_ytdlp_age(self, existing_file: str) -> bool:
        """Checks if a JSON info file is older than 6 hours and removes it if so."""
        current_time = time()
        data: Optional[Dict[str, Any]] = None

        if os.path.exists(existing_file):
            try:
                with open(existing_file, 'r', encoding='utf-8') as file:
                    data = json.load(file)
            except Exception:
                data = None

        file_age_hours = (current_time - os.path.getmtime(existing_file)) / 3600.0

        if data and 'epoch' in data:
            data_age_hours = (current_time - data['epoch']) / 3600.0
            if data_age_hours > 6 or file_age_hours > 6:
                self.logger.info(f"JSON {os.path.basename(existing_file)} is older than 6 hours, removing…")
                os.remove(existing_file)
                return False
        elif file_age_hours > 6:
            self.logger.info(f"JSON {os.path.basename(existing_file)} is older than 6 hours (mod time), removing…")
            os.remove(existing_file)
            return False

        return True

    def download_private(
        self,
        info_dict_file: str,
        thumbnail: Optional[str] = None,
        chat: Optional[str] = None
    ) -> None:
        """Downloads a private or post-live video using pre-fetched info."""
        with open(info_dict_file, 'r', encoding='utf-8') as file:
            info_dict = json.load(file)

        video_id = info_dict.get('id', "")
        self.logger.info(f"Attempting to download video: {video_id}")

        discord_web.main(video_id, "recording", config=self.config)

        options = {
            "ID": video_id,
            "resolution": "best",
            "video_format": None,
            "audio_format": None,
            "threads": 20,
            "batch_size": 5,
            "segment_retries": 10,
            "merge": self.config.get_mux(),
            "output": str(self.config.get_unarchived_output_path(self.config.get_ytdlp())),
            "temp_folder": self.config.get_unarchived_temp_folder(),
            "write_thumbnail": self.config.get_thumbnail(),
            "embed_thumbnail": self.config.get_thumbnail(),
            "write_info_json": self.config.get_info_json(),
            "write_description": self.config.get_description(),
            "keep_database_file": False,
            "recovery": True,
            "force_recover_merge": self.config.get_unarchived_force_merge(),
            "recovery_failure_tolerance": self.config.get_unarchived_recovery_failure_tolerance(),
            "database_in_memory": False,
            "direct_to_ts": False,
            "wait_for_video": None,
            "json_file": None,
            "remove_ip_from_json": self.config.get_remove_ip(),
            "log_level": self.config.get_log_level(),
            "log_file": self.config.get_log_file(),
            "write_ffmpeg_command": self.config.get_ffmpeg_command(),
        }

        self.logger.info(f"Output path: {options.get('output')}")

        if thumbnail and os.path.exists(thumbnail):
            self.livestream_downloader.file_names['thumbnail'] = FileInfo(thumbnail, file_type='thumbnail')
        if chat and os.path.exists(chat):
            self.livestream_downloader.file_names.update({
                'live_chat': FileInfo(chat, file_type='live_chat')
            })

        try:
            self.livestream_downloader.download_segments(info_dict=info_dict, resolution='best', options=options)
        except Exception as e:
            self.logger.exception(e)
            discord_web.main(
                video_id,
                "error",
                message=str(f"{e}\n{traceback.format_exc()}")[-1000:],
                config=self.config
            )
            return

        discord_web.main(video_id, "done", config=self.config)

        # Clean up temp files
        if os.path.exists(info_dict_file):
            os.remove(info_dict_file)
        if thumbnail and os.path.exists(thumbnail):
            os.remove(thumbnail)

    def is_video_private(self, video_id: str) -> None:
        """Checks video status, downloads info, thumbnail, and triggers download_private if needed."""
        temp_folder = self.config.get_unarchived_temp_folder()
        json_out_path = os.path.join(temp_folder, f"{video_id}.info.json")
        chat_out_path = os.path.join(temp_folder, f"{video_id}.live_chat.zip")
        jpg_out_path = os.path.join(temp_folder, f"{video_id}.jpg")

        try:
            additional_options = None
            if self.config.get_ytdlp_options():
                additional_options = json.loads(self.config.get_ytdlp_options())

            info_dict, live_status = getUrls.get_Video_Info(
                id=video_id,
                wait=(60, 1800),
                cookies=self.config.get_cookies_file(),
                proxy=self.config.get_proxy(),
                additional_options=additional_options,
                include_dash=False,
                include_m3u8=False,
            )

            if info_dict.get('live_status') in ['is_live', 'post_live']:
                import subprocess
                os.makedirs(os.path.dirname(json_out_path), exist_ok=True)
                with open(json_out_path, 'w', encoding='utf-8') as json_file:
                    json.dump(info_dict, json_file, ensure_ascii=False, indent=4)
                    self.logger.debug(f"Created {os.path.abspath(json_out_path)}")

                if self.config.get_unarchived_chat_dl() and info_dict.get('live_status') == 'is_live':
                    chat_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'getChatOnly.py')
                    command = [
                        "python", chat_script,
                        '--output-path', chat_out_path,
                        '--', json_out_path
                    ]
                    subprocess.Popen(command, start_new_session=True)

                aux_options = {
                    'write_thumbnail': True,
                    'temp_folder': temp_folder
                }
                downloaded_aux = self.livestream_downloader.download_auxiliary_files(info_dict=info_dict, options=aux_options)
                file_obj = downloaded_aux[0].get('thumbnail', None)

                if file_obj is not None and file_obj.exists() and not str(file_obj.suffix).endswith(".jpg"):
                    subprocess.run([
                        "ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
                        "-i", file_obj.absolute(),
                        "-q:v", "2",
                        jpg_out_path
                    ], check=True)
                    self.logger.debug(f"Deleting original thumbnail: {file_obj.absolute()}")
                    file_obj.unlink()
                elif file_obj is not None and file_obj.exists():
                    os.rename(file_obj.absolute(), jpg_out_path)

                return  # exit successfully if live/post-live

        except getUrls.VideoInaccessibleError as e:
            self.logger.info(f"VideoInaccessibleError while checking {video_id}: {e}")
            if os.path.exists(json_out_path):
                self.download_private(info_dict_file=json_out_path, thumbnail=jpg_out_path, chat=chat_out_path)
        except getUrls.VideoProcessedError as e:
            self.logger.info(f"({video_id}) {e}")
        except Exception as e:
            self.logger.exception(f"Unexpected exception occurred: {e}")
            try:
                discord_web.main(
                    video_id,
                    "error",
                    message=str(f"{e}\n{traceback.format_exc()}")[-1000:],
                    config=self.config
                )
            except Exception as e_discord:
                self.logger.exception("Unable to send discord error message")

        # Cleanup
        if os.path.exists(json_out_path):
            if not self.check_ytdlp_age(json_out_path):
                if os.path.exists(jpg_out_path):
                    os.remove(jpg_out_path)
                if os.path.exists(chat_out_path):
                    os.remove(chat_out_path)

    def main(self, use_lock_file=False) -> None:
        """
        Entry point: acquires lock and triggers processing logic.
        """
        
        if use_lock_file:
            # Determine lock file path
            if os.path.exists("/dev/shm"):
                lock_file_path = f"/dev/shm/unarchived-{self.id}"
            else:
                lock_file_path = os.path.join(
                    self.config.get_temp_folder(),
                    f"unarchived-{self.id}.lockfile"
                )

            try:
                with FileLock(lock_file_path) as lock_file:
                    lock_file.acquire()
                    self.is_video_private(self.id)
                    lock_file.release()
            except (IOError, BlockingIOError) as e:
                self.logger.warning(f"Unable to acquire lock for {lock_file_path}, must be already downloading: {e}")
        else:
            self.is_video_private(self.id)

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
        
        logger = initialize_logging(config=app_config, logger_name=f"{args.ID}-unarchived")
        # Call main, passing the config object
        # The main function will create the final logger instance specific to the ID

        unarchived_downloader = UnarchivedDownloader(id=args.ID, config=app_config, logger=logger)
        unarchived_downloader.main(use_lock_file=True)
        
    except Exception as e:
        logging.exception("An unhandled error occurred when trying to run the unarchived stream checker")
        raise
