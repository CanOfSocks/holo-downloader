#!/usr/local/bin/python
import yt_dlp
#import psutil
import os
#import sys
import threading
from getConfig import ConfigHandler
from pathlib import Path
import subprocess
import discord_web
import traceback
from time import sleep, asctime
from common import FileLock

import argparse

getConfig = ConfigHandler()

import logging

from livestream_dl.download_Live import setup_logging
setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file(), file_options=getConfig.get_log_file_options())

#id = sys.argv[1]
#id = "kJGsWORSg-4"
#outputFile = None
kill_all = False

def createTorrent(output):
    if not getConfig.getTorrent():
        return
    fullPath = getConfig.getTempOutputPath(output)
    folder = Path(fullPath).parent
    
    torrentRunner = subprocess.run(getConfig.torrentBuilder(fullPath,folder), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        
def downloader(id,outputTemplate, info_dict):
    if id is None or outputTemplate is None:
        raise Exception(("Unable to retrieve information about video {0}".format(id)))
    from livestream_dl import download_Live
    #outputFile = "{0}{1}".format(getConfig.getTempFolder(),output)
    
    options = getConfig.get_livestream_dl_options(info_dict=info_dict, output_template=outputTemplate)
    #output = str(getConfig.get_temp_output_path(outputTemplate))
    
    # Start additional information downloaders
    discord_notify = threading.Thread(target=discord_web.main, args=(id, "recording"), daemon=True)
    discord_notify.start()    
    try:
        download_Live.download_segments(info_dict, getConfig.get_quality(), options)
    except Exception as e:
        logging.exception("Error occured {0}".format(id))
        discord_web.main(id, "error", message=str(e)[-500:])
        global kill_all
        kill_all = True
        sleep(1.0)
        raise Exception(("{2} - Error downloading video: {0}, Code: {1}".format(id, e, asctime())))
        return
    # Wait for remaining processes
    discord_notify.join()
        
    #if(getConfig.getTorrent()):
    #    try:
    #        createTorrent(outputTemplate)
    #    except subprocess.CalledProcessError as e:
    #        print(e.stderr)
    #        discord_web.main(id, "error", message=str(e.stderr)[-1500:])
    #        raise Exception(("Error creating torrent for video: {0}, Code: {1}".format(id, e.returncode)))
        
    discord_web.main(id, "done")
    return

def download_video_info(video_url):
    options = {
        'wait_for_video': (1,300),
        'retries': 25,
        'skip_download': True,
        'outtmpl': getConfig.get_ytdlp(),
        'cookiefile': getConfig.get_cookies_file(),        
        'quiet': True,
        'no_warnings': True       
    }

    if getConfig.get_ytdlp_options() is not None:
        import json
        options.update({'ytdlp_options': json.loads(getConfig.get_ytdlp_options())})

    with yt_dlp.YoutubeDL(options) as ydl:
        info_dict = ydl.extract_info(video_url, download=False)
        info_dict = ydl.sanitize_info(info_dict)
        outputFile = str(ydl.prepare_filename(info_dict))
            
        
    logging.info("Output file: {0}".format(outputFile))
    return outputFile, info_dict
"""
def is_script_running(script_name, id):
    current = psutil.Process()
    logging.debug("PID: {0}, command line: {1}, argument: {2}".format(current.pid, current.cmdline(), current.cmdline()[2:]))
    current_pid = psutil.Process().pid
    
    for process in psutil.process_iter():
        try:
            process_cmdline = process.cmdline()
            if (
                process.pid != current_pid and
                script_name in process_cmdline and
                id in process_cmdline[2:]   # Needs testing between Windows and Postix to ensure compatibility
            ):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False
"""    
def main(id=None):
    
    #script_name = sys.argv[0]    

    # If ID is none, raise exception
    if id is None:
        raise ValueError("No video ID provided, unable to continue")
    
    if os.path.exists("/dev/shm"):
        lock_file_path = "/dev/shm/videoDL-{0}".format(id)
    else:
        lock_file_path = os.path.join(getConfig.get_temp_folder(), "videoDL-{0}.lockfile".format(id))
    with FileLock(lock_file_path) as lock_file:
        try:
            lock_file.acquire()
            discord_web.main(id, "waiting")
            outputFile, info_dict = download_video_info(id)
            logging.debug("Output file: {0}".format(outputFile))
            if outputFile is None:
                discord_web.main(id, "error")
                raise Exception(("Unable to retrieve information about video {0}".format(id)))
            
            downloader(id,outputFile, info_dict)
            """
            if result is not None and isinstance(result, tuple):
                out_folder = os.path.dirname(options.get("output"))
                os.makedirs(out_folder)
                shutil.move(result[0], out_folder)
            """
            lock_file.release()
        except (IOError, BlockingIOError):
            logging.error("Unable to aquire lock for {0}, must be already downloading".format(lock_file_path))
    """
    if is_script_running(script_name, id):
        logging.debug("{0} already running, exiting...".format(id))
        return 0
    """
    

if __name__ == "__main__":
    # Create the parser
    parser = argparse.ArgumentParser(description="Process an video by ID")

    # Add a required positional argument 'ID'
    parser.add_argument('ID', type=str, help='The video ID (required)')

    # Parse the arguments
    args = parser.parse_args()

    # Access the 'ID' value
    id = args.ID
    main(id=id)
