"""
Shared state and helpers used by the web app, the channel scans and the downloaders: the
shutdown event (kill_all, from shutdown.py), logging, small utilities, YouTube oEmbed requests and
base yt-dlp options. Channel scanning itself is in scan.py.
"""
import copy
import enum
import html
import json
import logging
import logging.handlers
import os
import random
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import httpx

from getConfig import ConfigHandler
from livestream_dl import getUrls, headers
from livestream_dl.download_Live import create_http_client
# Set when the application is shutting down (see shutdown.py); used throughout as common.kill_all
from shutdown import kill_all  # noqa: F401

# --- Logging ---

# Every logger of the application is a child of this one ("holo.web", "holo.Downloader", ...).
# Only this logger has handlers, so there is a single console handler and a single log file
# handler: several handlers rotating the same file would rotate it independently and lose lines.
APP_LOGGER_NAME = "holo"
_logging_lock = threading.Lock()
# None: no handlers yet; "console": console only (no readable config yet); "config": set up from the config
_logging_state: Optional[str] = None


class _LogFormatter(logging.Formatter):
    """'[LEVEL] [video id] time - message', with the video id part only for job loggers (see initialize_logging)."""
    def format(self, record: logging.LogRecord) -> str:
        video_id = getattr(record, "video_id", None)
        record.video_tag = f" [{video_id}]" if video_id else ""
        return super().format(record)


def _log_level(name: str) -> int:
    level = logging.getLevelName(str(name).upper())   # Also knows livestream_dl's VERBOSE level
    return level if isinstance(level, int) else logging.INFO


def _file_handler(log_file: str, options: Dict[str, Any]) -> logging.Handler:
    if options.get("maxBytes"):
        return logging.handlers.RotatingFileHandler(log_file, maxBytes=options["maxBytes"],
                                                    backupCount=options.get("backupCount") or 5, encoding="utf-8")
    if options.get("when"):
        return logging.handlers.TimedRotatingFileHandler(log_file, when=options["when"],
                                                         backupCount=options.get("backupCount") or 7, encoding="utf-8")
    return logging.FileHandler(log_file, mode="a", encoding="utf-8")


