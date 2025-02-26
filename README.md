# holo-downloader
A downloader for getting all streams for given hololive channels. This repo is designed primarily to be used in a Docker container, but the program should work on both Windows and Linux as is (with your choice of scheduler)

[Build on Docker Hub](https://hub.docker.com/r/canofsocks/holo-downloader)

Feature requests are welcomed

## Features
This program get the video, thumbnail, description, live chat and yt-dlp info.json file for all streams of given channels. It uses a temporary directory before moving all the files to a final folder after processing.

It also incorperates Discord Webhooks for status monitoring.

Please check out [Hoshinova](https://github.com/HoloArchivists/hoshinova) or [auto-ytarchive-raw](https://github.com/Spicadox/auto-ytarchive-raw/) for more advanced and/or customisable solutions, especially if you don't need a temporary folder and/or want only the video file.

Requires [livestream_dl](https://github.com/CanOfSocks/livestream_dl) and [ffmpeg](https://ffmpeg.org/)

## Usage
To use this container, you will need to have a temporary folder, a final folder and a cookie file.
Clone the repo and build the Docker container from within the root of the repo.

If using a container, you will need to create a copy of the config.py file for a persistent configuration.

Example with Docker hub:
```
docker pull 'canofsocks/holo-downloader:latest'
docker run -d --name='holo-downloader' --cpus=".75" -e TZ="Europe/London" -e HOST_CONTAINERNAME="holo-downloader" -e VIDEOSCHEDULE='*/2 * * * *' -e MEMBERSCHEDULE='*/5 * * * *' -e COMMUNITYSCHEDULE='0 */3 * * *' -v '/mnt/holo-downloader/config/config.py':'/app/config.py':'rw' -v '/mnt/holo-downloader/temp/':'/app/temp':'rw' -v '/mnt/holo-downloader/Done/':'/app/Done':'rw' -v '/mnt/holo-downloader/config/cookies.txt':'/app/cookies.txt':'rw' --restart always 'canofsocks/holo-downloader:latest'
```
## Configuration
Configuration is applied via the [`config.toml`](https://github.com/CanOfSocks/holo-downloader/blob/main/config.toml) file. Currently this must be placed in the same location as the [`getConfig.py`](https://github.com/CanOfSocks/holo-downloader/blob/main/getConfig.py) file. Ideas for making this more flexible are welcome.
Please check the `config.toml` file in this repo for the most up to date options.

### Adding channels
To add channels, add to the channel_ids_to_match block with a name and the channel ID of the video. The channel ID can be found at the share channel button on the about page for a channel.
```
[channel_ids_to_match]
"Gawr Gura Ch. hololive-EN" = "UCoSrY_IQQVpmIRZ9Xf-y93g"
"Watson Amelia Ch. hololive-EN" = "UCyl1z3jo3XHR1riLFKG5UAg"
"Mori Calliope Ch. hololive-EN" = "UCL_qhgtOy0dy1Agp8vkySQg"
"Fauna" = "UCO_aKKYxn4tvrqPjcTzZ6EQ"
```
### Filtering
For all of the filters, __if a filter is present then it will not be filtered__. For example, if no description filter is available, then no description filtering will be executed and filtering will rely on any remaining filters.

For title and description filtering, you will need to add the channel id along with a REGEX string to the respecive block. For example:
```
[title_filter]
"UCoSrY_IQQVpmIRZ9Xf-y93g" = "(?i).asmr|unarchive|karaoke|unarchived|no archive|WATCH-A-LONG|WATCHALONG|watch-along|birthday|offcollab|off-collab|off collab|SINGING."

[description_filter]
"UCoSrY_IQQVpmIRZ9Xf-y93g" = ".Calliope."
```
These strings are based off of the Python re library, so use syntax appropriate for that library.

### Members Only
Similar to the regular channel block. Any channels in this block have the "Membership" tab scanned when the getMembers script is run (periodically in the docker). Currently this gets all membership videos and does not use title or description filters.
The membership tab only scans the first 10 videos for possible live videos to reduce direct youtube requests. It is assumed that there will not be that many upcoming/live videos at once in almost all cases.
```
[members_only]
"Gawr Gura Ch. hololive-EN" = "UCoSrY_IQQVpmIRZ9Xf-y93g"
```

### Webhook
This block is used to define webhooks for notifications. This currently only supports a single discord webhook.
```
[webhook]
url = "https://discord.com/api/webhooks/xxxxxxxxxxxxxxxx/yyyyyyyyyyyyyyyyyyyy"
```

### Download Options
The download options must be under a `[download_options]` block.

#### Output
For the output templates, consult [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp#output-template)
The ```output_path``` contains the parent folder and names for children within that folder. 
This is required to have a depth >= 2 and the parent should be an option that will be unique to the video, such as %(fulltitle)s/%(fulltitle)s. If the depth is 1, the name will be duplicated to make a parent folder, for example ```output_folder = %(fulltitle)s``` will result in a structure of ```%(fulltitle)s/%(fulltitle)s``` in the output. This allows the easy movement of all resulting files from the temporary directory to the final directory.

A **good and recommended** example for this program is:
```
output_path = "%(channel)s/[%(upload_date)s] %(fulltitle)s - %(channel)s (%(id)s)/[%(upload_date)s] %(fulltitle)s - %(channel)s (%(id)s)"
```

There are several root folders for each type of stream. If a stream of a specific type below isn't listed the ```done_dir``` value is used.
```done_dir``` - Output directory for "regular" livestreams
```members_dir``` - Output directory for "Members Only" livestreams
```unarchived_dir``` - Output directory for unarchived streams that were retrieved by the stream recovery function of livestream_dl

There are also options for temporary folders:
```temp_dir``` - Temporary folder for livestreams during recording
```unarchived_tempdir``` - Stores info.jsons for unarchived stream recovery and temporary video data for stream recovery functions. ```temp_dir``` is used if this is not defined

#### Other download options
* ```video_fetch_method``` - Method for obtaining streams. ```ytdlp``` uses yt-dlp on the "streams" of a channel and checks the *first 10* videos if they are upcoming or live, which works for any youtube channel. ```json``` uses the [holo.dev api](https://holo.dev/api/v1/lives/open) to check for live and upcoming *Hololive* streams. If you're only scanning for Hololive content, it is highly recommended to use the ```json``` option. Channels specified in ```members_only``` config option will always use the ```ytdlp``` option.
* ```mux_file``` - Tells livestream_dl whether to combine the videos with ffmpeg after downloading, or leave the ts files with the mux command saved in a txt file
* ```download_threads``` - Sets the number of threads livestream_dl will use to download videos per video/audio stream. Default 4
* ```video_quality``` - Sets video quality
* ```video_only``` - When set to true, only the video is downloaded. Any other items (chat, thumbnail etc.) will not be downloaded
* ```download_chat``` - Downloads chat if true
* ```thumbnail``` - Downloads thumbnail to file if true
* ```info_json``` - Saves the info_json as a file if true
* ```description``` - Writes description file if true
* ```cookies_file``` - Absolute path for cookies file. This is required for membership and age-restricted streams. If you are using Docker, you should leave this as the default ```"/app/cookies.txt"``` and create a mount/mapping for the container instead.
* ```look_ahead``` - How many hours into the future to wait for a video e.g. 48 hours. Streams with a start time further in the future than this value will be ignored
* ```log_file``` - Log file to write to
* ```log_level``` - Log level to use. Can be DEBUG, INFO, WARNING, ERROR, CRITICAL
* ```keep_ts_files``` - Preserves .ts files made during recording
* ```write_ffmpeg_command``` - Writes the ffmpeg command used for merging to a file
* ```randomise_lists``` - Randomises channel fetch and video execution lists each runtime.
* ```remove_ip``` - Removes any IP addresses from info.json files
* ```clean_urls``` - Removes stream urls that could potentially be used for identification and replaces them with a dummy URL. This has little effect longterm due to expiry of most URLs.


### Scheduling
For container usage, you can change the frequency of how often videos and membership streams are checked for by adding docker environment variables ```VIDEOSCHEDULE``` and ```MEMBERSCHEDULE```. This must be in the cron format. For help: [crontab.guru](https://crontab.guru).

For example:
```-e VIDEOSCHEDULE='*/2 * * * *' -e MEMBERSCHEDULE='*/5 * * * *'```

By default, videos are checked every 2 minutes and membership videos every 5 minutes.

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
- [x] Automatic torrent creation
- [x] Configurable checking frequency
- [ ] Improve error detection
- [ ] ytarchive-raw integration
