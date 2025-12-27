import os
import json
import traceback
import logging
import threading
import argparse
from time import time
from typing import Optional, Dict, Any

# REMOVED: APScheduler imports
# from apscheduler.schedulers.blocking import BlockingScheduler
# from apscheduler.triggers.interval import IntervalTrigger

# Local imports
from getConfig import ConfigHandler
from common import FileLock, setup_umask, initialize_logging, kill_all
from livestream_dl import getUrls
import discord_web
from livestream_dl.download_Live import LiveStreamDownloader, FileInfo

# Import the Chat class
from getChatOnly import ChatOnlyDownloader
import requests
import random

class UnarchivedDownloader:
    def __init__(self, id, config: ConfigHandler = None, logger: logging.Logger = None, kill_this: threading.Event = None):
        
        if not id:
            raise Exception("No video ID provided, unable to continue")
        
        self.id = id
        self.kill_this = kill_this or threading.Event()
        self.config = config or ConfigHandler()
        self.logger = logger or initialize_logging(self.config, logger_name="unarchived_downloader")
        self.livestream_downloader = LiveStreamDownloader(kill_all=kill_all, logger=self.logger, kill_this=self.kill_this)
        self.info_dict = {}

        # Threading State
        # REMOVED: self.scheduler = BlockingScheduler()
        self.chat_thread: Optional[threading.Thread] = None
        
        try:
            response = requests.get("https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={0}".format(id), timeout=30)
            self.embed_info: Optional[Dict[str, Any]] = response.json() if response.status_code == 200 else {}
        except Exception as e:
            self.logger.warning(f"Could not fetch oembed info: {e}")
            self.embed_info = {}

    def _run_chat_thread(self, json_path, chat_out_path):
        """Worker function to run ChatOnlyDownloader in a separate thread."""
        try:
            chat_dl = ChatOnlyDownloader(
                json_file=json_path, 
                output_path=chat_out_path, 
                config=self.config, 
                logger=self.logger
            )
            chat_dl.main(use_lock_file=False)
        except Exception as e:
            self.logger.error(f"Error in chat thread: {e}")

    def is_video_private(self, video_id: str) -> None:
        """Checks video status, downloads info, thumbnail, and triggers download_private if needed."""
        # Check kill flag at start of operation
        if self.kill_this.is_set() or kill_all.is_set():
            return

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
                self.livestream_downloader.stats["status"] = "Monitoring"
                import subprocess
                os.makedirs(os.path.dirname(json_out_path), exist_ok=True)
                with open(json_out_path, 'w', encoding='utf-8') as json_file:
                    json.dump(info_dict, json_file, ensure_ascii=False, indent=4)
                    self.logger.debug(f"Created/Updated {os.path.abspath(json_out_path)}")

                # --- Optimized Chat Trigger ---
                if self.config.get_unarchived_chat_dl() and info_dict.get('live_status') == 'is_live':
                    if self.chat_thread and self.chat_thread.is_alive():
                        self.logger.debug("Chat download thread is already running.")
                    else:
                        self.logger.info("Stream is live. Starting chat download thread.")
                        self.chat_thread = threading.Thread(
                            target=self._run_chat_thread,
                            args=(json_out_path, chat_out_path),
                            name=f"ChatThread-{video_id}",
                            daemon=True
                        )
                        self.chat_thread.start()
                    self.livestream_downloader.stats["status"] = "Get Chat"
                # ------------------------------

                # Download Thumbnail (Aux)
                aux_options = {
                    'write_thumbnail': True,
                    'temp_folder': temp_folder
                }
                downloaded_aux = self.livestream_downloader.download_auxiliary_files(info_dict=info_dict, options=aux_options)
                
                # Check if we got results back
                if downloaded_aux and len(downloaded_aux) > 0:
                    file_obj = downloaded_aux[0].get('thumbnail', None)

                    # Thumbnail conversion logic
                    if file_obj is not None and file_obj.exists() and not str(file_obj.suffix).endswith(".jpg"):
                        subprocess.run([
                            "ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
                            "-i", str(file_obj.absolute()).replace('%', '%%'),
                            "-q:v", "2",
                            str(jpg_out_path).replace('%', '%%')
                        ], check=True)
                        file_obj.unlink()
                    elif file_obj is not None and file_obj.exists():
                        os.rename(file_obj.absolute(), jpg_out_path)

                return  # Exit successfully if live/post-live

        except getUrls.VideoInaccessibleError as e:
            self.logger.info(f"VideoInaccessibleError for {video_id}: {e}")
            if os.path.exists(json_out_path):
                self.download_private(info_dict_file=json_out_path, thumbnail=jpg_out_path, chat=chat_out_path)
        except getUrls.VideoProcessedError as e:
            self.logger.info(f"({video_id}) {e}")
        except Exception as e:
            self.logger.exception(f"Unexpected exception: {e}")
            try:
                discord_web.main(
                    video_id, "error",
                    message=str(f"{e}\n{traceback.format_exc()}")[-1000:],
                    config=self.config
                )
            except Exception:
                self.logger.exception("Unable to send discord error message")

        # Cleanup old files
        if os.path.exists(json_out_path):
            if not self.check_ytdlp_age(json_out_path):
                if os.path.exists(jpg_out_path):
                    os.remove(jpg_out_path)
                if os.path.exists(chat_out_path):
                    os.remove(chat_out_path)
                
                # REPLACED: scheduler kill with event set
                # If we removed the files because age was > 6 hours or similar logic, 
                # we assume the process is "done" for this video instance? 
                # Or if the download_private finished successfully?
                # Based on original logic, this kills the process.
                self.logger.info("Processing complete or timed out. Stopping monitor.")
                self.livestream_downloader.stats["status"] = "Expired"
                self.kill_this.set()


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
                self.logger.info(f"JSON {os.path.basename(existing_file)} is older than 6 hours, removing...")
                os.remove(existing_file)
                return False
        elif file_age_hours > 6:
            self.logger.info(f"JSON {os.path.basename(existing_file)} is older than 6 hours (mod time), removing...")
            os.remove(existing_file)
            return False

        return True

    def download_private(self, info_dict_file: str, thumbnail: Optional[str] = None, chat: Optional[str] = None) -> None:
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
            self.livestream_downloader.file_names.update({'live_chat': FileInfo(chat, file_type='live_chat')})

        try:
            self.livestream_downloader.stats["status"] = "Recording"
            self.livestream_downloader.download_segments(info_dict=info_dict, resolution='best', options=options)
        except Exception as e:
            self.logger.exception(e)
            self.livestream_downloader.stats["status"] = "Error"
            discord_web.main(
                video_id, "error",
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
        self.livestream_downloader.stats["status"] = "Finished"
        self.kill_this.set()

    def _scheduled_check(self, use_lock_file: bool):
        """The job function called by the loop."""
        if use_lock_file:
            # Determine lock file path
            if os.path.exists("/dev/shm"):
                lock_file_path = f"/dev/shm/unarchived-{self.id}"
            else:
                lock_file_path = os.path.join(self.config.get_temp_folder(), f"unarchived-{self.id}.lockfile")

            try:
                # We use a timeout or non-blocking acquire to avoid piling up threads if one is stuck
                with FileLock(lock_file_path) as lock_file:
                    lock_file.acquire()
                    self.is_video_private(self.id)
                    lock_file.release()
            except (IOError, BlockingIOError):
                self.logger.warning(f"Skipping check for {self.id}: Lock file active (process running).")
        else:
            self.is_video_private(self.id)

    # REMOVED: kill_scheduler and _watchdog_check (no longer needed)

    def start_monitoring(self, interval=3600, use_lock_file=False):
        """
        Starts the monitoring loop (replaces scheduler).
        """
        self.logger.info(f"Starting unarchived monitor for {self.id} with interval {interval}s")
        self.livestream_downloader.stats["status"] = "Waiting"

        # Loop until kill flag is set
        while not self.kill_this.is_set() and not kill_all.is_set():
            try:
                # We calculate the target time we want to wake up for next run
                jitter = random.uniform(0, 120)
                wake_up_time = max(60, interval - jitter)

                # 1. Perform the check
                self._scheduled_check(use_lock_file)
                
                # 2. Check flags again immediately after run (in case it finished processing and set kill flag)
                if self.kill_this.is_set():
                    break
                
                # 3. Wait for the interval
                while time() < wake_up_time:
                    # check triggers immediately
                    if kill_all.is_set() or self.kill_this.wait(timeout=1.0):
                        self.logger.info("Kill signal detected (during wait).")
                        return
                    
            except (KeyboardInterrupt, SystemExit):
                self.logger.info("Monitoring stopping due to interrupt...")
                self.kill_this.set()
                break
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {e}")
                # Optional: Sleep briefly on error to avoid rapid-fire looping if something is broken
                self.kill_this.set()
                if self.kill_this.wait(timeout=5):
                    break

        self.logger.debug("Finished unarchived monitoring of {0}".format(self.id))

    def main(self):
        self.start_monitoring(use_lock_file=False)

if __name__ == "__main__":
    try:
        app_config = ConfigHandler()
        setup_umask() 
        
        parser = argparse.ArgumentParser(description="Process an video by ID with internal scheduling")
        parser.add_argument('ID', type=str, help='The video ID (required)')
        parser.add_argument('--interval', type=int, default=3600, help='Check interval in seconds (default: 3600)')

        args = parser.parse_args()
        
        # Initialize logger
        logger = initialize_logging(config=app_config, logger_name=f"{args.ID}-unarchived")

        downloader = UnarchivedDownloader(id=args.ID, config=app_config, logger=logger)
        
        # Start the long-running process
        downloader.start_monitoring(interval=args.interval, use_lock_file=True)
        
    except Exception as e:
        logging.exception("An unhandled error occurred")
        raise