def _configure_app_logger(config: Optional[ConfigHandler], force: bool) -> None:
    """
    Add the console (and log file) handlers to the application logger. They are set up once from
    the config; if the config can't be read yet, console-only handlers are set up once and replaced
    when a config is first available. force replaces them again (e.g. `web.py --config`).
    """
    global _logging_state
    app_logger = logging.getLogger(APP_LOGGER_NAME)
    with _logging_lock:
        if config is not None:
            # The level is applied every time, so a changed log_level applies to the next check or download
            app_logger.setLevel(_log_level(config.get_log_level()))
        if not force and (_logging_state == "config" or (_logging_state == "console" and config is None)):
            return
        if config is None:
            app_logger.setLevel(logging.INFO)
        # Handlers further up (e.g. gunicorn's root logger) must not print every line again
        app_logger.propagate = False
        formatter = _LogFormatter("[%(levelname)s]%(video_tag)s %(asctime)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

        handlers: list[logging.Handler] = [logging.StreamHandler()]
        log_file = config.get_log_file() if config is not None else None
        if log_file:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
                handlers.append(_file_handler(log_file, config.get_log_file_options()))
            except OSError:
                # Unable to create or open the file (e.g. read-only mount): log to the console only
                pass
        # The new handlers are added before the old ones are removed, so no line logged by another
        # thread meanwhile is lost (at worst one is printed twice)
        old_handlers = list(app_logger.handlers)
        for handler in handlers:
            handler.setFormatter(formatter)
            app_logger.addHandler(handler)
        for handler in old_handlers:
            app_logger.removeHandler(handler)
            handler.close()
        _logging_state = "config" if config is not None else "console"


def initialize_logging(config: Optional[ConfigHandler] = None, logger_name: Optional[str] = None,
                       force: bool = False, video_id: Optional[str] = None) -> Union[logging.Logger, logging.LoggerAdapter]:
    """
    Return the logger `holo.<logger_name>` (the application logger if no name is given). With a
    video_id, the logger is wrapped so each line shows the video id. The handlers are configured
    from the config file the first time; later calls only apply the configured log level.
    If the config can't be read (e.g. it doesn't exist yet) console-only INFO logging is used
    until it can.
    """
    if config is None:
        try:
            config = ConfigHandler()
        except Exception:
            config = None
    _configure_app_logger(config, force)
    logger = logging.getLogger(f"{APP_LOGGER_NAME}.{logger_name}" if logger_name else APP_LOGGER_NAME)
    if video_id:
        return logging.LoggerAdapter(logger, {"video_id": video_id})
    return logger


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


# --- HTTP / YouTube oEmbed ---

OEMBED_URL = "https://www.youtube.com/oembed"
OEMBED_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
# Info shown in the web UI and Discord messages rarely changes, so it is reused for a while
EMBED_INFO_CACHE_SECONDS = 600

# Shared httpx clients by proxy setting: (client, time.monotonic() it was last handed out)
_http_clients: dict[str, tuple[httpx.Client, float]] = {}
_http_clients_lock = threading.Lock()
_embed_info_cache = BoundedCache(max_entries=500, ttl=EMBED_INFO_CACHE_SECONDS)
# A client not handed out for this long is closed. Far longer than any request takes (see
# OEMBED_TIMEOUT), so a request is never still using it; while jobs started before a proxy change
# still use the old setting, both clients stay open.
IDLE_CLIENT_CLOSE_SECONDS = 1800


def get_http_client(proxy: Optional[Union[str, dict]] = None) -> httpx.Client:
    """
    A shared httpx client for the proxy setting, so connections are reused instead of a new client
    (and TLS handshake) for every request. httpx clients are safe to share between threads.
    Clients of other proxy settings that haven't been used for IDLE_CLIENT_CLOSE_SECONDS are
    closed, so old connection pools don't pile up after the proxy setting changes.
    """
    key = json.dumps(proxy, sort_keys=True, default=str)
    now = time.monotonic()
    idle: list[httpx.Client] = []
    with _http_clients_lock:
        entry = _http_clients.get(key)
        client = entry[0] if entry is not None and not entry[0].is_closed else None
        if client is None:
            client = create_http_client(proxies=proxy, max_connections=4, timeout=OEMBED_TIMEOUT)
        _http_clients[key] = (client, now)
        for other_key, (other, last_used) in list(_http_clients.items()):
            if other_key != key and now - last_used > IDLE_CLIENT_CLOSE_SECONDS:
                del _http_clients[other_key]
                idle.append(other)
    for other in idle:
        other.close()
    return client


def close_http_clients() -> None:
    """Close every shared client (used by the tests)."""
    with _http_clients_lock:
        clients = [client for client, _ in _http_clients.values()]
        _http_clients.clear()
    for client in clients:
        client.close()


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
    except (httpx.HTTPError, RuntimeError) as e:
        # RuntimeError: the shared client was closed (see get_http_client)
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


# --- yt-dlp ---

# yt-dlp live statuses of a stream that can be downloaded as a live stream
LIVE_STATUSES = ("is_live", "post_live")


def ytdlp_options(config: ConfigHandler, ytdlp_logger: Any, flat: bool = False,
                  defaults: Optional[Dict[str, Any]] = None) -> dict:
    """
    Base yt-dlp options for an extraction that downloads nothing: cookies, proxy and logger, plus
    the caller's `defaults`.
    flat=True:  cheap playlist listing (one request per page); the user's `ytdlp_options` are not
                applied as they are intended for video extraction.
    flat=False: full extraction of a single video, with the user's `ytdlp_options` (e.g. a PO token
                provider), which override everything else. ignore_no_formats_error lets an upcoming
                video return its info (live_status / release_timestamp) instead of raising
                "This live event will begin in...".
    """
    options: dict[str, Any] = {
        'quiet': True,
        'skip_download': True,
        'cookiefile': config.get_cookies_file(),
        'logger': ytdlp_logger,
        **(defaults or {}),
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
