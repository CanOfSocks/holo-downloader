#!/usr/local/bin/python
import argparse
import common
from getConfig import ConfigHandler
import logging

getConfig = ConfigHandler()
from livestream_dl.download_Live import setup_logging
setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file(), file_options=getConfig.get_log_file_options())
    
def getVideos(members_only, command=None, frequency=None):
    from random import uniform
    from time import sleep
    import discord_web
    all_lives = []
    for channel in members_only:
        sleep(uniform(5.0, 10.0))
        try:
            logging.debug("Looking for: {0}".format(channel))
            lives = common.get_upcoming_or_live_videos(members_only[channel], "membership")
            all_lives += lives
        except Exception as e:
            logging.exception(("Error fetching membership streams for {0}. Check cookies. \n{1}".format(channel,e)))
            discord_web.main(members_only[channel], "membership-error", message=str(e))
    common.vid_executor(all_lives, command)

def main(command=None, frequency=None):  
    getVideos(getConfig.members_only, command, frequency)

if __name__ == "__main__":
    try:
        # Create the parser
        parser = argparse.ArgumentParser(description="Process optional command and frequency values.")

        # Add an optional named argument '--command' with default as None
        parser.add_argument('--command', type=str, choices=['spawn', 'bash', 'print'], default=None, help='The command (optional, default: None)')

        # Add an optional named argument '--frequency' with default as None
        parser.add_argument('--frequency', type=str, default=None, help='The frequency value (optional, default: None)')

        # Parse the arguments
        args = parser.parse_args()

        # Access the arguments
        command = args.command
        frequency = args.frequency

        main(command, frequency)
    except Exception as e:
        logging.exception("An unexpected error occurred when attempting to members videos")
        raise
