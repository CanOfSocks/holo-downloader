#!/usr/local/bin/python
from getConfig import ConfigHandler
import argparse
import logging

getConfig = ConfigHandler()

from livestream_dl.download_Live import setup_logging
setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file(), force=True, file_options=getConfig.get_log_file_options())

def main(command=None, unarchived=False, frequency=None):
    
    method = getConfig.get_fetch_method()
    if(method == "ytdlp"):
        import getYTDLP
        getYTDLP.main(command,unarchived=unarchived,frequency=frequency)
    elif(method == "json"):
        import getJson
        getJson.main(command,unarchived=unarchived,frequency=frequency)
    else:
        logging.error("Invalid method: {0}".format(method))

if __name__ == "__main__":
    # Create the parser
    parser = argparse.ArgumentParser(description="Process command and an optional unarchived flag.")

    # Add an optional named argument '--command' (default to None if not provided)
    parser.add_argument('--command', type=str, default=None, help='The command (optional, default: None)')

    # Add an optional flag '--unarchived' (set to True if provided, otherwise False)
    parser.add_argument('--unarchived', action='store_true', help='Flag to indicate unarchived (default: False)')
    
    parser.add_argument('--frequency', type=str, default=None, help='The cron schedule (optional, default: None)')

    # Parse the arguments
    args = parser.parse_args()
    # Access the arguments
    command = args.command
    unarchived = args.unarchived
    
    main(command=command, unarchived=unarchived)