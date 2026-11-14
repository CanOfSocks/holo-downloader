"""Chooses the channel scan method (`video_fetch_method` in the config)."""
import logging
from typing import Container, Optional

import common
from getConfig import ConfigHandler


def main(output, unarchived: bool = False, config: Optional[ConfigHandler] = None,
         logger: Optional[logging.Logger] = None, active_ids: Container[str] = ()) -> None:
    """Run the configured scan, putting each DiscoveredVideo found on `output` (a queue)."""
    config = config or ConfigHandler()
    logger = logger or common.initialize_logging(config, "getVids")

    method = config.get_fetch_method()
    if method == "ytdlp":
        import getYTDLP
        getYTDLP.main(output, unarchived=unarchived, config=config, logger=logger, active_ids=active_ids)
    elif method == "json":
        import getJson
        getJson.main(output, unarchived=unarchived, config=config, logger=logger, active_ids=active_ids)
    else:
        logger.error("Invalid fetch method: {0}".format(method))
