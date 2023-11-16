# holo-downloader
A downloader for getting all streams for given hololive channels

[Build on Docker Hub](https://hub.docker.com/r/canofsocks/holo-downloader)

Feature requests are welcomed

## Features
This program get the video, thumbnail, description, live chat and info.json file for all streams of given channels. It uses a temporary directory before moving all the files to a final folder after processing.

It also incorperates Discord Webhooks for status monitoring.

Please check out [Hoshinova](https://github.com/HoloArchivists/hoshinova) or [auto-ytarchive-raw](https://github.com/Spicadox/auto-ytarchive-raw/) for more advanced and customisable solutions, especially if you don't need a temporary folder and/or want only the video file.

## Usage
To use this container, you will need to have a temporary folder, a final folder and a cookie file. Currently, a cookie file is mandatory.
Clone the repo and build the Docker container from within the root of the repo.

If using a container, you will need to create a copy of the config.py file for a persistent configuration.

Example with Docker hub:
```
docker pull 'canofsocks/holo-downloader:latest'
docker run -d --name='holo-downloader' --cpus=".75" -e TZ="Europe/London" -e HOST_CONTAINERNAME="holo-downloader" -v '/mnt/holo-downloader/config/config.py':'/app/config.py':'rw' -v '/mnt/holo-downloader/temp/':'/app/temp':'rw' -v '/mnt/holo-downloader/Done/':'/app/Done':'rw' -v '/mnt/holo-downloader/config/cookies.txt':'/app/cookies.txt':'rw' --restart always 'canofsocks/holo-downloader:latest'
```



### To-Do
- [x] Option to mux file or not
- [x] Start time look-ahead config
- [ ] Title filtering
- [ ] Description filtering
- [ ] Configurable checking frequency
- [ ] Improve error detection
