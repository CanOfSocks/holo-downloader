"""
Channel scanning with yt-dlp: listing a channel's uploads (or members-only) playlist, deciding
which videos are wanted (live status, look-ahead/look-behind window, title/description filters)
and finding unarchived monitors to resume after a restart.
"""
import logging
import os
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Container, Dict, Iterable, List, Optional

from yt_dlp import YoutubeDL

import common
import lockup_schedule
from common import LIVE_STATUSES, BoundedCache, DiscoveredVideo
from getConfig import ConfigHandler
from livestream_dl.monitor_channel import VideoStatusCache
from livestream_dl.YoutubeURL import YTDLPLogger

logger = common.initialize_logging(logger_name="scan")

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

# A channel ID (UC...), its uploads playlist (UU...) or one of its other channel playlists
# (UUMO members-only, UULF videos, UUSH shorts, ...). The 22 characters after the prefix are the same.
_CHANNEL_ID_PATTERN = re.compile(r"^(?:UC|UU)([0-9A-Za-z_-]{22})$|^UU[A-Z]{2}([0-9A-Za-z_-]{22})$")
# Channel IDs looked up from handles/URLs in [members_only] (they don't change)
_resolved_channel_ids = BoundedCache(max_entries=500, ttl=None)

# Matches "<video id>.info.json" files saved by the unarchived monitor
_UNARCHIVED_JSON_PATTERN = re.compile(r"^([0-9A-Za-z_-]{10}[048AEIMQUYcgkosw])\.info\.json$")


def scan_ytdlp_options(config: ConfigHandler, this_logger: logging.Logger, flat: bool) -> dict:
    """
    yt-dlp options for channel scans (see common.ytdlp_options). Warnings are hidden, and requests
    are spaced out by a second as a scan makes many of them.
    flat=True:  cheap playlist listing (one request per page); the user's `ytdlp_options` are not
                applied as they are intended for video extraction.
    flat=False: full extraction of a single video, with the user's `ytdlp_options`.
    """
    options = common.ytdlp_options(config, YTDLPLogger(logger=this_logger), flat=flat)
    options.update({'no_warnings': True, 'sleep_interval_requests': 1})
    return options


def within_time_window(config: ConfigHandler, release_time: Optional[float]) -> bool:
    """True if release_time (epoch seconds) is within the configured look-behind/look-ahead window, or unknown."""
    if not release_time:
        return True
    now = datetime.now(timezone.utc)
    release = datetime.fromtimestamp(release_time, timezone.utc)
    return now - timedelta(hours=config.get_look_behind()) <= release <= now + timedelta(hours=config.get_look_ahead())


def channel_id_suffix(value: str) -> Optional[str]:
    """The 22 character part shared by a channel's ID and playlists, or None if `value` isn't one of them."""
    match = _CHANNEL_ID_PATTERN.match(value or "")
    return (match.group(1) or match.group(2)) if match else None


def members_playlist_id(value: str, config: ConfigHandler, this_logger: Optional[logging.Logger] = None) -> str:
    """
    The members-only (UUMO) playlist of a [members_only] entry. Channel and playlist IDs are
    converted directly; anything else (a handle such as "@name" or a channel URL) is looked up
    once with yt-dlp. Raises ValueError if the channel can't be found.
    """
    suffix = channel_id_suffix(value)
    if suffix is None:
        suffix = _resolved_channel_ids.get(value)
    if suffix is None:
        if value.startswith("http"):
            url = value
        elif value.startswith("@"):
            url = f"https://www.youtube.com/{value}"
        else:
            url = f"https://www.youtube.com/channel/{value}"
        options = scan_ytdlp_options(config, this_logger or logger, flat=True)
        options['playlist_items'] = '1:1'
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        suffix = channel_id_suffix(info.get('channel_id') or "")
        if suffix is None:
            raise ValueError(f"Unable to find the channel ID of {value!r}")
        _resolved_channel_ids.set(value, suffix)
    return "UUMO" + suffix


