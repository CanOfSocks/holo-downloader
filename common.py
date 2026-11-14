"""
Shared state and helpers used by the web app, the channel scans and the downloaders.
"""
import copy
import enum
import html
import json
import logging
import os
import random
import re
import signal
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from platform import system
from typing import Any, Container, Dict, Iterable, List, Optional, Union

import httpx
from yt_dlp import YoutubeDL

import lockup_schedule
from getConfig import ConfigHandler
from livestream_dl import getUrls, headers
from livestream_dl.download_Live import create_http_client, setup_logging
from livestream_dl.monitor_channel import VideoStatusCache
from livestream_dl.YoutubeURL import YTDLPLogger

# --- Shutdown handling ---

# Set when the application is shutting down. Every download and wait loop checks it.
kill_all = threading.Event()

original_sigint = signal.getsignal(signal.SIGINT)


def handle_shutdown(signum, frame) -> None:
    """Signal handler: tell every thread to stop, give them a moment, then run the original handler."""
    kill_all.set()
    time.sleep(5)
    if callable(original_sigint):
        original_sigint(signum, frame)


# Signal handlers can only be installed from the main thread. Under gunicorn this module is first
# imported by gunicorn.conf.py in the master process; gunicorn then installs its own handlers, and
# the worker uses gunicorn's hooks (see gunicorn.conf.py) to set kill_all.
if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGINT, handle_shutdown)
    if system() == "Windows":
        # SIGTERM won't fire on Windows, but SIGBREAK will on Ctrl-Break
        signal.signal(signal.SIGBREAK, handle_shutdown)
    else:
        signal.signal(signal.SIGTERM, handle_shutdown)


# --- Logging ---

def initialize_logging(config: Optional[ConfigHandler] = None, logger_name: Optional[str] = None,
                       force: bool = False, video_id: Optional[str] = None):
    """
    Return a logger configured from the config file. Loggers are cached by name by the logging
    module and handlers are only added the first time, so calling this repeatedly is cheap.
    If the config can't be read (e.g. it doesn't exist yet) console-only INFO logging is used.
    """
    if config is None:
        try:
            config = ConfigHandler()
        except Exception:
            return setup_logging(log_level="INFO", console=True, logger_name=logger_name, force=force, video_id=video_id)

    log_file = config.get_log_file()
    if log_file:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
        except OSError:
            # Unable to create the folder (e.g. read-only mount): log to the console only
            log_file = None

    try:
        return setup_logging(log_level=config.get_log_level(), console=True, file=log_file,
                             file_options=config.get_log_file_options(), logger_name=logger_name,
                             force=force, video_id=video_id)
    except OSError:
        return setup_logging(log_level=config.get_log_level(), console=True, logger_name=logger_name,
                             force=force, video_id=video_id)


logger = initialize_logging()


def setup_umask() -> None:
    try:
        if os.name != 'nt' and os.getenv("UMASK", None) is not None:
            umask = int(os.getenv("UMASK", "002"), 8)
            logger.debug("Setting UMASK to {0:o}".format(umask))
            os.umask(umask)
    except Exception as e:
        logger.exception("Unable to setup UMASK: {0}".format(e))


# --- Small utilities ---

@dataclass(frozen=True)
class DiscoveredVideo:
    """A video found by a channel scan. live_status / release_timestamp are hints from the scan and may be None."""
    id: str
    channel_id: Optional[str] = None
    live_status: Optional[str] = None
    release_timestamp: Optional[float] = None
    # Added by hand in the web UI: the look-ahead window isn't applied while it waits
    manual: bool = False


class BoundedCache:
    """
    Small thread-safe LRU cache with an optional time-to-live. Used for data fetched from YouTube
    that is requested repeatedly (descriptions, oEmbed info). Bounded so it can't grow forever
    in a long-running container.
    """
    def __init__(self, max_entries: int = 500, ttl: Optional[float] = None):
        self.max_entries: int = max_entries
        self.ttl: Optional[float] = ttl
        self._entries: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return default
            stored_at, value = entry
            if self.ttl is not None and time.monotonic() - stored_at > self.ttl:
                del self._entries[key]
                return default
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)


class WaitResult(enum.Enum):
    ELAPSED = "elapsed"   # The full time passed
    WOKEN = "woken"       # The wake event was set (e.g. "check now" or a channel scan update)
    STOPPED = "stopped"   # kill_all or the job's own stop event was set


def interruptible_wait(seconds: float, stop: threading.Event, wake: Optional[threading.Event] = None) -> WaitResult:
    """
    Sleep for up to `seconds`, returning early if the application is shutting down, the job is
    cancelled (`stop`), or `wake` is set. `wake` is cleared when it ends the wait.
    The events are polled at least once a second so a shutdown is never delayed noticeably.
    """
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if kill_all.is_set() or stop.is_set():
            return WaitResult.STOPPED
        if wake is not None and wake.is_set():
            wake.clear()
            return WaitResult.WOKEN
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return WaitResult.ELAPSED
        (wake or stop).wait(min(remaining, 1.0))


