# holo-downloader
A downloader for getting all streams for given hololive channels. This repo is designed primarily to be used in a Docker container, but the program should work on both Windows and Linux as is (with your choice of scheduler)

[Build on Docker Hub](https://hub.docker.com/r/canofsocks/holo-downloader)

Feature requests are welcomed

## Features
This program get the video, thumbnail, description, live chat and info.json file for all streams of given channels. It uses a temporary directory before moving all the files to a final folder after processing.

It also incorperates Discord Webhooks for status monitoring.

Please check out [Hoshinova](https://github.com/HoloArchivists/hoshinova) or [auto-ytarchive-raw](https://github.com/Spicadox/auto-ytarchive-raw/) for more advanced and customisable solutions, especially if you don't need a temporary folder and/or want only the video file.

Requires [ytarchive](https://github.com/Kethsar/ytarchive) and [ffmpeg](https://ffmpeg.org/)

## Usage
To use this container, you will need to have a temporary folder, a final folder and a cookie file.
Clone the repo and build the Docker container from within the root of the repo.

If using a container, you will need to create a copy of the config.py file for a persistent configuration.

Example with Docker hub:
```
docker pull 'canofsocks/holo-downloader:latest'
docker run -d --name='holo-downloader' --cpus=".75" -e TZ="Europe/London" -e HOST_CONTAINERNAME="holo-downloader" -v '/mnt/holo-downloader/config/config.py':'/app/config.py':'rw' -v '/mnt/holo-downloader/temp/':'/app/temp':'rw' -v '/mnt/holo-downloader/Done/':'/app/Done':'rw' -v '/mnt/holo-downloader/config/cookies.txt':'/app/cookies.txt':'rw' --restart always 'canofsocks/holo-downloader:latest'
```
## Configuration
For the most up to date configuration options, please check the config.py included in the repo

It is best practice to include a value for each of the variables in the config file. While some values have default fallbacks, others are required for the program's functionality. This can either be ```None``` for string and number values, or ```False``` for many of the boolean options.

### Adding channels
To add channels, add to the channel_ids_to_match dictionary with a name and the channel ID of the video. The channel ID can be found at the share channel button on the about page for a channel.

### Filtering
For all of the filters, __if a filter is present then it will not be filtered__. For example, if no description filter is available, then no description filtering will be executed and filtering will rely on any remaining filters.

For title and description filtering, you will need to add the channel id along with a REGEX string to the respecive dictionary. For example:
```
title_filter = {
    "UCoSrY_IQQVpmIRZ9Xf-y93g": "(?i).unarchive|karaoke|unarchived|no archive|SINGING."
}
description_filter = {
    "UCoSrY_IQQVpmIRZ9Xf-y93g": ".Calliope."
}
```
These strings are based off of the Python re library, so use syntax appropriate for that library.

Use the "members_only" filter for grabbing only membership videos. Set True to only get membership videos, or False for all videos:
```
members_only ={
    "UCoSrY_IQQVpmIRZ9Xf-y93g": True
}
```

### Output
For the output templates, consult [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp#output-template)
The ```output_folder``` contains the parent folder and names for children within that folder. 
This is required to have a depth >= 2 and the parent should be an option that will be unique to the video, such as %(fulltitle)s/%(fulltitle)s. If the depth is 1, the name will be duplicated to make a parent folder, for example ```output_folder = %(fulltitle)s``` will result in a structure of ```%(fulltitle)s/%(fulltitle)s``` in the output. This allows the easy movement of all resulting files from the temporary directory to the final directory.

A **good and recommended** example for this program is:
```
output_folder = "%(channel)s/[%(upload_date)s] %(fulltitle)s - %(channel)s (%(id)s)/[%(upload_date)s] %(fulltitle)s - %(channel)s (%(id)s)"
```

### Other
* ```webhook_url``` - Discord webhook url for Discord integration, disables if None
* ```mux_file``` - Tells ytarchive whether to combine the videos with ffmpeg after downloading, or leave the ts files with the mux command saved in a txt file
* ```download_threads``` - Sets the number of threads ytarchive will use to download videos. Default 4
* ```ytarchive_options``` - Any commands added to this string will be parsed to ytarchive at runtime
* ```video_quality``` - Sets video quality
* ```video_only``` - When set to true, only the video is downloaded. Any other items (chat, thumbnail etc.) will not be downloaded
* ```download_chat``` - Downloads chat if True
* ```thumbnail``` - Downloads thumbnail to file as PNG
* ```info_json``` - Saves the info_json as a file if True
* ```description``` - Writes description file if True
* ```cookies_file``` - Absolute path for cookies file. This is required for membership and age-restricted streams. If you are using Docker, you should leave this as the default ```"/app/cookies.txt"``` and create a mapping for the container instead.

### To-Do
While some components have been marked as added, testing of full functionalility may be required
- [x] Option to mux file or not
- [x] Options for auxillary data (thumbnails, description, info-json, chat)
- [x] Start time look-ahead config
- [x] Cookie file option
- [x] Downloader options
- [x] Title filtering
- [x] Description filtering
- [x] Membership only filtering
- [ ] Configurable checking frequency
- [ ] Improve error detection
- [ ] ytarchive-raw integration (maybe)
