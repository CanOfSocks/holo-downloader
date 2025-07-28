from livestream_dl.download_Live import download_live_chat
import common
from getConfig import ConfigHandler
import json
import argparse
import os
import logging

getConfig = ConfigHandler()

common.setup_umask()
from livestream_dl.download_Live import setup_logging
setup_logging(log_level=getConfig.get_log_level(), console=True, file=getConfig.get_log_file(), file_options=getConfig.get_log_file_options())
    

def main(json_file, output_path=None):
    
    try:
        with open(json_file, 'r', encoding='utf-8') as file:
            # Load the JSON data from the file
            info_dict = json.load(file)
    except Exception as e:
        logging.exception(e)
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
    with common.FileLock(lock_file_path) as lock_file:
        try:
            lock_file.acquire()
            result = download_live_chat(info_dict=info_dict, options=options)
            """
            if result is not None and isinstance(result, tuple):
                out_folder = os.path.dirname(options.get("output"))
                os.makedirs(out_folder)
                shutil.move(result[0], out_folder)
            """
            lock_file.release()
            return result
        except (IOError, BlockingIOError):
            logging.error("Unable to aquire lock for {0}, must be already downloading".format(lock_file_path))

if __name__ == "__main__":
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