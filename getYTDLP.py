"""Channel scan using yt-dlp: lists each configured channel's uploads playlist."""
import logging
from typing import Container, Optional

import common
import discord_web
from common import DiscoveredVideo, kill_all
from getConfig import ConfigHandler


def main(output, unarchived: bool = False, config: Optional[ConfigHandler] = None,
         logger: Optional[logging.Logger] = None, active_ids: Container[str] = ()) -> None:
    """
    Scan the configured channels and put each matching DiscoveredVideo on `output` (a queue)
    as soon as its channel has been scanned, so downloads can start while the scan continues.
    """
    config = config or ConfigHandler()
    logger = logger or common.initialize_logging(config, logger_name="getYTDLP")

    channels: dict[str, str] = config.unarchived_channel_ids_to_match if unarchived else config.channel_ids_to_match
    if config.randomise_lists():
        channels = common.random_sample(channels)

    found_ids: set[str] = set()
    for channel_name, channel_id in channels.items():
        if kill_all.is_set():
            return
        try:
            logger.debug("Looking for: {0}".format(channel_name))
            videos = common.get_upcoming_or_live_videos(channel_id, config, tab="streams", passed_logger=logger, active_ids=active_ids)
        except Exception as e:
            discord_web.notify(channel_id, "playlist-error", message=f"Error getting streams:\n{type(e).__name__}: {e}"[-500:], config=config)
            logger.exception("Error fetching streams for {0}. Check cookies.".format(channel_name))
            continue
        found_ids.update(video.id for video in videos)
        common.put_all(output, videos)

    if unarchived:
        # Resume monitors for streams that already have a saved info.json (e.g. after a restart).
        # IDs found by the scan above were already queued, so they are not queued twice.
        saved = [DiscoveredVideo(video_id) for video_id in common.saved_unarchived_ids(config, logger) if video_id not in found_ids]
        common.put_all(output, saved)