def random_sample(data: Union[List, tuple, Dict], k: Optional[int] = None) -> Union[List, tuple, Dict]:
    """Return the items of a list, tuple or dict in a random order (or a random subset of k items)."""
    if k is None:
        k = len(data)
    if isinstance(data, list):
        return random.sample(data, k)
    elif isinstance(data, tuple):
        return tuple(random.sample(data, k))
    elif isinstance(data, dict):
        return dict(random.sample(list(data.items()), k))
    raise TypeError("Unsupported data type. Only lists, tuples, and dictionaries are allowed.")


def withinFuture(config: Optional[ConfigHandler] = None, releaseTime: Optional[float] = None) -> bool:
    """True if releaseTime (epoch seconds) is within the configured look-behind/look-ahead window, or unknown."""
    if not releaseTime:
        return True
    config = config or ConfigHandler()
    now = datetime.now(timezone.utc)
    release = datetime.fromtimestamp(releaseTime, timezone.utc)
    return now - timedelta(hours=config.get_look_behind()) <= release <= now + timedelta(hours=config.get_look_ahead())


# --- HTTP / YouTube oEmbed ---

OEMBED_URL = "https://www.youtube.com/oembed"
OEMBED_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
# Info shown in the web UI and Discord messages rarely changes, so it is reused for a while
EMBED_INFO_CACHE_SECONDS = 600

_http_clients: dict[str, httpx.Client] = {}
_http_clients_lock = threading.Lock()
_embed_info_cache = BoundedCache(max_entries=500, ttl=EMBED_INFO_CACHE_SECONDS)


def get_http_client(proxy: Optional[Union[str, dict]] = None) -> httpx.Client:
    """
    One shared httpx client per proxy setting, so connections are reused instead of a new client
    (and TLS handshake) for every request. httpx clients are safe to share between threads.
    """
    key = json.dumps(proxy, sort_keys=True, default=str)
    with _http_clients_lock:
        client = _http_clients.get(key)
        if client is None or client.is_closed:
            client = create_http_client(proxies=proxy, max_connections=4, timeout=OEMBED_TIMEOUT)
            _http_clients[key] = client
        return client


@dataclass
class OembedResult:
    status_code: Optional[int]      # None if the request failed (timeout, network error)
    data: Optional[Dict[str, Any]]  # Parsed JSON for a 200 response
    error: Optional[str] = None


def fetch_oembed(video_id: str, proxy: Optional[Union[str, dict]] = None) -> OembedResult:
    """
    Request YouTube's oEmbed info for a video. This is a single small request (about 1 KB) that
    doesn't use yt-dlp or the player API, so it is used as a cheap availability check.
    Typical responses: 200 public/unlisted, 401 private or embedding disabled, 404 removed/missing.
    """
    try:
        response = get_http_client(proxy).get(
            OEMBED_URL,
            params={"format": "json", "url": f"https://www.youtube.com/watch?v={video_id}"},
            headers=headers.get_httpx_header(),
        )
    except httpx.HTTPError as e:
        return OembedResult(status_code=None, data=None, error=f"{type(e).__name__}: {e}")

    if response.status_code != 200:
        return OembedResult(status_code=response.status_code, data=None, error=f"HTTP {response.status_code}")

    try:
        data = response.json()
    except ValueError:
        data = {}
    if data.get("title") is None:
        # Before a stream starts the oEmbed "title" can be missing while the embed HTML still
        # contains it as an escaped attribute (title="..."), so take it from there.
        title_match = re.search(r'title=\\u0022(.*?)\\u0022', response.text)
        if title_match:
            data["title"] = html.unescape(json.loads(f'"{title_match.group(1)}"'))
    # The embed HTML is large and not used
    data.pop("html", None)
    return OembedResult(status_code=200, data=data)


def get_embed_info(video_id: str, proxy: Optional[Union[str, dict]] = None) -> Dict[str, Any]:
    """Cached oEmbed info (title, author, thumbnail) for display. Returns {} if unavailable."""
    cached = _embed_info_cache.get(video_id)
    if cached is not None:
        return cached
    result = fetch_oembed(video_id, proxy=proxy)
    info: Dict[str, Any] = dict(result.data or {})
    if info and not info.get("thumbnail_url"):
        info["thumbnail_url"] = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    # Failed requests are not cached, so the next caller tries again
    if result.status_code == 200:
        _embed_info_cache.set(video_id, info)
    return info


# --- yt-dlp channel scanning ---

