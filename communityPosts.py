#!/usr/local/bin/python
import subprocess
import getConfig
from os import path

def main(command=None):
    from config import community_tab
    com_tab_folder = getConfig.getCommunityTabDirectory()
    if com_tab_folder:
        com_tab_archive = getConfig.getCommunityTabArchive()
        for channel in community_tab:
            id = community_tab[channel]
            command = ["python", "ytct.py", "--dates", "-d", "{0}".format(path.join(com_tab_folder, channel))]
            if getConfig.getCookiesFile():
                command += ["--cookies", getConfig.getCookiesFile()]
            if com_tab_archive:
                command += ["--post-archive", com_tab_archive]
            command.append('"https://www.youtube.com/channel/{0}/community"'.format(id))
            
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    
    


if __name__ == "__main__":
    main()