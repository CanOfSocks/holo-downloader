#!/usr/local/bin/python
from sys import argv
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
        import unarchived
        import threading
        import time
        threads = []
        streams = common.combine_unarchived(streams)
        
        # Threading may be unnecessary
        for stream in all_lives:
            t = threading.Thread(target=unarchived.main, args=(stream,), daemon=True)
            threads.append(t)
            t.start()
            time.sleep(3.0)
        for t in threads:
            t.join()
    else:
        common.vid_executor(all_lives, command)
       
def main(command=None, unarchived = False):
    try:
        if unarchived:
            from config import unarchived_channel_ids_to_match as channel_ids_to_match
        else:
            from config import channel_ids_to_match
        if channel_ids_to_match:
            getVideos(channel_ids_to_match, command, unarchived)
    except ImportError:
        pass
if __name__ == "__main__":
    try:
        command = argv[1]
    except IndexError:
        command = None
    try:
        unarchived = argv[2]
    except IndexError:
        pass

    main(command, unarchived=unarchived)
