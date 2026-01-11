#!/usr/local/bin/python
from getConfig import ConfigHandler
from common import initialize_logging
import argparse
import logging
from typing import Optional
import queue


def main(command: Optional[str] = None, unarchived: bool = False, frequency: Optional[str] = None, config: ConfigHandler = None, logger: logging = None, queue: queue.Queue= None) -> list[str] | str:
    """
    Main entry point to determine which video fetching method to use.
    
    :param command: The command to execute on the fetched video IDs.
    :param unarchived: Flag to indicate if unarchived videos should be included.
    :param frequency: The CRON frequency string.
    :param config: The ConfigHandler object, defaults to a new instance if None.
    """
    # Instantiate ConfigHandler if it's not provided
    if config is None:
        config = ConfigHandler()

    if logger is None:
        logger = initialize_logging(config, "getVids")

    method = config.get_fetch_method()
    
    if(method == "ytdlp"):
        import getYTDLP
        # Assuming getYTDLP.main is updated to accept config as its final argument
        return getYTDLP.main(command, unarchived=unarchived, frequency=frequency, config=config, queue=queue)
    elif(method == "json"):
        import getJson
        # Assuming getJson.main is updated to accept config as its final argument
        return getJson.main(command, unarchived=unarchived, frequency=frequency, config=config, queue=queue)
    else:
        # Use a standard logger if main is called without prior initialization
        logger.error("Invalid fetch method: {0}".format(method))
    

if __name__ == "__main__":
    try:
        # 1. Instantiate ConfigHandler once for the execution flow
        app_config = ConfigHandler()

        # 2. Initialize logging using the instance
        logger = initialize_logging(app_config, "getVids") 
    
    
        # Create the parser
        parser = argparse.ArgumentParser(description="Process command and an optional unarchived flag.")

        # Add arguments
        parser.add_argument('--command', type=str, choices=['spawn', 'bash', 'print'], default=None, help='The command (optional, default: None)')
        parser.add_argument('--unarchived', action='store_true', help='Flag to indicate unarchived (default: False)')
        parser.add_argument('--frequency', type=str, default=None, help='The cron schedule (optional, default: None)')

        # Parse the arguments
        args = parser.parse_args()
        
        # Call main, passing the config object
        main(command=args.command, unarchived=args.unarchived, frequency=args.frequency, config=app_config)

    except Exception as e:
        # The initialized logger is now used for the final error logging
        logging.exception("An unhandled error occurred while attempting to fetch videos")
        raise