# Results of full per-video extractions done during channel scans. Videos that have finished
# (was_live / not_live) are never extracted again; see livestream_dl.monitor_channel.
status_cache = VideoStatusCache()
# Read scheduled start times from flat playlist listings (see lockup_schedule)
lockup_schedule.install()
# Scheduled starts beyond the look-ahead window, seen by downloads that then stopped waiting.
# Used when a listing shows a video as upcoming without its start time, so the video isn't started
# (and stopped again) on every scan. Expires so a stream moved earlier is noticed eventually.
KNOWN_SCHEDULE_SECONDS = 6 * 3600
known_schedules = BoundedCache(max_entries=500, ttl=KNOWN_SCHEDULE_SECONDS)
# Descriptions don't change often and are only needed for description filters
_description_cache = BoundedCache(max_entries=1000, ttl=6 * 3600)

LIVE_STATUSES = ("is_live", "post_live")


def ytdlp_options(config: ConfigHandler, logger: logging.Logger, flat: bool) -> dict:
    """
    yt-dlp options for channel scans.
    flat=True:  cheap playlist listing (one request per page); the user's `ytdlp_options` are not
                applied as they are intended for video extraction.
    flat=False: full extraction of a single video, with the user's `ytdlp_options` (e.g. a PO token
                provider). ignore_no_formats_error lets an upcoming video return its info
                (live_status / release_timestamp) instead of raising "This live event will begin in...".
    """
    options: dict[str, Any] = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'sleep_interval_requests': 1,
        'cookiefile': config.get_cookies_file(),
        'logger': YTDLPLogger(logger=logger),
    }
    proxy = getUrls.proxy_for_ytdlp(config.get_proxy())
    if proxy:
        options['proxy'] = proxy
    if flat:
        options['extract_flat'] = True
    else:
        options['ignore_no_formats_error'] = True
        # Deep copy so the nested extractor_args of the config are never modified by yt-dlp
        options.update(copy.deepcopy(config.get_ytdlp_options() or {}))
    return options


def playlist_url(channel_id: str, tab: Optional[str]) -> tuple[str, bool]:
    """
    URL to scan for a channel, and whether it is a playlist. Channel (UC) IDs are converted to the
    uploads (UU) or members-only (UUMO) playlist, which is cheaper to list than a channel tab.
    """
    if tab == "membership":
        if channel_id.startswith("UUMO"):
            return f"https://www.youtube.com/playlist?list={channel_id}", True
        if channel_id.startswith("UC") or channel_id.startswith("UU"):
            return f"https://www.youtube.com/playlist?list=UUMO{channel_id[2:]}", True
    elif tab == "streams":
        # Note: a members playlist ID (UUMO...) also starts with "UU", so it is used as-is
        if channel_id.startswith("UU"):
            return f"https://www.youtube.com/playlist?list={channel_id}", True
        if channel_id.startswith("UC"):
            return f"https://www.youtube.com/playlist?list=UU{channel_id[2:]}", True
    return f"https://www.youtube.com/channel/{channel_id}/{tab}", False


def extract_video(video_id_or_url: str, config: ConfigHandler, logger: logging.Logger) -> Optional[dict]:
    """
    Full yt-dlp extraction of one video for channel scans. Returns None on error.
    process=False skips format selection: only the status, start time and description are
    needed, and format selection fails ("Requested format is not available") for an upcoming video.
    """
    try:
        with YoutubeDL(ytdlp_options(config, logger, flat=False)) as ydl:
            info = ydl.extract_info(video_id_or_url, download=False, process=False)
    except Exception as e:
        logger.error("Unable to extract information for video {0}: {1}".format(video_id_or_url, e))
        return None
    if info and info.get("id") and info.get("description") is not None:
        _description_cache.set(info["id"], info.get("description"))
    return info


def titleFilter(live: Dict[str, Any], channel_id: str, config: ConfigHandler) -> Optional[bool]:
    """True/False if the channel has a title filter and it matches/doesn't. None if there's no (valid) filter."""
    title_filter = config.get_title_filter().get(channel_id, None)
    if title_filter is None:
        return None
    try:
        return re.search(title_filter, live.get('title') or "") is not None
    except Exception:
        logger.exception("Title filter failed")
        return None


def descriptionFilter(live: Dict[str, Any], channel_id: str, config: ConfigHandler,
                      this_logger: Optional[logging.Logger] = None) -> Optional[bool]:
    """Like titleFilter but for the description, which may need a full extraction (results are cached)."""
    desc_filter = config.get_desc_filter().get(channel_id, None)
    if desc_filter is None:
        return None

    description = live.get('description', None)
    if description is None and live.get('id'):
        description = _description_cache.get(live.get('id'))
    if description is None and live.get('id'):
        info = extract_video("https://www.youtube.com/watch?v={0}".format(live.get('id')), config, this_logger or logger)
        description = (info or {}).get('description')
    try:
        return re.search(desc_filter, description or "") is not None
    except Exception:
        logger.exception("Description filter failed")
        return None


