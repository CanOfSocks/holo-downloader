"""
Behaviour shared by the normal downloader (downloadVid.VideoDownloader) and the unarchived
monitor (unarchived.UnarchivedDownloader): waiting for an upcoming stream, accepting updates
from channel scans, and the controls used by the web UI (cancel, check now, stop & merge).
"""
import copy
import errno
import logging
import random
import threading
import time
from typing import Any, Dict, Optional, Union

import yt_dlp

import common
from common import DiscoveredVideo, WaitResult, kill_all
from getConfig import ConfigHandler, MINIMUM_RECHECK_SECONDS
from livestream_dl import getUrls
from livestream_dl.download_Live import LiveStreamDownloader

# While a stream is late, the delay before the next check is a quarter of how late it is,
# between MINIMUM_RECHECK_SECONDS and `upcoming_wait_for_video_max`. A stream that starts a few
# minutes late is picked up quickly; one that is hours late (or never starts) is checked rarely.
LATE_BACKOFF_DIVISOR = 4

# A check delay that is capped at `upcoming_recheck_max` or `upcoming_wait_for_video_max` is
# shortened by a random amount of up to this fraction of it. Streams found in the same channel scan
# (or late at the same time) would otherwise all be checked in the same instant every cycle, and
# bursts of extractions are what trigger rate limiting.
MAX_DELAY_JITTER_FRACTION = 0.1

# An extraction reporting the video private, or an upcoming stream as ended/processed, is only
# believed when the next one, this many seconds later, reports it again. One degraded extraction
# ("No title found in player responses") has been seen reporting a working stream as private, and
# livestream_dl confirms such reports the same way.
CONFIRM_DELAY = 60

# yt-dlp live statuses of a stream that is over and has been (or is being) processed
ENDED_LIVE_STATUSES = ("was_live", "not_live")

# Status shown in the web UI and stored in history. livestream_dl sets its own statuses
# ("Starting", "Recording", "Muxing", "Moving", "Finished", "Cancelled") in the same place.
STATUS_WAITING = "Waiting"
STATUS_CANCELLED = "Cancelled"
STATUS_ERROR = "Error"
STATUS_FINISHED = "Finished"
STATUS_STOPPED_MERGED = "Stopped (merged)"
# The scheduled start moved beyond `look_ahead`; a later channel scan starts it again once it is within the window
STATUS_BEYOND_LOOK_AHEAD = "Beyond look-ahead"


# Short reasons for an error, shown next to the status in the web UI's history. Checked in order
# against the message of a VideoInaccessibleError (see getUrls._classify_download_error).
_INACCESSIBLE_REASONS = (
    ("member", "Membership"),
    ("premium", "Membership"),
    ("private", "Private"),
    ("age-restricted", "Age restricted"),
    ("region-locked", "Region locked"),
    ("copyright", "Copyright claim"),
    ("terminated", "Terminated"),
    ("recording is not available", "Recording unavailable"),
    ("removed", "Removed"),
    ("not available on this player", "Player unsupported"),
)


class UnexpectedStatusError(getUrls.VideoProcessedError):
    """
    A stream expected to be upcoming was reported as something other than upcoming, live or
    ended-but-downloadable (e.g. "not_live", or no live status at all). Treated like a processed
    video once confirmed by a second extraction (see report_needs_confirmation).
    """


def describe_error(error: BaseException) -> str:
    """A brief reason for a failed download (e.g. "Membership", "Private", "Processed")."""
    if isinstance(error, yt_dlp.utils.DownloadError):
        # Unclassified yt-dlp errors: classify them the same way as livestream_dl does
        classified = getUrls._classify_download_error(error, "")
        if classified is error:
            return "yt-dlp error"
        error = classified
    if isinstance(error, getUrls.VideoInaccessibleError):
        message = str(error).lower()
        return next((reason for text, reason in _INACCESSIBLE_REASONS if text in message), "Inaccessible")
    if isinstance(error, getUrls.VideoProcessedError):
        return "Processed"
    if isinstance(error, getUrls.VideoUnavailableError):
        return "Unavailable"
    if isinstance(error, getUrls.RateLimitException):
        return "Rate limited"
    if isinstance(error, getUrls.LivestreamError):
        return "Stream ended"
    if isinstance(error, getUrls.VideoDownloadError):
        return "Missing segments"
    if isinstance(error, getUrls.MaxRetryExceededError):
        return "Retries exceeded"
    if isinstance(error, TimeoutError):
        return "URLs expired" if "expir" in str(error).lower() else "Timed out"
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return "Disk full"
    return type(error).__name__


