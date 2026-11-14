"""Channel scan using the holo.dev API (Hololive channels only). No yt-dlp requests are made for the listing."""
import logging
from datetime import datetime, timezone
from typing import Any, Container, Dict, Optional

import httpx

import common
from common import DiscoveredVideo
from getConfig import ConfigHandler

url = "https://holo.dev/api/v1/lives/open"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def parse_utc(value: str) -> datetime:
    # The API returns UTC times ("Z" suffix). strptime gives a naive datetime, which .timestamp()
    # would treat as local time, so the UTC timezone is attached explicitly.
    return datetime.strptime(value, DATE_FORMAT).replace(tzinfo=timezone.utc)


class json_object:
    """Wraps a holo.dev API entry so it can be used with the shared filter functions (which call .get())."""
    def __init__(self, live: Dict[str, Any]):
        self.holodev_id = live.get('id')
        self.title: Optional[str] = live.get('title')
        self.start_at: datetime = parse_utc(live.get('start_at'))
        self.created_at: datetime = parse_utc(live.get('created_at'))
        self.thumbnail_url: Optional[str] = live.get('cover')
        self.id: str = live.get('room')                  # YouTube video ID
        self.platform: Optional[str] = live.get('platform')
        self.channel_id: Optional[str] = live.get('channel')  # YouTube channel ID (UC...)
        self.duration = live.get('duration') if live.get('duration') is not None else -1
        self.description: Optional[str] = None

    def get(self, property: str, default: Any = None) -> Any:
        return getattr(self, property, default)


def getStreams(unarchived: bool, config: ConfigHandler, logger: logging.Logger) -> list[DiscoveredVideo]:
    try:
        response = common.get_http_client(config.get_proxy()).get(url)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        logger.exception("Error retrieving streams from holo.dev")
        return []
    logger.debug("Received %d lives from holo.dev", len(data.get('lives', [])))

    channel_ids = set((config.unarchived_channel_ids_to_match if unarchived else config.channel_ids_to_match).values())
    matching: list[DiscoveredVideo] = []
    for entry in data.get('lives', []):
        try:
            live = json_object(entry)
        except (TypeError, ValueError):
            logger.warning("Skipping holo.dev entry with unexpected data: {0}".format(entry))
            continue
        if live.platform != "youtube" or live.channel_id not in channel_ids:
            continue
        if not common.withinFuture(config, live.start_at.timestamp()):
            continue
        # Filters only apply to normal downloads, not to the unarchived monitor
        if not unarchived and not common.filtering(live, live.channel_id, config, logger):
            continue
        # The API doesn't say whether the stream is live yet, so only the start time is passed on
        matching.append(DiscoveredVideo(live.id, live.channel_id, None, live.start_at.timestamp()))
    return matching


def main(output, unarchived: bool = False, config: Optional[ConfigHandler] = None,
         logger: Optional[logging.Logger] = None, active_ids: Container[str] = ()) -> None:
    config = config or ConfigHandler()
    logger = logger or common.initialize_logging(config, "GetJson")

    videos = getStreams(unarchived, config, logger)
    common.put_all(output, videos)
    if unarchived:
        found = {video.id for video in videos}
        common.put_all(output, [DiscoveredVideo(video_id) for video_id in common.saved_unarchived_ids(config, logger) if video_id not in found])
