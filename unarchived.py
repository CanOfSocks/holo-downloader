"""
Unarchived stream monitor.

While a monitored stream is live (or post-live), its info.json (which contains the stream URLs)
is saved to the unarchived temp folder and refreshed every URL_REFRESH_INTERVAL. If the video
becomes private, livestream_dl's stream recovery downloads it using the saved URLs, which stay
valid for about 6 hours after extraction.

To notice a privated stream quickly without frequent yt-dlp extractions, a cheap oEmbed request
is made every CHEAP_CHECK_INTERVAL. A suspicious oEmbed response is always confirmed with a full
extraction before recovery starts, as oEmbed alone is not reliable (it also returns 401 for
videos that have embedding disabled).
"""
import enum
import json
import logging
import os
import random
import subprocess
import threading
import time
import traceback
from typing import Optional, Union

import common
import discord_web
from base_downloader import (CONFIRM_DELAY, STATUS_CANCELLED, STATUS_FINISHED, STATUS_STOPPED_MERGED,
                             STATUS_WAITING, BaseDownloader, next_upcoming_check_delay)
from common import DiscoveredVideo, WaitResult
from getChatOnly import ChatOnlyDownloader
from getConfig import ConfigHandler, MINIMUM_RECHECK_SECONDS
from livestream_dl import getUrls
from livestream_dl.download_Live import FileInfo, LiveStreamDownloader

# --- Timings (seconds) ---
CHEAP_CHECK_INTERVAL = 600            # oEmbed availability check
CHEAP_CHECK_JITTER = 60               # +/- random jitter on each cheap check
CHEAP_CHECK_MAX_INTERVAL = 3600       # Upper limit when backing off after HTTP 429
URL_REFRESH_INTERVAL = 6600           # 1h50m: full extraction to refresh the saved stream URLs
URL_REFRESH_EARLY_JITTER = 300        # Refreshes happen up to this much earlier, never later
URL_REFRESH_RETRY = 1200              # Retry delay after a failed extraction
RECOVERY_RETRY_DELAY = 3600           # Wait before retrying a failed recovery (while the URLs are still valid)
INFO_JSON_MAX_AGE = 6 * 3600          # Saved URLs expire after about 6 hours, so the monitor stops then
# While URL refreshes fail (rate limiting, YouTube's bot check), whether the video turned private
# can't be confirmed. So the saved URLs aren't wasted, recovery is started with them this long before
# they expire if the refreshes are still failing then.
RECOVER_BEFORE_EXPIRY = 1800
CHAT_THREAD_JOIN_TIMEOUT = 15

STATUS_MONITORING = "Monitoring"
STATUS_EXPIRED = "Expired"
STATUS_UNAVAILABLE = "Unavailable"
STATUS_PUBLIC = "Public archive"


class CheapCheck(enum.Enum):
    OK = "ok"                      # 200: still public/unlisted
    SUSPICIOUS = "suspicious"      # 401/403/404: possibly private, removed or members-only
    RATE_LIMITED = "rate_limited"  # 429
    UNKNOWN = "unknown"            # 5xx, timeout or network error: try again next time


def classify_oembed(status_code: Optional[int]) -> CheapCheck:
    if status_code == 200:
        return CheapCheck.OK
    if status_code in (401, 403, 404):
        return CheapCheck.SUSPICIOUS
    if status_code == 429:
        return CheapCheck.RATE_LIMITED
    return CheapCheck.UNKNOWN


class Refresh(enum.Enum):
    SAVED = "saved"            # Live/post-live: info.json saved with fresh URLs
    UPCOMING = "upcoming"      # Not started yet
    NOT_READY = "not_ready"    # Live but no formats yet
    PRIVATE = "private"        # Inaccessible (private, members-only, removed...)
    UNCONFIRMED = "unconfirmed"  # Reported private (or an unexpected status) once: checked again first (see report_needs_confirmation)
    PROCESSED = "processed"    # Archive is public, so there is nothing to recover
    RATE_LIMITED = "rate_limited"  # Rate limited or stopped by YouTube's bot check
    FAILED = "failed"          # Any other error (network, ...)


