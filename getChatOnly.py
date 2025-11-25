from livestream_dl.download_Live import LiveStreamDownloader
import common
from getConfig import ConfigHandler
import json
import argparse
import os
import logging

import threading
import signal
from time import sleep

kill_all = threading.Event()

# Preserve original keyboard interrupt logic as true behaviour is known
original_sigint = signal.getsignal(signal.SIGINT)

def handle_shutdown(signum, frame):
    kill_all.set()
    sleep(0.5)
    if callable(original_sigint):
        original_sigint(signum, frame)

getConfig = ConfigHandler()

common.setup_umask()
from livestream_dl.download_Live import setup_logging
logger = setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file(), file_options=getConfig.get_log_file_options())
    

def main(json_file, output_path=None):
    global logger
    logger = setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file(), file_options=getConfig.get_log_file_options(),logger_name=info_dict.get('id'))
    try:
        with open(json_file, 'r', encoding='utf-8') as file:
            # Load the JSON data from the file
            info_dict = json.load(file)
    except Exception as e:
        logger.exception(e)
        return
    
    options = {
        "ID": info_dict.get('id'),
        "output": output_path if output_path is not None else str(getConfig.get_unarchived_output_path(getConfig.get_ytdlp())),
        "temp_folder": getConfig.get_unarchived_temp_folder(),
        "cookies": getConfig.get_cookies_file(),
        "log_level": getConfig.get_log_level(),
        #"log_level": "DEBUG",
        "log_file": getConfig.get_log_file(),
    }
    if os.path.exists("/dev/shm"):
        lock_file_path = "/dev/shm/chat-{0}".format(options.get("ID"))
    else:
        lock_file_path = os.path.join(options.get("temp_folder"), "chat-{0}.lockfile".format(options.get("ID")))
    '''
    lock = common.FileLock(lock_file_path)

    try:
        lock.acquire()
        result = download_live_chat(info_dict=info_dict, options=options)
        return result
    except (IOError, BlockingIOError):
        logging.info("Unable to acquire lock for {0}, must be already downloading".format(lock_file_path))
        return None
    finally:
        try:
            lock.release()
        except Exception:
            pass
    '''
    try:
        with common.FileLock(lock_file_path) as lock_file:
        
            lock_file.acquire()
            downloader = LiveStreamDownloader(kill_all=kill_all, logger=logger)
            result = downloader.download_live_chat(info_dict=info_dict, options=options)
            """
            if result is not None and isinstance(result, tuple):
                out_folder = os.path.dirname(options.get("output"))
                os.makedirs(out_folder)
                shutil.move(result[0], out_folder)
            """
            lock_file.release()
            return result
    except (IOError, BlockingIOError) as e:
        logger.info("Unable to aquire lock for {0}, must be already downloading: {1}".format(lock_file_path, e))
    return None
    

if __name__ == "__main__":
    try:
        # Create the parser
        parser = argparse.ArgumentParser(description="Download chat of a video using info.json")

        # Add a required positional argument 'ID'
        parser.add_argument('json', type=str, help='info.json path (required)')

        parser.add_argument('--output-path', type=str, default=None, help='Optional output path')

        # Parse the arguments
        args = parser.parse_args()

        # Access the values
        json_file = args.json
        output_path = args.output_path

        main(json_file=json_file, output_path=output_path)
    except Exception as e:
        logger.exception("An unhandled error occurred when trying to download chat")