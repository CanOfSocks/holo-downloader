#!/usr/local/bin/python
import argparse
import common
import time
import discord_web
from getConfig import ConfigHandler
import logging
from typing import Optional, Dict, List, Any
from common import initialize_logging, kill_all
import queue

def getVideos(channel_ids_to_match: Dict[str, str], command: Optional[str] = None, unarchived: bool = False, frequency: Optional[str] = None, config: ConfigHandler = None, logger: logging.Logger = None, queue: queue.Queue = None, return_dict: bool = False) -> list[str] | str:
    """
    Fetches streams for the given channels and executes a command on them.
    
    :param channel_ids_to_match: Dictionary of channel names to channel IDs.
    :param command: Command to execute ('spawn', 'bash', 'print').
    :param unarchived: Flag to include unarchived videos.
    :param frequency: Optional CRON frequency string.
    :param config: The ConfigHandler object, defaults to a new instance if None.
    :param logger: The logger instance to use, defaults to a new instance if None.
    """
    # 1. Ensure config and logger objects are available
    if config is None:
        config = ConfigHandler()
    if logger is None:
        logger = initialize_logging(config, logger_name="getVideos")

    all_lives: List[str] = []
    
    for channel in channel_ids_to_match:
        if kill_all.is_set():
            break
        try:
            logger.debug("Looking for: {0}".format(channel))
            # Assuming common.get_upcoming_or_live_videos is updated to accept config
            lives = common.get_upcoming_or_live_videos(channel_ids_to_match[channel], config, tab="streams")
            if queue is not None:
                for live in lives:
                    if return_dict:
                        queue.put({"id": live, "channel_id": channel_ids_to_match[channel]})
                    else:
                        queue.put(live)
            elif return_dict:
                lives = [{"channel_id": channel_ids_to_match[channel], "id": id} for id in lives]

            all_lives.extend(lives)
        except Exception as e:
            discord_web.main(channel_ids_to_match[channel], "playlist-error", message=f"Error getting streams:\n{type(e).__name__}: {str(e)}"[-500:], config=config)
            logger.exception("Error fetching streams for {0}. Check cookies.".format(channel))
            
    if unarchived:
        # Assuming common.combine_unarchived is updated to accept config
        all_lives = common.combine_unarchived(all_lives, config)
        if queue is not None:
            for live in all_lives:
                queue.put(live)

    # Pass the config object and other arguments to the executor
    return common.vid_executor(streams=all_lives, command=command, config=config, frequency=frequency, unarchived=unarchived)
        
def main(command: Optional[str] = None, unarchived: bool = False, frequency: Optional[str] = None, config: ConfigHandler = None, logger: logging.Logger = None, queue: queue.Queue = None, return_dict: bool = False) -> list[str] | str:
    """
    Main execution logic to determine which channel list to use and start fetching videos.
    
    :param command: The command to execute on the fetched video IDs.
    :param unarchived: Flag to indicate if unarchived videos should be included.
    :param frequency: The CRON frequency string.
    :param config: The ConfigHandler object, defaults to a new instance if None.
    :param logger: The logger instance to use, defaults to a new instance if None.
    """
    # 1. Ensure config and logger objects are available
    if config is None:
        config = ConfigHandler()
    if logger is None:
        logger = initialize_logging(config, logger_name="main_ytdlp")
        
    if unarchived:
        channel_ids_to_match = config.unarchived_channel_ids_to_match
    else:
        channel_ids_to_match = config.channel_ids_to_match
        
    if channel_ids_to_match:
        if config.randomise_lists() is True:
            channel_ids_to_match = common.random_sample(channel_ids_to_match)
            
        # Pass the config and logger objects down
        return getVideos(channel_ids_to_match, command, unarchived, frequency=frequency, config=config, logger=logger, queue=queue, return_dict=return_dict)

    
if __name__ == "__main__":
    try:
        # 1. Instantiate ConfigHandler once
        app_config = ConfigHandler()

        # 2. Initialize the logger for the main execution block
        main_logger = initialize_logging(app_config, logger_name="getYTDLP")
    
    
        # Create the parser
        parser = argparse.ArgumentParser(description="Process command and an optional unarchived flag.")

        # Add arguments
        parser.add_argument('--command', type=str, choices=['spawn', 'bash', 'print'], default=None, help='The command (optional, default: None)')
        parser.add_argument('--unarchived', action='store_true', help='Flag to indicate unarchived (default: False)')
        parser.add_argument('--frequency', type=str, default=None, help='The cron schedule (optional, default: None)')

        # Parse the arguments
        args = parser.parse_args()
        
        # Call main, passing the config object and the logger
        main(command=args.command, unarchived=args.unarchived, frequency=args.frequency, config=app_config, logger=main_logger)
        
    except Exception as e:
        # Use the initialized logger for final error handling
        main_logger.exception("An unexpected error occurred when attempting to fetch videos via yt-dlp")
        raise