def playlist_url(channel_id: str, tab: Optional[str]) -> tuple[str, bool]:
    """
    URL to scan for a channel, and whether it is a playlist. Channel (UC) IDs are converted to the
    uploads (UU) or members-only (UUMO) playlist, which is cheaper to list than a channel tab.
    The membership scan resolves other values to an ID first (see members_playlist_id).
    """
    if tab == "membership":
        suffix = channel_id_suffix(channel_id)
        if suffix is not None:
            return f"https://www.youtube.com/playlist?list=UUMO{suffix}", True
    elif tab == "streams":
        # Note: a members playlist ID (UUMO...) also starts with "UU", so it is used as-is
        if channel_id.startswith("UU"):
            return f"https://www.youtube.com/playlist?list={channel_id}", True
        if channel_id.startswith("UC"):
            return f"https://www.youtube.com/playlist?list=UU{channel_id[2:]}", True
    return f"https://www.youtube.com/channel/{channel_id}/{tab}", False


def extract_video(video_id_or_url: str, config: ConfigHandler, this_logger: logging.Logger) -> Optional[dict]:
    """
    Full yt-dlp extraction of one video for channel scans. Returns None on error.
    process=False skips format selection: only the status, start time and description are
    needed, and format selection fails ("Requested format is not available") for an upcoming video.
    """
    try:
        with YoutubeDL(scan_ytdlp_options(config, this_logger, flat=False)) as ydl:
            info = ydl.extract_info(video_id_or_url, download=False, process=False)
    except Exception as e:
        this_logger.error("Unable to extract information for video {0}: {1}".format(video_id_or_url, e))
        return None
    if info and info.get("id") and info.get("description") is not None:
        _description_cache.set(info["id"], info.get("description"))
    return info


# --- Filters ---

def title_filter_matches(live: Dict[str, Any], channel_id: str, config: ConfigHandler) -> Optional[bool]:
    """True/False if the channel has a title filter and it matches/doesn't. None if there's no (valid) filter."""
    title_filter = config.get_title_filter().get(channel_id, None)
    if title_filter is None:
        return None
    try:
        return re.search(title_filter, live.get('title') or "") is not None
    except Exception:
        logger.exception("Title filter failed")
        return None


def description_filter_matches(live: Dict[str, Any], channel_id: str, config: ConfigHandler,
                               this_logger: Optional[logging.Logger] = None) -> Optional[bool]:
    """Like title_filter_matches but for the description, which may need a full extraction (results are cached)."""
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


def passes_filters(live: Dict[str, Any], channel_id: str, config: ConfigHandler,
                   this_logger: Optional[logging.Logger] = None) -> bool:
    """
    Title and description filters are an inclusive OR: the video passes if either filter matches,
    or if the channel has no filters. The title is checked first so a matching title avoids
    fetching the description.
    """
    title = title_filter_matches(live, channel_id, config)
    if title:
        return True
    description = description_filter_matches(live, channel_id, config, this_logger)
    if description:
        return True
    return title is None and description is None


def is_wanted_status(video: Dict[str, Any], config: ConfigHandler) -> bool:
    """Live or post-live, or upcoming within the look-ahead/look-behind window."""
    live_status = video.get('live_status')
    return live_status in LIVE_STATUSES or (live_status == 'is_upcoming' and within_time_window(config, video.get('release_timestamp')))


# --- Scanning ---

def get_upcoming_or_live_videos(channel_id: str, config: ConfigHandler, tab: str,
                                passed_logger: Optional[logging.Logger] = None,
                                active_ids: Optional[Container[str]] = None,
                                apply_filters: bool = True) -> List[DiscoveredVideo]:
    """
    Scan the start of a channel's uploads (or members) playlist for live, post-live and upcoming
    videos that pass the channel's filters (all of them if apply_filters is False).

    Videos in `active_ids` are already downloading: they are not extracted or filtered again, but
    are still returned when the listing shows their status, so the running download can be told
    the stream went live or its schedule changed.
    """
    this_logger = passed_logger or logger
    active_ids = active_ids or ()
    if tab == "membership":
        # Always the channel's members-only playlist, whatever form the [members_only] entry has
        playlist_id = members_playlist_id(channel_id, config, this_logger)
        channel_id = "UC" + playlist_id[4:]
        url, is_playlist = playlist_url(playlist_id, tab)
    else:
        url, is_playlist = playlist_url(channel_id, tab)
    flat_options = scan_ytdlp_options(config, this_logger, flat=True)
    # Channel tabs are slower to list than playlists, so fewer entries are checked
    playlist_limit = (config.get_playlist_limit() or 10) if is_playlist else 10
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
        if is_wanted_status(video, config) and (not apply_filters or passes_filters(video, entry_channel, config, this_logger)):
            found[video_id] = DiscoveredVideo(video_id, entry_channel, video.get('live_status'), video.get('release_timestamp'))

    return list(found.values())


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
