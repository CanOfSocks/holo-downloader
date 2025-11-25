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
from common import FileLock, setup_umask

import argparse

import signal
from time import sleep
import platform

kill_all = threading.Event()

# Preserve original keyboard interrupt logic as true behaviour is known
original_sigint = signal.getsignal(signal.SIGINT)

def handle_shutdown(signum, frame):
    kill_all.set()
    sleep(0.5)
    if callable(original_sigint):
        original_sigint(signum, frame)

# common
signal.signal(signal.SIGINT, handle_shutdown)

if platform.system() == "Windows":
    # SIGTERM won’t fire — but SIGBREAK will on Ctrl-Break
    signal.signal(signal.SIGBREAK, handle_shutdown)
else:
    # normal POSIX termination
    signal.signal(signal.SIGTERM, handle_shutdown)

getConfig = ConfigHandler()

import logging

setup_umask()
from livestream_dl.download_Live import setup_logging
logger = setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file(), file_options=getConfig.get_log_file_options())

#id = sys.argv[1]
#id = "kJGsWORSg-4"
#outputFile = None



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
        downloader = download_Live.LiveStreamDownloader(kill_all=kill_all, logger=logger)
        downloader.download_segments(info_dict=info_dict, resolution=getConfig.get_quality(), options=options)
    except Exception as e:
        logger.exception("Error occured {0}".format(id))
        #discord_web.main(id, "error", message=str(e)[-500:])
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
    #    'wait_for_video': (1,300),
    #   'retries': 25,
    #    'skip_download': True,
        'outtmpl': getConfig.get_ytdlp(),
    #    'cookiefile': getConfig.get_cookies_file(),        
        'quiet': True,
        'no_warnings': True       
    }
    import json
    #if getConfig.get_ytdlp_options() is not None:
    #    options.update({'ytdlp_options': json.loads(getConfig.get_ytdlp_options())})

    with yt_dlp.YoutubeDL(options) as ydl:
        #info_dict = ydl.extract_info(video_url, download=False)
        from livestream_dl import getUrls
        additional_ytdlp_options = None
        if getConfig.get_ytdlp_options():
            additional_ytdlp_options = json.loads(getConfig.get_ytdlp_options())
        info_dict, live_status = getUrls.get_Video_Info(id=video_url,  wait=(1,300), cookies=getConfig.get_cookies_file(), proxy=getConfig.get_proxy(), additional_options=additional_ytdlp_options, include_dash=getConfig.get_include_dash(), include_m3u8=getConfig.get_include_m3u8())
        #info_dict = ydl.sanitize_info(info_dict)
        outputFile = str(ydl.prepare_filename(info_dict))
            
    logger.debug("({0}) Info.json: {1}".format(video_url, json.dumps(info_dict)))
    logger.info("Output file: {0}".format(outputFile))
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
    global logger
    logger = setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file(), file_options=getConfig.get_log_file_options(), logger_name=id)
    
    if os.path.exists("/dev/shm/"):
        lock_file_path = "/dev/shm/videoDL-{0}".format(id)
    else:
        lock_file_path = os.path.join(getConfig.get_temp_folder(), "videoDL-{0}.lockfile".format(id))

    lock = FileLock(lock_file_path)
    '''
    try:
        lock.acquire()
        discord_web.main(id, "waiting")
        outputFile, info_dict = download_video_info(id)
        logging.debug("Output file: {0}".format(outputFile))
        if outputFile is None:
            discord_web.main(id, "error")
            raise Exception(("Unable to retrieve information about video {0}".format(id)))
        
        downloader(id,outputFile, info_dict)        
    except (IOError, BlockingIOError):
        logging.info("Unable to acquire lock for {0}, must be already downloading".format(lock_file_path))
    finally:
        try:
            lock.release()
        except Exception:
            pass
    '''
    try:
        with FileLock(lock_file_path) as lock_file:
        
            lock_file.acquire()
            discord_web.main(id, "waiting")
            try:
                outputFile, info_dict = download_video_info(id)
                logger.debug("Output file: {0}".format(outputFile))
                if outputFile is None:
                    raise LookupError(("Unable to retrieve information about video {0}".format(id)))
                
                downloader(id,outputFile, info_dict)
                """
                if result is not None and isinstance(result, tuple):
                    out_folder = os.path.dirname(options.get("output"))
                    os.makedirs(out_folder)
                    shutil.move(result[0], out_folder)
                """
            except Exception as e:
                discord_web.main(id, "error", message=f"{type(e).__name__}: {str(e)}"[-500:])
                logger.exception("Error downloading video")

            lock_file.release()
    except (IOError, BlockingIOError) as e:
        logger.info("Unable to aquire lock for {0}, must be already downloading: {1}".format(lock_file_path, e))
    
    """
    if is_script_running(script_name, id):
        logging.debug("{0} already running, exiting...".format(id))
        return 0
    """
    

if __name__ == "__main__":
    try:
        # Create the parser
        parser = argparse.ArgumentParser(description="Process an video by ID")

        # Add a required positional argument 'ID'
        parser.add_argument('ID', type=str, help='The video ID (required)')

        # Parse the arguments
        args = parser.parse_args()

        # Access the 'ID' value
        id = args.ID
        main(id=id)
    except Exception as e:
        logger.exception("An unhandled error occurred when attempting to download a video")
        raise
