#!/usr/local/bin/python
from sys import argv
import common


def getVideos(channel_ids_to_match, command=None, frequency=None):
    from random import uniform
    all_lives = []
    for channel in channel_ids_to_match:
        try:
            #print("Looking for: {0}".format(channel))
            lives = common.get_upcoming_or_live_videos(channel_ids_to_match[channel], "streams")
            all_lives += lives
        except Exception as e:
            print(("Error fetching streams for {0}. Check cookies. \n{1}".format(channel,e)))
    common.vid_executor(all_lives, command)
       
def main(command=None, frequency=None):
    try:
        from config import channel_ids_to_match
        getVideos(channel_ids_to_match, command, frequency)
    except ImportError:
        pass
if __name__ == "__main__":
    try:
        command = argv[1]
    except IndexError:
        command = None
    try:
        frequency = argv[2]
    except IndexError:
        frequency = None

    main(command, frequency)