class UnarchivedDownloader(BaseDownloader):
    kind = "unarchived"

    def __init__(self, video: Union[DiscoveredVideo, str], config: Optional[ConfigHandler] = None,
                 logger: Optional[logging.Logger] = None, kill_this: Optional[threading.Event] = None):
        super().__init__(video, config=config, logger=logger, kill_this=kill_this, logger_name="unarchived_downloader")
        temp_folder = self.config.get_unarchived_temp_folder()
        self.json_path: str = os.path.join(temp_folder, f"{self.id}.info.json")
        self.chat_path: str = os.path.join(temp_folder, f"{self.id}.live_chat.zip")
        self.thumbnail_path: str = os.path.join(temp_folder, f"{self.id}.jpg")
        self.chat_thread: Optional[threading.Thread] = None

        # Epoch times, shown in the web UI
        self.json_saved_at: Optional[float] = None
        self.last_cheap_check: Optional[float] = None
        self.next_url_refresh: float = 0.0           # 0 = due now
        self.next_cheap_check: Optional[float] = None  # None = cheap checks not active

        self.cheap_check_interval: float = CHEAP_CHECK_INTERVAL
        # oEmbed status code seen right after a successful extraction (see _record_baseline)
        self.oembed_baseline: Optional[int] = None
        self._cheap_check_unusable_logged: bool = False
        # Set once the archive is public; URL refreshes are then no longer useful
        self.processed: bool = False
        # Recovery was started because refreshes failed with the saved URLs close to expiry (see _recover_before_expiry)
        self._expiry_recovery_tried: bool = False
        # Set by the first recover(); later attempts use a new LiveStreamDownloader
        self._recovery_attempted: bool = False

    def notify(self, status: str, message: Optional[str] = None) -> None:
        discord_web.notify(self.id, status, message=message, config=self.config, embed_info=self.embed_info or None)

    # --- Main loop ---

    def main(self) -> None:
        self.load_embed_info()
        self.set_status(STATUS_WAITING)
        self.logger.info(f"Starting unarchived monitor for {self.id}")
        try:
            self._monitor()
        except KeyboardInterrupt:
            self.set_status(STATUS_CANCELLED)
        finally:
            self._stop_chat_thread()
        self.logger.debug("Finished unarchived monitoring of {0}".format(self.id))

    def _monitor(self) -> None:
        self.json_saved_at = self._saved_json_time()
        while not (common.kill_all.is_set() or self.kill_this.is_set()):
            if self._json_expired():
                self.logger.info("Saved info.json is older than {0} hours, stopping monitor".format(INFO_JSON_MAX_AGE // 3600))
                self._remove_temp_files(include_chat=True)
                self.set_status(STATUS_EXPIRED)
                return

            now = time.time()
            outcome: Optional[Refresh] = None
            suspicious = False
            if self._force_check or now >= self.next_url_refresh:
                self._force_check = False
                outcome = self.refresh_urls()
            elif self.next_cheap_check is not None and now >= self.next_cheap_check:
                if self.cheap_check() is CheapCheck.SUSPICIOUS:
                    self.logger.info("oEmbed check suggests the video is no longer accessible, confirming with a full extraction")
                    suspicious = True
                    outcome = self.refresh_urls()

            if outcome in (Refresh.RATE_LIMITED, Refresh.FAILED) and self._recover_before_expiry(suspicious):
                if self.recover():
                    return
                self.logger.info("Recovery with the expiring stream URLs failed, monitoring until they expire")
                continue

            if outcome is Refresh.PRIVATE:
                if not os.path.exists(self.json_path):
                    self.logger.info("Video is inaccessible and no stream URLs were saved, nothing to recover")
                    self.set_status(STATUS_UNAVAILABLE)
                    return
                if self.recover():
                    return
                # Retry later while the saved URLs may still be valid. The video is known to be
                # private, so cheap checks (and their confirming extractions) pause until then.
                self.next_url_refresh = time.time() + RECOVERY_RETRY_DELAY
                self.next_cheap_check = None
                self.logger.info("Recovery failed, retrying in {0:.0f} minutes".format(RECOVERY_RETRY_DELAY / 60))
            elif outcome is Refresh.PROCESSED and not os.path.exists(self.json_path):
                self.logger.info("Stream archive is public and no stream URLs were saved, stopping monitor")
                self.set_status(STATUS_PUBLIC)
                return

            if self.stop_if_beyond_look_ahead():
                return
            if self._wait_for_next_event() is WaitResult.STOPPED:
                break
        # Cancelled from the web UI, or the application is shutting down
        self.set_status(STATUS_CANCELLED)

    def _wait_for_next_event(self) -> WaitResult:
        """Sleep until the next refresh, cheap check, recovery retry or json expiry is due."""
        due = [self.next_url_refresh]
        if self.next_cheap_check is not None:
            due.append(self.next_cheap_check)
        if self.json_saved_at is not None:
            due.append(self.json_saved_at + INFO_JSON_MAX_AGE)
        next_due = min(due)
        self.next_check_at = next_due
        result = self.wait(max(1.0, next_due - time.time()))
        if result is WaitResult.WOKEN and not self._force_check and self.status == STATUS_WAITING:
            # A channel scan changed the scheduled start: recalculate when to check again
            self.next_url_refresh = time.time() + self._upcoming_delay()
        return result

    def _recovery_deadline(self) -> Optional[float]:
        """When recovery is started if URL refreshes are still failing (see RECOVER_BEFORE_EXPIRY)."""
        if self.json_saved_at is None:
            return None
        return self.json_saved_at + INFO_JSON_MAX_AGE - RECOVER_BEFORE_EXPIRY

    def _retry_after_failure(self, now: float) -> float:
        """Next refresh after a failed one: URL_REFRESH_RETRY later, but not after the recovery deadline."""
        retry = now + URL_REFRESH_RETRY
        deadline = self._recovery_deadline()
        if deadline is not None and now < deadline and not self._expiry_recovery_tried:
            retry = min(retry, deadline)
        return retry

    def _recover_before_expiry(self, suspicious: bool) -> bool:
        """
        True (once) if recovery should start because refreshes failed and the saved URLs expire
        within RECOVER_BEFORE_EXPIRY. Skipped once the archive is known to be public, unless the
        cheap check has since reported the video inaccessible.
        """
        deadline = self._recovery_deadline()
        if self._expiry_recovery_tried or deadline is None or time.time() < deadline or not os.path.exists(self.json_path):
            return False
        if self.processed and not suspicious:
            return False
        self._expiry_recovery_tried = True
        self.logger.warning("URL refreshes are failing and the saved stream URLs expire in {0:.0f} minutes. "
                            "Starting stream recovery with them, as it can't be confirmed whether the video was privated".format(
                                max(0.0, self.json_saved_at + INFO_JSON_MAX_AGE - time.time()) / 60))
        return True

    def _upcoming_delay(self) -> float:
        return next_upcoming_check_delay(self.scheduled_start, time.time(), self.config.get_upcoming_recheck_max(),
                                         self.config.upcoming_video_max_wait())

    # --- Full extraction ---

    def refresh_urls(self) -> Refresh:
        """Full yt-dlp extraction. Saves the info.json if the stream is live or post-live."""
        now = time.time()
        try:
            info_dict, live_status = self.extract_info(include_dash=False, include_m3u8=False)
        except (getUrls.VideoInaccessibleError, getUrls.VideoProcessedError) as e:
            # A private report, or an upcoming stream reported with an unexpected status, is checked
            # once more before it is believed (see report_needs_confirmation)
            if self.report_needs_confirmation(e):
                self.next_url_refresh = now + CONFIRM_DELAY
                return Refresh.UNCONFIRMED
            if isinstance(e, getUrls.VideoProcessedError):
                return self._archive_is_public(e, now)
            self.logger.info(f"Video is inaccessible: {e}")
            return Refresh.PRIVATE
        except getUrls.RateLimitException as e:
            # Expected now and then: retried without an error notification each time
            self.logger.warning(f"URL refresh was rate limited or stopped by YouTube's bot check: {e}")
            self.next_url_refresh = self._retry_after_failure(now)
            return Refresh.RATE_LIMITED
        except Exception as e:
            self.logger.exception(f"Unexpected exception: {e}")
            self.notify("error", message=f"{e}\n{traceback.format_exc()}"[-1000:])
            self.next_url_refresh = self._retry_after_failure(now)
            return Refresh.FAILED

        self.hinted_live_status = live_status
        if live_status == "is_upcoming":
            self.scheduled_start = info_dict.get('release_timestamp') or self.scheduled_start
            self.set_status(STATUS_WAITING)
            self.next_url_refresh = now + self._upcoming_delay()
            return Refresh.UPCOMING
        if not info_dict.get('formats'):
            self.logger.info("Stream is {0} but no formats are available yet".format(live_status))
            self.next_url_refresh = now + MINIMUM_RECHECK_SECONDS
            return Refresh.NOT_READY

        self._save_info_json(info_dict)
        self.set_status(STATUS_MONITORING)
        self.next_url_refresh = now + URL_REFRESH_INTERVAL - random.uniform(0, URL_REFRESH_EARLY_JITTER)
        if live_status == 'is_live' and self.config.get_unarchived_chat_dl():
            self._start_chat_thread()
        if not os.path.exists(self.thumbnail_path):
            self._download_thumbnail(info_dict)
        self._record_baseline()
        return Refresh.SAVED

    def _archive_is_public(self, error: BaseException, now: float) -> Refresh:
        self.logger.info(f"Stream archive is available: {error}")
        self.processed = True
        # Cheap checks keep watching for the video being privated until the saved URLs
        # expire. If they can't be used (see _record_baseline), keep doing full extractions.
        if self.next_cheap_check is None and os.path.exists(self.json_path):
            self._record_baseline()
        self.next_url_refresh = float('inf') if self.next_cheap_check is not None else now + URL_REFRESH_INTERVAL
        return Refresh.PROCESSED

    def _save_info_json(self, info_dict: dict) -> None:
        os.makedirs(os.path.dirname(self.json_path) or ".", exist_ok=True)
        # Write to a temporary file first so a crash mid-write can't leave a corrupt info.json
        temp_path = self.json_path + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as json_file:
            json.dump(info_dict, json_file, ensure_ascii=False, indent=4)
        os.replace(temp_path, self.json_path)
        self.json_saved_at = time.time()
        self.logger.debug(f"Created/Updated {os.path.abspath(self.json_path)}")

    def _saved_json_time(self) -> Optional[float]:
        """Time the saved info.json was extracted: its "epoch" field or modification time, whichever is older."""
        if not os.path.exists(self.json_path):
            return None
        times = [os.path.getmtime(self.json_path)]
        try:
            with open(self.json_path, 'r', encoding='utf-8') as file:
                epoch = json.load(file).get('epoch')
            if epoch:
                times.append(float(epoch))
        except Exception:
            self.logger.warning("Unable to read {0}".format(self.json_path))
        return min(times)

    def _json_expired(self) -> bool:
        return self.json_saved_at is not None and time.time() - self.json_saved_at > INFO_JSON_MAX_AGE

    # --- Cheap check ---

    def _record_baseline(self) -> None:
        """
        Check oEmbed right after a successful extraction, while the video is known to be accessible.
        If oEmbed already reports an error (usually because embedding is disabled), it can't detect
        the video being privated, so cheap checks are turned off and the URL refreshes are relied on.
        """
        result = classify_oembed(common.fetch_oembed(self.id, proxy=self.config.get_proxy()).status_code)
        self.last_cheap_check = time.time()
        if result is CheapCheck.SUSPICIOUS:
            if self.next_cheap_check is not None or not self._cheap_check_unusable_logged:
                self.logger.info("oEmbed reports an error although the video is accessible (embedding is probably disabled). "
                                 "Relying on URL refreshes to detect the video being privated.")
                self._cheap_check_unusable_logged = True
            self.oembed_baseline = None
            self.next_cheap_check = None
            return
        self.oembed_baseline = 200 if result is CheapCheck.OK else None
        self._schedule_cheap_check()

    def _schedule_cheap_check(self) -> None:
        self.next_cheap_check = time.time() + self.cheap_check_interval + random.uniform(-CHEAP_CHECK_JITTER, CHEAP_CHECK_JITTER)

    def cheap_check(self) -> CheapCheck:
        result = classify_oembed(common.fetch_oembed(self.id, proxy=self.config.get_proxy()).status_code)
        self.last_cheap_check = time.time()
        if result is CheapCheck.RATE_LIMITED:
            self.cheap_check_interval = min(self.cheap_check_interval * 2, CHEAP_CHECK_MAX_INTERVAL)
            self.logger.warning("oEmbed check was rate limited, next check in {0:.0f}s".format(self.cheap_check_interval))
        elif result is CheapCheck.OK:
            self.cheap_check_interval = CHEAP_CHECK_INTERVAL
            self.oembed_baseline = 200
        self._schedule_cheap_check()
        self.logger.debug("oEmbed check result: {0}".format(result.value))
        return result

    # --- Chat and thumbnail ---

    def _run_chat_thread(self) -> None:
        try:
            ChatOnlyDownloader(json_file=self.json_path, output_path=self.chat_path, config=self.config,
                               logger=self.logger, kill_this=self.kill_this).main()
        except Exception as e:
            self.logger.error(f"Error in chat thread: {e}")

    def _start_chat_thread(self) -> None:
        if self.chat_thread and self.chat_thread.is_alive():
            return
        self.logger.info("Stream is live. Starting chat download thread.")
        self.chat_thread = threading.Thread(target=self._run_chat_thread, name=f"ChatThread-{self.id}", daemon=True)
        self.chat_thread.start()

    def _stop_chat_thread(self) -> None:
        """The chat thread shares kill_this, so setting it (the job is ending anyway) stops the chat download."""
        if self.chat_thread and self.chat_thread.is_alive():
            self.kill_this.set()
            self.chat_thread.join(CHAT_THREAD_JOIN_TIMEOUT)
        self.chat_thread = None

    def _download_thumbnail(self, info_dict: dict) -> None:
        try:
            downloaded = self.livestream_downloader.download_auxiliary_files(
                info_dict=info_dict, options={'write_thumbnail': True, 'temp_folder': self.config.get_unarchived_temp_folder()})
            file_obj = downloaded[0].get('thumbnail', None) if downloaded else None
            if file_obj is None or not file_obj.exists():
                return
            if str(file_obj.suffix).endswith(".jpg"):
                os.replace(file_obj.absolute(), self.thumbnail_path)
            else:
                subprocess.run([
                    "ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
                    "-i", str(file_obj.absolute()).replace("%", "％"),
                    "-q:v", "2",
                    str(self.thumbnail_path).replace("%", "％"),
                ], check=True, timeout=120)
                file_obj.unlink()
        except Exception:
            # The thumbnail is optional, so a failure doesn't stop monitoring
            self.logger.exception("Unable to download thumbnail")

    # --- Recovery ---

    def _remove_temp_files(self, include_chat: bool = False) -> None:
        paths = [self.json_path, self.thumbnail_path] + ([self.chat_path] if include_chat else [])
        for path in paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as e:
                self.logger.warning(f"Unable to remove {path}: {e}")
        self.json_saved_at = None

    def recover(self) -> bool:
        """Download the privated stream from the saved info.json. Returns True if it succeeded."""
        with open(self.json_path, 'r', encoding='utf-8') as file:
            info_dict = json.load(file)
        self.logger.info(f"Attempting to recover video: {self.id}")

        if self._recovery_attempted:
            # Start every retry with a fresh downloader, so nothing from the failed attempt carries
            # over (its files, stats, or a "Stop & merge" that would end the new attempt at once)
            self.livestream_downloader = LiveStreamDownloader(kill_all=common.kill_all, logger=self.logger, kill_this=self.kill_this)
        self._recovery_attempted = True
        self.notify("recording")
        options = self.config.get_unarchived_livestream_dl_options(info_dict)
        self.logger.info(f"Output path: {options.get('output')}")

        if os.path.exists(self.thumbnail_path):
            self.livestream_downloader.file_names['thumbnail'] = FileInfo(self.thumbnail_path, file_type='thumbnail')
        if os.path.exists(self.chat_path):
            self.livestream_downloader.file_names['live_chat'] = FileInfo(self.chat_path, file_type='live_chat')

        try:
            self.start_recording(info_dict)
            # Recovery always selects the best formats ("bv+ba/best"), as before
            self.livestream_downloader.download_segments(info_dict=info_dict, resolution='bv+ba/best', options=options)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.logger.exception(e)
            self.set_error(e)
            self.notify("error", message=f"{e}\n{traceback.format_exc()}"[-1000:])
            return False

        self.notify("done")
        self._remove_temp_files()
        self.set_status(STATUS_STOPPED_MERGED if self.merge_requested else STATUS_FINISHED)
        return True
