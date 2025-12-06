#!/usr/local/bin/python
import argparse
import common
from getConfig import ConfigHandler
import logging
from typing import Optional, Dict, List, Any
import discord_web 

from random import uniform
from time import sleep

def main(command: Optional[str] = None, frequency: Optional[str] = None, config: ConfigHandler = None, logger: logging = None) -> list[str] | str:
    """
    Fetches member-only videos for the given channels and executes a command on them.
    
    :param members_only: Dictionary of channel names to channel IDs (or playlist IDs).
    :param command: Command to execute ('spawn', 'bash', 'print').
    :param frequency: Optional CRON frequency string.
    :param config: The ConfigHandler object, defaults to a new instance if None.
    """
    # Instantiate ConfigHandler if it's not provided
    config = config or ConfigHandler()

    logger = logger or common.initialize_logging(config, "getMembers")

    all_lives: List[str] = []
    
    members_only = config.members_only

    for channel_name, channel_id in members_only.items():
        # Rate limit the API calls
        sleep(uniform(5.0, 10.0))
        
        try:
            logger.debug(f"Looking for membership streams for: {channel_name} ({channel_id})")
            
            # Assuming common.get_upcoming_or_live_videos is updated to accept config
            lives = common.get_upcoming_or_live_videos(channel_id, config, tab="membership")
            all_lives.extend(lives)
            
        except Exception as e:
            error_message = f"Error fetching membership streams for {channel_name}. Check cookies."
            logger.exception(error_message)
            
            # Assuming discord_web.main is updated to accept config, although we're relying on 
            # its internal default config creation here if not explicitly passed.
            # We are passing the channel ID as the target of the error.
            discord_web.main(channel_id, "membership-error", message=str(e))
        
    return common.vid_executor(all_lives=all_lives, command=command, config=config, frequency=frequency)

if __name__ == "__main__":
    try:
        # Instantiate ConfigHandler once for the execution flow
        app_config = ConfigHandler()

        # Initialize logging using the instance
        logger = common.initialize_logging(app_config, "getMembers") 
    
    
        # Create the parser
        parser = argparse.ArgumentParser(description="Process optional command and frequency values.")

        # Add an optional named argument '--command' with default as None
        parser.add_argument('--command', type=str, choices=['spawn', 'bash', 'print'], default=None, help='The command (optional, default: None)')

        # Add an optional named argument '--frequency' with default as None
        parser.add_argument('--frequency', type=str, default=None, help='The frequency value (optional, default: None)')

        # Parse the arguments
        args = parser.parse_args()

        # Call main, passing the config object
        main(command=args.command, frequency=args.frequency, config=app_config, logger=logger)
        
    except Exception as e:
        logging.exception("An unexpected error occurred when attempting to fetch members videos")
        raise