def filtering(live: Dict[str, Any], channel_id: str, config: ConfigHandler,
              this_logger: Optional[logging.Logger] = None) -> bool:
    """
    Title and description filters are an inclusive OR: the video passes if either filter matches,
    or if the channel has no filters. The title is checked first so a matching title avoids
    fetching the description.
    """
    title = titleFilter(live, channel_id, config)
    if title:
        return True
    description = descriptionFilter(live, channel_id, config, this_logger)
    if description:
        return True
    return title is None and description is None


def is_wanted_status(video: Dict[str, Any], config: ConfigHandler) -> bool:
    """Live or post-live, or upcoming within the look-ahead/look-behind window."""
    live_status = video.get('live_status')
    return live_status in LIVE_STATUSES or (live_status == 'is_upcoming' and withinFuture(config, video.get('release_timestamp')))


def get_upcoming_or_live_videos(channel_id: str, config: ConfigHandler, tab: str,
                                passed_logger: Optional[logging.Logger] = None,
                                active_ids: Optional[Container[str]] = None) -> List[DiscoveredVideo]:
    """
    Scan the start of a channel's uploads (or members) playlist for live, post-live and upcoming
    videos that pass the channel's filters.

    Videos in `active_ids` are already downloading: they are not extracted or filtered again, but
    are still returned when the listing shows their status, so the running download can be told
    the stream went live or its schedule changed.
    """
    this_logger = passed_logger or logger
    active_ids = active_ids or ()
    url, is_playlist = playlist_url(channel_id, tab)
    flat_options = ytdlp_options(config, this_logger, flat=True)
    # Channel tabs are slower to list than playlists, so fewer entries are checked
    playlist_limit = (config.get_ytlp_playlist_limit() or 10) if is_playlist else 10
    flat_options['playlist_items'] = '1:{0}'.format(playlist_limit)

    try:
        with YoutubeDL(flat_options) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception:
        this_logger.exception("An unexpected error occurred when trying to fetch videos")
        raise

    found: "OrderedDict[str, DiscoveredVideo]" = OrderedDict()
    for video in info.get('entries') or []:
        if not video or not video.get('id'):
            # Unavailable playlist entries can be returned as None
            continue
        video_id: str = video['id']
        entry_channel = video.get('channel_id') or channel_id

        if video_id in active_ids:
            if video.get('live_status'):
                found[video_id] = DiscoveredVideo(video_id, entry_channel, video.get('live_status'), video.get('release_timestamp'))
            continue

        # Fallback if the listing doesn't include the live status (and it isn't a normal video with a duration)
        if video.get('live_status') is None and video.get('duration') is None:
            if status_cache.should_skip(video_id):
                this_logger.debug("({0}) Skipping re-extraction, status cached".format(video_id))
                continue
            extracted = extract_video(video.get('url') or video_id, config, this_logger)
            if extracted is None:
                continue
            video = extracted
            status_cache.record(video_id, video.get('live_status'), video.get('release_timestamp'), config.get_look_ahead())

        if video.get('live_status') == 'is_upcoming' and not video.get('release_timestamp'):
            video['release_timestamp'] = known_schedules.get(video_id)
        this_logger.debug("({1}) live_status = {0}, scheduled start = {2}".format(
            video.get('live_status'), video_id, video.get('release_timestamp')))
        # Cheap status/time check before the filters, which may need a full extraction for the description
        if is_wanted_status(video, config) and filtering(video, entry_channel, config, this_logger):
            found[video_id] = DiscoveredVideo(video_id, entry_channel, video.get('live_status'), video.get('release_timestamp'))

    return list(found.values())


# Matches "<video id>.info.json" files saved by the unarchived monitor
_UNARCHIVED_JSON_PATTERN = re.compile(r"^([0-9A-Za-z_-]{10}[048AEIMQUYcgkosw])\.info\.json$")


def saved_unarchived_ids(config: ConfigHandler, this_logger: Optional[logging.Logger] = None) -> List[str]:
    """IDs that have an info.json saved in the unarchived temp folder (e.g. monitors running before a restart)."""
    this_logger = this_logger or logger
    directory = config.get_unarchived_temp_folder()
    os.makedirs(directory, exist_ok=True)
    ids = []
    for entry in os.scandir(directory):
        match = _UNARCHIVED_JSON_PATTERN.match(entry.name)
        if match and entry.is_file():
            this_logger.debug("[Unarchived] Found ID {0} from filename {1}".format(match.group(1), entry.name))
            ids.append(match.group(1))
    return ids


def put_all(output, videos: Iterable[DiscoveredVideo]) -> None:
    """Put videos on a queue (or anything with a put() method)."""
    for video in videos:
        output.put(video)
