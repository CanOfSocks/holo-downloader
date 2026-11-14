"""Channel scan of members-only playlists (requires cookies of an account with the memberships)."""
import logging
from random import uniform
from typing import Container, Optional

import common
import discord_web
from common import kill_all
from getConfig import ConfigHandler


def main(output, config: Optional[ConfigHandler] = None, logger: Optional[logging.Logger] = None,
         active_ids: Container[str] = ()) -> None:
    """Scan each configured members-only playlist and put matching DiscoveredVideos on `output`."""
    config = config or ConfigHandler()
    logger = logger or common.initialize_logging(config, "getMembers")

    for channel_name, channel_id in config.members_only.items():
        # Space out the requests made with the account's cookies. kill_all.wait() sleeps but
        # returns True straight away if the application is shutting down.
        if kill_all.wait(uniform(5.0, 10.0)):
            return
        try:
            logger.debug(f"Looking for membership streams for: {channel_name} ({channel_id})")
            videos = common.get_upcoming_or_live_videos(channel_id, config, tab="membership", passed_logger=logger, active_ids=active_ids)
        except Exception as e:
            logger.exception(f"Error fetching membership streams for {channel_name}. Check cookies.")
            discord_web.notify(channel_id, "membership-error", message=str(e)[-500:], config=config)
            continue
        common.put_all(output, videos)