def next_upcoming_check_delay(scheduled_start: Optional[float], now: float, recheck_max: float, late_max: float) -> float:
    """
    Seconds to wait before checking an upcoming stream again.

    - Start time unknown:         late_max.
    - Before the scheduled start: until the scheduled start, but never longer than recheck_max.
    - After the scheduled start:  (seconds late / LATE_BACKOFF_DIVISOR), capped at late_max.
    A delay capped at recheck_max or late_max is shortened by up to MAX_DELAY_JITTER_FRACTION
    (never lengthened, so a check is never later than the maximum or the scheduled start).
    The result is never below MINIMUM_RECHECK_SECONDS.
    """
    if scheduled_start is None:
        delay, cap = late_max, late_max
    elif scheduled_start > now:
        delay, cap = scheduled_start - now, recheck_max
    else:
        delay, cap = (now - scheduled_start) / LATE_BACKOFF_DIVISOR, late_max
    if delay >= cap:
        delay = cap * (1 - random.uniform(0, MAX_DELAY_JITTER_FRACTION))
    return max(delay, MINIMUM_RECHECK_SECONDS)


class BaseDownloader:
    """Common state and controls. Subclasses implement main(), which runs on the job's thread."""

    kind: str = "unknown"

    def __init__(self, video: Union[DiscoveredVideo, str], config: Optional[ConfigHandler] = None,
                 logger: Optional[logging.Logger] = None, kill_this: Optional[threading.Event] = None,
                 logger_name: str = "Downloader"):
        if isinstance(video, str):
            video = DiscoveredVideo(video)
        if not video.id:
            raise ValueError("No video ID provided, unable to continue")

        self.id: str = video.id
        self.channel_id: Optional[str] = video.channel_id
        self.config: ConfigHandler = config or ConfigHandler()
        self.logger = logger or common.initialize_logging(self.config, logger_name=logger_name, video_id=self.id)

        # kill_this cancels this job only. kill_all (in common) stops everything on shutdown.
        self.kill_this: threading.Event = kill_this or threading.Event()
        # Ends a wait early without cancelling (channel scan update or "check now")
        self.wake: threading.Event = threading.Event()
        self.livestream_downloader = LiveStreamDownloader(kill_all=kill_all, logger=self.logger, kill_this=self.kill_this)

        # Information from the channel scan (and later from extractions) about an upcoming stream
        self.scheduled_start: Optional[float] = video.release_timestamp
        self.hinted_live_status: Optional[str] = video.live_status
        # Jobs from channel scans stop waiting if the stream is rescheduled beyond the look-ahead window
        self.enforce_look_ahead: bool = not video.manual
        # Set when the next check should extract straight away instead of recalculating the wait
        self._force_check: bool = False
        # Epoch time of the next planned check, shown in the web UI while waiting
        self.next_check_at: Optional[float] = None

        # Set when recording begins (see start_recording): epoch time recording began, and the
        # stream's own start time when YouTube reports it. Shown in the web UI's "Started" column.
        self.recording_started_at: Optional[float] = None
        self.stream_started_at: Optional[float] = None

        # Brief reason for the last error (see describe_error), saved in history with STATUS_ERROR
        self.error_reason: Optional[str] = None
        # Consecutive extractions reporting each kind of unconfirmed status (see report_needs_confirmation)
        self._unconfirmed_reports: Dict[str, int] = {}

        # Small dicts used for display (the full info_dict is not kept)
        self.embed_info: Dict[str, Any] = {}
        self.info_dict: Dict[str, Any] = {}

    # --- Status ---

    @property
    def stats(self) -> dict:
        return self.livestream_downloader.stats

    @property
    def status(self) -> str:
        return self.stats.get("status") or "Unknown"

    def set_status(self, status: str) -> None:
        self.stats["status"] = status

    def set_error(self, error: BaseException) -> None:
        """Set STATUS_ERROR and remember a brief reason for it."""
        self.error_reason = describe_error(error)
        self.set_status(STATUS_ERROR)

    def start_recording(self, info_dict: Dict[str, Any]) -> None:
        """
        Set the "Recording" status and remember when recording began. For a live (or post-live)
        stream, yt-dlp's release_timestamp is the stream's actual start time.
        """
        if self.recording_started_at is None:
            self.recording_started_at = time.time()
        stream_start = info_dict.get('release_timestamp')
        if isinstance(stream_start, (int, float)) and 0 < stream_start <= time.time() + 60:
            self.stream_started_at = float(stream_start)
        self.set_status("Recording")

    def is_recording(self) -> bool:
        return str(self.status).lower() == "recording"

    def missing_segments(self) -> int:
        """
        Segments livestream_dl could not download, for the format missing the most. livestream_dl
        records the count when a format finishes (not after "Stop & merge").
        """
        return max((int((self.stats.get(kind) or {}).get("missing_segments") or 0) for kind in ("video", "audio")), default=0)

    def history_reason(self) -> Optional[str]:
        """Note saved in history: the error reason, or how many segments a finished download is missing."""
        if self.status == STATUS_ERROR:
            return self.error_reason
        if self.status != STATUS_FINISHED:
            # A cancelled or stopped download may have counts from a format that finished first
            return None
        missing = self.missing_segments()
        if missing:
            return "{0} segment{1} missing".format(missing, "" if missing == 1 else "s")
        return None

    def display_info(self) -> Dict[str, Any]:
        return {
            'fulltitle': self.info_dict.get('fulltitle'),
            'title': self.info_dict.get('title') or self.embed_info.get('title'),
        }

    def load_embed_info(self) -> None:
        """Fetch the title/thumbnail for display. Called from the job thread, never while holding a lock."""
        self.embed_info = common.get_embed_info(self.id, proxy=self.config.get_proxy())

    # --- Controls used by the web UI and channel scans ---

    def cancel(self) -> None:
        self.kill_this.set()

    def check_now(self) -> None:
        """Make a waiting job check the stream immediately."""
        self._force_check = True
        self.wake.set()

    def stop_and_merge(self) -> bool:
        """
        Stop downloading and merge what has been downloaded so far. Uses livestream_dl's graceful
        stop, which ends the segment loops and then muxes and moves the files as normal.
        Returns False if the job isn't recording (there's nothing to merge yet).
        """
        if not self.is_recording():
            return False
        self.logger.warning("Stop and merge requested")
        self.livestream_downloader.graceful_stop.set()
        return True

    @property
    def merge_requested(self) -> bool:
        return self.livestream_downloader.graceful_stop.is_set()

    def apply_discovery_hint(self, video: DiscoveredVideo) -> None:
        """
        Called when a channel scan finds this video again. If it is now live, the waiting job
        checks straight away; if the scheduled start moved, the wait is recalculated.
        """
        if video.live_status in common.LIVE_STATUSES and self.hinted_live_status not in common.LIVE_STATUSES:
            self.logger.info("Channel scan reports the stream is {0}, checking now".format(video.live_status))
            self.hinted_live_status = video.live_status
            self.check_now()
        elif video.release_timestamp and video.release_timestamp != self.scheduled_start:
            self.logger.info("Channel scan reports a new scheduled start time")
            self.scheduled_start = video.release_timestamp
            self.wake.set()
        if video.live_status:
            self.hinted_live_status = video.live_status

    # --- Waiting for an upcoming stream ---

    def beyond_look_ahead(self, now: Optional[float] = None) -> bool:
        """True if the stream hasn't started and its scheduled start is further away than `look_ahead` hours."""
        if not self.enforce_look_ahead or not self.scheduled_start or self.hinted_live_status in common.LIVE_STATUSES:
            return False
        now = time.time() if now is None else now
        return self.scheduled_start > now + self.config.get_look_ahead() * 3600.0

    def stop_if_beyond_look_ahead(self) -> bool:
        """Set the job's status and return True if it should stop waiting because of the look-ahead window."""
        if not self.beyond_look_ahead():
            return False
        self.logger.info("Scheduled start ({0}) is more than {1} hours away, no longer waiting".format(
            self.scheduled_start, self.config.get_look_ahead()))
        self.set_status(STATUS_BEYOND_LOOK_AHEAD)
        common.known_schedules.set(self.id, self.scheduled_start)
        return True

    def wait(self, seconds: float) -> WaitResult:
        return common.interruptible_wait(seconds, stop=self.kill_this, wake=self.wake)

    def wait_at_least(self, seconds: float) -> bool:
        """
        Wait `seconds` without being cut short by channel scan updates (which only set wake).
        "Check now" still ends the wait. Returns False if the job was cancelled.
        """
        deadline = time.monotonic() + seconds
        while True:
            result = self.wait(max(deadline - time.monotonic(), 0.0))
            if result is WaitResult.STOPPED:
                return False
            if result is WaitResult.ELAPSED or self._force_check or time.monotonic() >= deadline:
                self._force_check = False
                return True

    def wait_until_next_check(self) -> bool:
        """
        Wait until the next check of an upcoming stream is due. Returns False if cancelled, or if
        the scheduled start is beyond the look-ahead window (the status is then STATUS_BEYOND_LOOK_AHEAD).
        A wake-up from a schedule change only recalculates the wait; "check now" (or the stream
        being reported live) ends it.
        """
        self.set_status(STATUS_WAITING)
        while True:
            if self._force_check:
                break
            if self.stop_if_beyond_look_ahead():
                self.next_check_at = None
                return False
            delay = next_upcoming_check_delay(self.scheduled_start, time.time(), self.config.get_upcoming_recheck_max(),
                                              self.config.upcoming_video_max_wait())
            self.next_check_at = time.time() + delay
            self.logger.debug("Next check of upcoming stream in {0:.0f}s".format(delay))
            result = self.wait(delay)
            if result is WaitResult.STOPPED:
                return False
            if result is WaitResult.ELAPSED:
                break
        self._force_check = False
        self.next_check_at = None
        return True

    def probe_status(self) -> dict:
        """
        Full yt-dlp extraction WITHOUT format selection (process=False), used for streams that
        haven't started. Returns the raw info, which includes live_status and release_timestamp.

        livestream_dl's get_Video_Info can't be used for an upcoming stream: it has no formats,
        so yt-dlp warns "Requested format is not available" during format selection, and
        livestream_dl's ExtractionLogger turns any warning containing "not available" into an error.
        Errors are classified the same way as get_Video_Info (private, members only, ...).
        """
        options: dict[str, Any] = {
            'quiet': True,
            'skip_download': True,
            'cookiefile': self.config.get_cookies_file(),
            # Return the info of an upcoming stream instead of raising "This live event will begin in..."
            'ignore_no_formats_error': True,
            'logger': getUrls.ExtractionLogger(logger=self.logger, wait=False),
        }
        proxy = getUrls.proxy_for_ytdlp(self.config.get_proxy())
        if proxy:
            options['proxy'] = proxy
        # Deep copy so yt-dlp can't modify the config's nested extractor_args
        options.update(copy.deepcopy(self.config.get_ytdlp_options() or {}))

        with yt_dlp.YoutubeDL(options) as ydl:
            try:
                return ydl.extract_info(getUrls.normalize_video_url(self.id), download=False, process=False) or {}
            except yt_dlp.utils.DownloadError as e:
                # Same mapping to VideoInaccessibleError etc. as get_Video_Info (a module helper of livestream_dl)
                classified = getUrls._classify_download_error(e, self.id)
                if classified is e:
                    raise
                raise classified from e

    def report_needs_confirmation(self, error: BaseException) -> bool:
        """
        True (and logged) for the first of consecutive extractions reporting the video private, or
        an upcoming stream with an unexpected status (UnexpectedStatusError): the caller checks again
        after CONFIRM_DELAY instead of giving up or starting recovery. Later reports of the same
        kind return False until an extraction succeeds again.
        """
        if isinstance(error, UnexpectedStatusError):
            kind = "unexpected status"
        elif isinstance(error, getUrls.VideoInaccessibleError) and "private" in str(error).lower():
            kind = "private"
        else:
            return False
        self._unconfirmed_reports[kind] = self._unconfirmed_reports.get(kind, 0) + 1
        if self._unconfirmed_reports[kind] > 1:
            return False
        self.logger.warning("Extraction reported {0} ({1}), checking again in {2}s before believing it".format(
            kind, error, CONFIRM_DELAY))
        return True

    def extract_info(self, include_dash: bool = False, include_m3u8: bool = False) -> tuple[dict, str]:
        """
        One full yt-dlp extraction, without yt-dlp's own wait loop (which can't be interrupted).
        Returns (info_dict, live_status). Usable formats are only included once the stream is
        live; for an upcoming stream the info has live_status "is_upcoming" and no formats.
        """
        info_dict, live_status = self._extract_info(include_dash=include_dash, include_m3u8=include_m3u8)
        # The extraction worked, so a later "private" or unexpected status report needs confirming again
        self._unconfirmed_reports.clear()
        return info_dict, live_status

    def _extract_info(self, include_dash: bool, include_m3u8: bool) -> tuple[dict, str]:
        """
        While the stream is expected to be upcoming, probe_status() is used so each check costs
        one extraction. Once it reports the stream live, get_Video_Info extracts it again to get
        the processed formats livestream_dl needs.
        """
        if self.hinted_live_status == "is_upcoming":
            info_dict = self.probe_status()
            live_status = info_dict.get('live_status')
            if live_status == "was_live":
                # Ended since the last check. get_Video_Info decides whether it is processed by its
                # URLs: while live stream URLs are still listed it is downloaded like post_live.
                self.logger.info("Stream has ended, checking whether it can still be downloaded")
            elif live_status not in ("is_upcoming", *common.LIVE_STATUSES):
                # Same check as get_Video_Info: a processed video can't be downloaded as a stream.
                # A subclass, so callers confirm it once (a degraded extraction can lack the status).
                raise UnexpectedStatusError("Upcoming stream reported as {0}: video has been processed or the "
                                            "extraction was incomplete".format(live_status or "having no live status"))
            elif live_status == "is_upcoming" or not info_dict.get('formats'):
                self.hinted_live_status = live_status
                return info_dict, live_status
            else:
                self.logger.info("Stream is now {0}, extracting formats".format(live_status))

        try:
            info_dict, live_status = getUrls.get_Video_Info(
                id=self.id,
                wait=False,
                cookies=self.config.get_cookies_file(),
                proxy=self.config.get_proxy(),
                additional_options=self.config.get_ytdlp_options(),
                include_dash=include_dash,
                include_m3u8=include_m3u8,
                clean_info_dict=self.config.get_clean_info_json(),
                ignore_no_formats=True,
                logger=self.logger,
            )
        except yt_dlp.utils.DownloadError as e:
            if "requested format is not available" not in str(e).lower():
                raise
            # No formats: almost always a stream that hasn't started (e.g. a manually added
            # video, where the status wasn't known in advance). Get its status and start time.
            info_dict = self.probe_status()
            live_status = info_dict.get('live_status')
            if live_status in ENDED_LIVE_STATUSES:
                # Ended, and get_Video_Info found nothing to download: callers would otherwise
                # keep checking it as a live stream whose formats haven't appeared yet
                raise getUrls.VideoProcessedError("Video has been processed, please use yt-dlp directly")
        self.hinted_live_status = live_status
        return info_dict, live_status

    def wait_for_stream(self, include_dash: bool = False, include_m3u8: bool = False) -> Optional[dict]:
        """
        Return the info_dict once the stream is live (or post-live) and has formats, or None if
        the job was cancelled or the stream is beyond the look-ahead window (see wait_until_next_check).
        Exceptions from yt-dlp (private, removed, rate limited...) propagate.
        """
        # If the channel scan already says the stream is upcoming, nothing is extracted until it
        # is close to the scheduled start (or the scan reports it live, or "check now" is used).
        if self.hinted_live_status == "is_upcoming" and self.scheduled_start and self.scheduled_start - time.time() > MINIMUM_RECHECK_SECONDS:
            if not self.wait_until_next_check():
                return None

        while True:
            try:
                info_dict, live_status = self.extract_info(include_dash=include_dash, include_m3u8=include_m3u8)
            except (getUrls.VideoInaccessibleError, UnexpectedStatusError) as e:
                if not self.report_needs_confirmation(e):
                    raise
                self.set_status(STATUS_WAITING)
                if not self.wait_at_least(CONFIRM_DELAY):
                    return None
                continue
            if live_status in common.LIVE_STATUSES and info_dict.get('formats'):
                # Later channel scans reporting it live then don't trigger another check
                self.hinted_live_status = live_status
                return info_dict
            if live_status == "is_upcoming":
                self.scheduled_start = info_dict.get('release_timestamp') or self.scheduled_start
                self.logger.info("Stream is upcoming (scheduled start: {0})".format(self.scheduled_start))
                del info_dict
                if not self.wait_until_next_check():
                    return None
            else:
                # Live but no formats yet: YouTube sometimes needs a moment after a stream starts,
                # so check again soon rather than using the (possibly long) upcoming back-off.
                self.logger.info("Stream is {0} but no formats are available yet".format(live_status))
                del info_dict
                self.set_status(STATUS_WAITING)
                if self.wait(MINIMUM_RECHECK_SECONDS) is WaitResult.STOPPED:
                    return None

    def main(self) -> None:
        raise NotImplementedError
