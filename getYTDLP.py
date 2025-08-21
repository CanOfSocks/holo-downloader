#!/usr/local/bin/python
import argparse
import common
import time
from getConfig import ConfigHandler
import logging

getConfig = ConfigHandler()
from livestream_dl.download_Live import setup_logging
setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file(), file_options=getConfig.get_log_file_options())


def getVideos(channel_ids_to_match, command=None, unarchived = False, frequency=None):
    
    all_lives = []
    for channel in channel_ids_to_match:
        try:
            logging.debug("Looking for: {0}".format(channel))
            lives = common.get_upcoming_or_live_videos(channel_ids_to_match[channel], "streams")
            all_lives += lives
        except Exception as e:
            logging.exception(("Error fetching streams for {0}. Check cookies. \n{1}".format(channel,e)))
    if unarchived:
        all_lives = common.combine_unarchived(all_lives)

    common.vid_executor(streams=all_lives, command=command, frequency=frequency, unarchived=unarchived)
       
def main(command=None, unarchived = False, frequency=None):
    if unarchived:
        channel_ids_to_match = getConfig.unarchived_channel_ids_to_match
    else:
        channel_ids_to_match = getConfig.channel_ids_to_match
    if channel_ids_to_match:
        if getConfig.randomise_lists() is True:
            channel_ids_to_match = common.random_sample(channel_ids_to_match)
        getVideos(channel_ids_to_match, command, unarchived, frequency=frequency)

    
if __name__ == "__main__":
    try:
        # Create the parser
        parser = argparse.ArgumentParser(description="Process command and an optional unarchived flag.")

        # Add an optional named argument '--command' (default to None if not provided)
        parser.add_argument('--command', type=str, choices=['spawn', 'bash', 'print'], default=None, help='The command (optional, default: None)')

        # Add an optional flag '--unarchived' (set to True if provided, otherwise False)
        parser.add_argument('--unarchived', action='store_true', help='Flag to indicate unarchived (default: False)')

        # Parse the arguments
        args = parser.parse_args()
        # Access the arguments
        command = args.command
        unarchived = args.unarchived
        
        main(command=command, unarchived=unarchived)
    except Exception as e:
        logging.exception("An unexpected error occurred when attempting to fetch videos via yt-dlp")
        raise
