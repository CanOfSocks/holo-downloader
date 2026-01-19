# Holo-Downloader (StreamArchiver)

A robust, schedule-based downloader designed for archiving live streams from YouTube channels. While designed primarily for **Hololive** content, it works with any YouTube channel.

The program supports standard streams, members-only content, unarchived (private/deleted) streams, and community posts. It is designed primarily to be used in a Docker container, but works on Windows and Linux (with `livestream_dl` and `ffmpeg` installed).

**[Build on Docker Hub](https://hub.docker.com/r/canofsocks/holo-downloader)**

## Features

* **Comprehensive Archiving**: Downloads video, thumbnail, description, live chat, and `info.json` metadata.
* **Multiple Modes**:
  * **Standard**: Archives upcoming and live streams.
  * **Members Only**: Archives streams from the membership tab (requires cookies).
  * **Unarchived**: Monitors and captures streams that are set to private or deleted immediately after ending (uses "stream recovery" logic).
  * **Community Tab**: Archives community posts and images.


* **Resiliency**: Uses `livestream_dl` (a custom wrapper around `yt-dlp` and `ffmpeg`) to handle stream interruptions and segment merging.
* **Web UI**: A Flask-based interface (port 5000) to view active downloads, history, and active schedules.
* **Notifications**: Integrates with Discord Webhooks for status updates (Recording, Done, Error).
* **Flexible Filtering**: Filter downloads by regex matching on video titles or descriptions.

## Docker Usage

The recommended way to run this application is via Docker. You will need to create a `config.toml`, a temporary folder, a final download folder, and a `cookies.txt` file (if using Members/Unarchived features).

### Run Command

```bash
docker pull 'canofsocks/holo-downloader:latest'

docker run -d \
  --name='holo-downloader' \
  --cpus=".75" \
  --restart always \
  -e TZ="Europe/London" \
  -e PUID=99 \
  -e PGID=100 \
  -p '10765:5000/tcp' \
  -v '/mnt/holo-downloader/config/config.toml':'/app/config.toml':'rw' \
  -v '/mnt/holo-downloader/temp/':'/app/temp':'rw' \
  -v '/mnt/holo-downloader/Done/':'/app/Done':'rw' \
  -v '/mnt/holo-downloader/config/cookies.txt':'/app/cookies.txt':'rw' \
  'canofsocks/holo-downloader:web-ui'

```

### Environment Variables

These variables configure the container runtime.

| Variable | Default | Description |
| --- | --- | --- |
| `PUID` | *None* | User ID to run the application as. Maps file permissions on the host. |
| `PGID` | *None* | Group ID to run the application as. Maps file permissions on the host. |
| `TZ` | *None* | Timezone for the container (e.g., `Europe/London`). |
| `PORT` | `5000` | The port the Web UI listens on inside the container. |
| `UMASK` | *None* | Sets the file creation mask (permissions) for downloaded files. |
| `VIDEOSCHEDULE` | *Internal Cron* | Override the check frequency for videos (e.g., `'*/2 * * * *'`). |
| `MEMBERSCHEDULE` | *Internal Cron* | Override the check frequency for members streams (e.g., `'*/5 * * * *'`). |
| `SECRET_KEY` | `dev-key...` | Secret key for Flask sessions. Change for security in production. |

---

## Configuration (`config.toml`)

Configuration is applied via the `config.toml` file. This file must be mounted to `/app/config.toml` in the container.

### 1. Global Schedules

Define Cron expressions for how often different checks run.
*Format: Minute Hour Day_of_Month Month Day_of_Week*

> **Note:** If a scheduled event is running during the next scheduled run, the new run is skipped. Only one instance of a check runs at a time.

```toml
[cron_schedule]
streams = "*/30 * * * *"          # Check standard streams
members_only = "15,45 * * * *"    # Check members-only tab
unarchived = "45 * * * *"         # Check for unarchived streams
community_posts = "0 0 * * *"     # Check community posts

```

### 2. Channels

Channels are defined by their display name and Channel ID (found in the URL of the channel).

#### Standard Channels

```toml
[channel_ids_to_match]
"Saba" = "UCxsZ6NCzjU_t4YSxQLBcM5A"
"Gawr Gura Ch. hololive-EN" = "UCoSrY_IQQVpmIRZ9Xf-y93g"
"Fauna" = "UCO_aKKYxn4tvrqPjcTzZ6EQ"

```

#### Members Only
*Channels here have their "Membership" tab scanned. Requires `cookies.txt`.*

```toml
[members_only]
"Saba" = "UCxsZ6NCzjU_t4YSxQLBcM5A"
"Gawr Gura Ch. hololive-EN" = "UCoSrY_IQQVpmIRZ9Xf-y93g"

```

#### Unarchived (Privated) Streams
*Monitors for streams that become unavailable/private immediately after ending. Requires `cookies.txt`.*   
The "unarchived channels" option saves the video stream urls (info.json file) to the temporary directory and refreshes it periodically (hourly). If a stream becomes inaccessible before the stream URLs expire (about 6 hours after extraction), an attempt to download the stream via livesteam_dl's `--recovery` option is triggered using the saved info.json file.  

> **Warning:** This option should be treated as a last resort option as it is currently unreliable. Streams that are important to you should always be recorded live.

```toml
[unarchived_channel_ids_to_match]
"Gawr Gura Ch. hololive-EN" = "UCoSrY_IQQVpmIRZ9Xf-y93g"

```

### 3. Filtering

Filters allow you to restrict downloads based on REGEX strings.

* **Logic:** If a filter is present, it **will** be used. Filters are **inclusive OR**; if either the title OR description matches the regex, the video is downloaded.
* **Syntax:** Python `re` library syntax.

```toml
[title_filter]
"UCoSrY_IQQVpmIRZ9Xf-y93g" = "(?i).asmr|unarchive|karaoke|watch-along"

[description_filter]
"UCoSrY_IQQVpmIRZ9Xf-y93g" = ".Calliope."

```

### 4. Webhook

Defines the Discord webhook for notifications.

```toml
[webhook]
url = "https://discord.com/api/webhooks/xxxxxxxxxxxxxxxx/yyyyyyyyyyyyyyyyyyyy"

```

### 5. Download Options

These options control file paths, quality, and processing logic. Place these under `[download_options]`.

#### Output Path Configuration

The `output_path` template follows [yt-dlp output template](https://github.com/yt-dlp/yt-dlp#output-template) rules.

> **Important:** The path requires a depth ≥ 2. The parent folder should be unique to the video to allow easy movement from the `temp` folder to `done` folder.
> * **Bad:** `%(fulltitle)s`
> * **Good:** `%(channel)s/[%(upload_date)s] %(fulltitle)s (%(id)s)/[%(upload_date)s] %(fulltitle)s (%(id)s)`
> 
> 

#### Options Table

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| **Path Options** |  |  |  |
| `output_path` | String | `%(fulltitle)s/%(fulltitle)s` | **Required.** Template for output filename/path. |
| `temp_dir` | String | `/app/temp/` | Directory for active/temporary downloads. |
| `done_dir` | String | `/app/Done/` | Directory for finished standard downloads. |
| `members_dir` | String | `/app/Done/` | Directory for finished members-only downloads. |
| `unarchived_dir` | String | `/app/Done/` | Directory for finished unarchived downloads. |
| `cookies_file` | String | `/app/cookies.txt` | Path to cookies file (Required for Members/Age-gated). |
| **Quality & Fetching** |  |  |  |
| `video_quality` | String | `best` | Target resolution (e.g., `best`, `1080p`, `720p`). |
| `download_threads` | Int | `4` | Threads used by livestream_dl for downloading. |
| `video_fetch_method` | String | `ytdlp` | `ytdlp` (scans channel page) or `json` (uses holo.dev API, Hololive only). |
| `look_ahead` | Int | `48` | Hours into the future to schedule downloads for. |
| `proxies` | String | *None* | Proxy URL (e.g., `"http://10.0.1.2:3128"`). |
| `randomise_lists` | Bool | `False` | Randomize channel check order to reduce bot detection patterns. |
| `ytdlp_options` | String| *None* | A JSON string that will be added to the yt-dlp options for stream URL extraction. E.g `'{"extractor_args":{"youtubepot-bgutilhttp":{"base_url":["http://bgutil-provider:4416"]}}}'` |
| **Processing** |  |  |  |
| `mux_file` | Bool | `True` | Merge video/audio into a single file after download. |
| `remux_extension` | String | *None* | Extension to remux final file to (e.g., `mp4`, `mkv`). |
| `keep_ts_files` | Bool | `False` | Keep the raw `.ts` segments after merging. |
| `write_ffmpeg_command` | Bool | `False` | Writes the FFmpeg command used to a text file (debugging). |
| **Metadata & Extras** |  |  |  |
| `video_only` | Bool | `False` | If true, skips chat, thumbnails, and metadata. |
| `download_chat` | Bool | `False` | Download live chat (archived streams). |
| `thumbnail` | Bool | `False` | Download and embed thumbnail. |
| `info_json` | Bool | `False` | Write `info.json` metadata file. |
| `description` | Bool | `False` | Write description to a text file. |
| `clean_info_json` | Bool | `False` | Strip internal metadata from `info.json` to keep it clean. |
| `remove_ip` | Bool | `False` | Remove IP addresses from `info.json`. |
| `clean_urls` | Bool | `False` | Remove transient URLs from `info.json`. |
| **Fallback** |  |  |  |
| `include_dash` | Bool | `False` | Allow DASH formats as a fallback. |
| `include_m3u8` | Bool | `False` | Allow HLS/m3u8 formats as a fallback. |

### 6. Unarchived Specific Options

These apply to the "Unarchived" recorder module.
> **Warning:** Unarchived recovery is currently unreliable. For streams that are important to you, download them live.
> **Warning:** Cleanup for this function is not perfect. It is recommended to remove files in `unarchived_tempdir` older than 24 hours via an external cron job.

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `unarchived_tempdir` | String | *Same as temp* | Temp folder specifically for unarchived streams. |
| `unarchived_download_chat` | Bool | `False` | Download live chat in real-time while the stream is live. |
| `unarchived_force_merge` | Bool | `False` | Force FFmpeg merge even if segments are missing. |

### 7. Community Tab Options

Downloads community posts (text and images).

#### Channel Config

```toml
[community_tab]
"Saba" = "UCxsZ6NCzjU_t4YSxQLBcM5A"

```

#### Community Tab Options
```toml
[community_tab_options]
community_dir = "/app/CommunityPosts"        # Output directory
archive_file = "/app/com-tab-archive.txt"    # File tracking downloaded post IDs

```

### 8. Logging Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `log_level` | String | `INFO` | Level (DEBUG, INFO, WARNING, ERROR). |
| `log_file` | String | *None* | Path to write logs to. |
| `log_file_max_size` | Int | *None* | Max size in bytes before log rotation. |
| `log_file_keep_backup` | Int | *None* | Number of log backups to keep. |

### 9. Torrent & Web UI Options

#### Torrent Creation (currently disabled)
Automatically create torrents after download.

```toml
[torrent_options]
enabled = false
torrentOptions = []              # List of args passed to py3createtorrent

```

#### Web UI Theme

```toml
[webui]
theme = "dark"                   # "light" or "dark"

```