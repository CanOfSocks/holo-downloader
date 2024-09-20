#!/usr/local/bin/python
import argparse
import common


def getVideos(channel_ids_to_match, command=None, unarchived = False):
    from random import uniform
    all_lives = []
    for channel in channel_ids_to_match:
        try:
            #print("Looking for: {0}".format(channel))
            lives = common.get_upcoming_or_live_videos(channel_ids_to_match[channel], "streams")
            all_lives += lives
        except Exception as e:
            print(("Error fetching streams for {0}. Check cookies. \n{1}".format(channel,e)))
    if unarchived:
        all_lives = common.combine_unarchived(all_lives)

    common.vid_executor(all_lives, command)
       
def main(command=None, unarchived = False, frequency=None):
    try:
        if unarchived:
            from config import unarchived_channel_ids_to_match as channel_ids_to_match
        else:
            from config import channel_ids_to_match
        if channel_ids_to_match:
            getVideos(channel_ids_to_match, command, unarchived, frequency=frequency)
    except ImportError:
        pass
    
if __name__ == "__main__":
    # Create the parser
    parser = argparse.ArgumentParser(description="Process command and an optional unarchived flag.")

    # Add an optional named argument '--command' (default to None if not provided)
    parser.add_argument('--command', type=str, default=None, help='The command (optional, default: None)')

    # Add an optional flag '--unarchived' (set to True if provided, otherwise False)
    parser.add_argument('--unarchived', action='store_true', help='Flag to indicate unarchived (default: False)')

    # Parse the arguments
    args = parser.parse_args()
    # Access the arguments
    command = args.command
    unarchived = args.unarchived
    
    main(command=command, unarchived=unarchived)
