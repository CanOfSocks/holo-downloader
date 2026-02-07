#!/usr/local/bin/python
import subprocess
from getConfig import ConfigHandler
import os
import logging
from typing import Optional, Dict, Any
from common import initialize_logging, kill_all

# The global instantiation and immediate logging setup are removed:
# getConfig = ConfigHandler()
# setup_logging(...)

def main(config: ConfigHandler = None, logger: logging = None):
    """
    Main function to process the community tab download.
    ConfigHandler is instantiated if not passed.
    """
    # 1. Instantiate ConfigHandler if it's not provided
    if config is None:
        config = ConfigHandler()

    if logger is None:
        logger = initialize_logging(config, "community_posts")
    
    # NOTE: Since this main function is the entry point, 
    # you might want to call initialize_logging(config) here 
    # if it hasn't been done elsewhere in your application.

    com_tab_folder = config.get_community_tab_directory()
    community_tab: Dict[str, str] = config.community_tab
    
    if config.randomise_lists() is True:
        # We assume 'common.py' contains the random_sample function from the previous context.
        # This import is left as-is, assuming 'common' is in the Python path.
        import common
        community_tab = common.random_sample(community_tab)
    
    if com_tab_folder:
        com_tab_archive = config.get_community_tab_archive()
        for channel in community_tab:
            if kill_all.is_set():
                break
            id = community_tab[channel]
            
            # Construct the base command
            command_list = ["python", "/app/ytct.py", "--reverse", "--dates", "-d", "{0}".format(os.path.join(com_tab_folder, channel))]
            
            if config.get_community_tab_cookies():
                command_list += ["--cookies", config.get_community_tab_cookies()]
            
            if com_tab_archive:
                command_list += ["--post-archive", com_tab_archive]
                
            command_list.append('"https://www.youtube.com/channel/{0}/posts"'.format(id))
            
            logger.debug(f"Executing command: {' '.join(command_list)}")

            # result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            #result = subprocess.run(command_list, capture_output=True, text=True)
            
            log_file = os.path.join(com_tab_folder, channel, "log.txt")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, 'a', encoding='utf-8') as f:                
                # Stream directly to file, do not capture in RAM
                subprocess.run(command_list, stdout=f, stderr=subprocess.STDOUT, text=True)

# Example of how you would run this script in your application entry point:
if __name__ == "__main__":
    try:
        # 1. Create the Config Handler instance
        app_config = ConfigHandler()
        
        # 2. Initialize logging using the instance
        logger = initialize_logging(config=app_config, logger_name="community-posts")
        
        # 3. Pass the instance to main()
        main(config=app_config, logger=logger)
    except Exception as e:
        logging.exception("An unhandled error occurred when fetching community posts")
        raise
