#!/usr/local/bin/python
import subprocess
from getConfig import ConfigHandler
from os import path

def main(command=None):
    getConfig = ConfigHandler()
    com_tab_folder = getConfig.get_community_tab_directory()
    community_tab = getConfig.community_tab
    
    if getConfig.randomise_lists() is True:
        import common
        community_tab = common.random_sample(community_tab)
    
    if com_tab_folder:
        com_tab_archive = getConfig.get_community_tab_archive()
        for channel in community_tab:
            id = community_tab[channel]
            command = ["python", "/app/ytct.py", "--dates", "-d", "{0}".format(path.join(com_tab_folder, channel))]
            if getConfig.get_cookies_file():
                command += ["--cookies", getConfig.get_cookies_file()]
            if com_tab_archive:
                command += ["--post-archive", com_tab_archive]
            command.append('"https://www.youtube.com/channel/{0}/community"'.format(id))
            
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

if __name__ == "__main__":
    main()