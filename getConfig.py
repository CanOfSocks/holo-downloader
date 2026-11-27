"""
Access to the holo-downloader configuration (config.toml).

A new ConfigHandler is created for each scheduled check and each download, so edits made to the
config file (for example through the web UI) apply to the next check without a restart.
"""
import json
import logging
import os
from pathlib import Path, PurePath
from typing import Any, Optional, Union

import tomlkit

from livestream_dl.YoutubeURL import quality_aliases

DEFAULT_CONFIG_FILE = "config.toml"

# Lower bound for any "wait before checking again" value, to stop a typo in the config from
# turning into a tight yt-dlp extraction loop.
MINIMUM_RECHECK_SECONDS = 60


def default_config_path() -> str:
    """Config path from the CONFIG_FILE environment variable (set by Docker or `web.py --config`)."""
    return os.environ.get("CONFIG_FILE", DEFAULT_CONFIG_FILE)


def load_or_create_config(path: str) -> "ConfigHandler":
    """Load the config, first creating a minimal one (a stream check every 30 minutes) if the file doesn't exist."""
    if not os.path.exists(path):
        doc = tomlkit.document()
        schedule = tomlkit.table()
        schedule.add(tomlkit.comment("Format: Minute Hour Day_of_Month Month Day_of_Week (standard 5 fields)"))
        schedule.add("streams", "*/30 * * * *")
        doc.add("cron_schedule", schedule)
        with open(path, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))
    return ConfigHandler(config_file=path)


def save_config_text(path: str, content: str) -> tuple[bool, str]:
    """
    Save config text from the web UI if it is valid TOML. Returns (saved, message).
    Written to a temporary file and then swapped in, so a reader (the stall check, a new download)
    never sees a half-written file and a crash can't leave a truncated config.
    It is written in place instead only when the temporary file can't be created (read-only
    folder) or can't be swapped in (a config mounted into Docker as a single file: EBUSY/EXDEV).
    If writing the temporary file fails (e.g. disk full), config.toml is left untouched.
    """
    try:
        tomlkit.parse(content)
    except Exception as e:
        return False, f"Invalid TOML, configuration not saved: {e}"
    temp_path = path + ".tmp"
    try:
        if not _replace_with_temp_file(path, temp_path, content):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
    except OSError as e:
        return False, f"Unable to save the configuration: {e}"
    return True, "Config saved"


def _replace_with_temp_file(path: str, temp_path: str, content: str) -> bool:
    """
    Write `content` to temp_path and swap it in. Returns False if the temporary file can't be
    created or swapped in (the caller then writes in place). A failed write raises OSError.
    """
    try:
        temp_file = open(temp_path, "w", encoding="utf-8", newline="")
    except OSError:
        return False
    try:
        with temp_file:
            temp_file.write(content)
        try:
            os.replace(temp_path, path)
        except OSError:
            return False
        return True
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# Cookies paths already reported as unusable, so the warning is logged once per path
_unusable_cookies_logged: set[str] = set()


def usable_cookies_path(value: Optional[str]) -> Optional[str]:
    """
    The configured cookies file, or None if none is set or the path exists but isn't a file.
    Docker creates a directory when a bind-mounted file (e.g. cookies.txt in compose.yml) doesn't
    exist on the host; yt-dlp would fail every extraction trying to load it. A path that doesn't
    exist at all is passed on unchanged (yt-dlp then simply has no cookies).
    """
    if value is None:
        return None
    path = str(Path(value))
    if os.path.exists(path) and not os.path.isfile(path):
        if path not in _unusable_cookies_logged:
            _unusable_cookies_logged.add(path)
            logging.getLogger("holo.config").warning(
                "Cookies file {0} is not a file (a directory?), continuing without cookies. If it is mounted "
                "into Docker, create the file on the host first, e.g. `touch cookies.txt`.".format(path))
        return None
    return path


def apply_quality_alias(options: dict) -> dict:
    """Replace a quality alias (e.g. "1080p") in options["resolution"] with its yt-dlp format and sort."""
    alias: Optional[dict] = quality_aliases.get(options.get("resolution"))
    if alias:
        options["resolution"] = alias.get("format")
        if alias.get("sort"):
            options["custom_sort"] = alias.get("sort")
    return options


class ConfigHandler:
    def __init__(self, config: Optional[dict] = None, config_file: Optional[str] = None):
        # If no dict is provided, load the TOML file. tomlkit's unwrap() converts the document to plain dicts.
        if config is None:
            with open(config_file or default_config_path(), "rt", encoding="utf-8") as toml_file:
                config = tomlkit.load(toml_file).unwrap()

        self.channel_ids_to_match: dict[str, str] = config.get("channel_ids_to_match", {})
        self.unarchived_channel_ids_to_match: dict[str, str] = config.get("unarchived_channel_ids_to_match", {})
        self.members_only: dict[str, str] = config.get("members_only", {})
        self.community_tab: dict[str, str] = config.get("community_tab", {})

        self.title_filter: dict[str, str] = config.get("title_filter", {})
        self.description_filter: dict[str, str] = config.get("description_filter", {})
        self.per_channel_output_template: dict[str, str] = config.get("per_channel_output_template", {})
        self.webhook: dict = config.get("webhook", {})
        self.cron_schedule: dict[str, str] = config.get("cron_schedule", {})

        self.download_options: dict = config.get("download_options", {})
        self.torrent_options: dict = config.get("torrent_options", {})
        self.community_tab_options: dict = config.get("community_tab_options", {})
        self.webui_options: dict = config.get("webui", {})

    # --- Cookies ---

    def get_cookies_file(self) -> Optional[str]:
        return usable_cookies_path(self.download_options.get("cookies_file"))

    def get_community_tab_cookies(self) -> Optional[str]:
        if self.community_tab_options.get("cookies_file") is not None:
            return usable_cookies_path(self.community_tab_options.get("cookies_file"))
        return self.get_cookies_file()

    # --- Filters ---

    def get_title_filter(self) -> dict[str, str]:
        return self.title_filter

    def get_desc_filter(self) -> dict[str, str]:
        return self.description_filter

    # --- Output paths ---

    def get_channel_output_template(self, channel_id: Optional[str]) -> Optional[str]:
        if not channel_id:
            return None
        return self.per_channel_output_template.get(channel_id)

    def get_output_template(self, channel_id: Optional[str] = None) -> str:
        """yt-dlp output template (per channel template first, then the global output_path)."""
        output_folder = self.get_channel_output_template(channel_id=channel_id) or self.download_options.get('output_path', "")
        if not output_folder:
            output_folder = "%(fulltitle)s"

        # The template must have a depth of at least 2 so each video has its own folder,
        # which is moved from the temp folder to the done folder as a whole.
        if len(Path(output_folder).parents) <= 1:
            output_folder = str(PurePath(output_folder, output_folder))
        return str(Path(output_folder))

    def get_temp_folder(self) -> str:
        return self.download_options.get('temp_dir', "/app/temp/")

    def get_done_folder(self) -> str:
        return self.download_options.get('done_dir', "/app/Done/")

    def get_temp_output_path(self, output: str) -> Path:
        return Path(self.get_temp_folder()) / Path(output)

    def get_done_output_path(self, output: str) -> Path:
        return Path(self.get_done_folder()) / Path(output)

    def get_membership_directory(self) -> str:
        return self.download_options.get('members_dir', None) or self.get_done_folder()

    def get_membership_output_path(self, output: str) -> Path:
        return Path(self.get_membership_directory()) / Path(output)

    def get_unarchived_temp_folder(self) -> str:
        return self.download_options.get("unarchived_tempdir", None) or self.get_temp_folder()

    def get_unarchived_folder(self) -> str:
        return self.download_options.get("unarchived_dir", None) or self.get_done_folder()

    def get_unarchived_output_path(self, output: str) -> Path:
        return Path(self.get_unarchived_folder()) / Path(output)

    # --- What to download ---

    def vid_only(self) -> bool:
        return self.download_options.get("video_only", False)

    def get_chat(self) -> bool:
        return not self.vid_only() and self.download_options.get("download_chat", False)

    def get_thumbnail(self) -> bool:
        return not self.vid_only() and self.download_options.get("thumbnail", False)

    def get_description(self) -> bool:
        return not self.vid_only() and self.download_options.get("description", False)

    def get_info_json(self) -> bool:
        return not self.vid_only() and self.download_options.get("info_json", False)

    def get_clean_info_json(self) -> bool:
        return self.download_options.get("clean_info_json", False)

    def get_mux(self) -> bool:
        return self.download_options.get("mux_file", True)

    def get_download_threads(self) -> int:
        return self.download_options.get("download_threads", 4)

    def get_quality(self) -> str:
        return self.download_options.get("video_quality", "bv+ba/best")

    def get_direct_to_ts(self) -> bool:
        return self.download_options.get("direct_to_ts", False)

    def get_keep_ts_files(self) -> bool:
        return self.download_options.get('keep_ts_files', False)

    def get_remux_container(self) -> Optional[str]:
        return self.download_options.get('remux_extension', None)

    def get_ffmpeg_command(self) -> bool:
        return self.download_options.get('write_ffmpeg_command', False)

    def get_remove_ip(self) -> bool:
        return self.download_options.get('remove_ip', False)

    def get_remove_url(self) -> bool:
        return self.download_options.get('clean_urls', False)

    def get_include_dash(self) -> bool:
        return self.download_options.get('include_dash', False)

    def get_include_m3u8(self) -> bool:
        return self.download_options.get('include_m3u8', False)

    # --- Discovery ---

    def get_fetch_method(self) -> str:
        return self.download_options.get('video_fetch_method', "ytdlp")

    def get_playlist_limit(self) -> int:
        return self.download_options.get("yt_dlp_playlist_limit", 20)

    def get_look_ahead(self) -> float:
        # Hours into the future, defaulting to 48 hours (2 days)
        return self.download_options.get('look_ahead', 48)

    def get_look_behind(self) -> float:
        # Hours into the past, defaulting to 336 hours (2 weeks)
        return self.download_options.get('look_behind', 336)

    def randomise_lists(self) -> bool:
        return self.download_options.get('randomise_lists', False)

    # --- Waiting, stalls and limits ---

    def get_new_segment_wait_limit(self) -> int:
        """
        Number of ~10s livestream_dl wait cycles without new segments before the download ends.
        0 waits until livestream_dl detects the end of the stream.
        `new_segment_wait_limit` is the documented key. `segment_wait_limit` is an older key that
        previously silently overrode it (both were written to the same dict key), so it is still
        honoured as a fallback.
        """
        value = self.download_options.get('new_segment_wait_limit')
        if value is None:
            value = self.download_options.get('segment_wait_limit', 0)
        return int(value or 0)

    def upcoming_video_max_wait(self) -> float:
        """Maximum seconds between checks of a stream that is past its scheduled start but not live yet."""
        return max(float(self.download_options.get('upcoming_wait_for_video_max', 900)), MINIMUM_RECHECK_SECONDS)

    def get_upcoming_recheck_max(self) -> float:
        """
        Maximum seconds between full extractions of a stream that is waiting for its scheduled
        start. This applies regardless of what the channel scan reports, so a moved schedule is
        noticed even if the video drops out of the scanned part of the playlist.
        """
        return max(float(self.download_options.get('upcoming_recheck_max', 10800)), MINIMUM_RECHECK_SECONDS)

    def get_stall_timeout(self) -> float:
        """Seconds without a new downloaded segment (while recording) before a download is marked stalled."""
        return max(float(self.download_options.get('stall_timeout', 900)), MINIMUM_RECHECK_SECONDS)

    def get_stall_auto_merge(self) -> float:
        """Seconds a download stays stalled before it is automatically stopped and merged. 0 disables it."""
        return max(float(self.download_options.get('stall_auto_merge', 0) or 0), 0.0)

    def get_stop_chat_when_done(self) -> int:
        """
        Seconds to keep downloading the live chat after the stream's media has finished, before it is
        stopped and the chat downloaded so far is kept. 0 waits until the chat ends on its own.
        """
        return max(int(self.download_options.get('stop_chat_when_done', 900) or 0), 0)

    # --- Unarchived ---

    def get_unarchived_chat_dl(self) -> bool:
        return self.download_options.get("unarchived_download_chat", False)

    def get_unarchived_force_merge(self) -> bool:
        return self.download_options.get("unarchived_force_merge", False)

    def get_unarchived_recovery_failure_tolerance(self) -> int:
        return self.download_options.get("recovery_failure_tolerance", 0)

    # --- Network / yt-dlp ---

    def get_ytdlp_options(self) -> Optional[dict]:
        """`ytdlp_options` is stored as a JSON string in the config. Invalid JSON is logged and ignored."""
        raw = self.download_options.get('ytdlp_options', None)
        if not raw:
            return None
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logging.getLogger().exception("Invalid JSON in download_options.ytdlp_options, ignoring it")
            return None

    def get_proxy(self) -> Optional[Union[str, dict]]:
        proxies = self.download_options.get('proxies', None)
        if proxies is not None:
            from livestream_dl import runner
            return runner.process_proxies(proxies)
        return None

    # --- Logging ---

    def get_log_level(self) -> str:
        return str(self.download_options.get('log_level', "INFO")).upper()

    def get_log_file(self) -> Optional[str]:
        return self.download_options.get('log_file', None)

    def get_log_file_options(self) -> dict[str, Any]:
        return {
            "maxBytes": self.download_options.get('log_file_max_size', None),
            "when": self.download_options.get('log_file_rotate_when', None),
            "backupCount": self.download_options.get('log_file_keep_backup', None),
        }

    # --- Other sections ---

    def get_discord_webhook(self) -> Optional[str]:
        return self.webhook.get("url", None)

    def get_cron_schedule(self, timer: Optional[str] = None) -> Union[dict, str, None]:
        if not timer:
            return self.cron_schedule
        return self.cron_schedule.get(timer, None)

    def get_webui_theme(self) -> str:
        return self.webui_options.get("theme", "dark")

    def get_community_tab_archive(self) -> Optional[str]:
        return self.community_tab_options.get('archive_file', None)

    def get_community_tab_directory(self) -> str:
        return self.community_tab_options.get("community_dir", "")

    # Torrent creation is currently disabled (see README); kept for when it is re-enabled.
    def get_torrent(self) -> bool:
        return self.torrent_options.get('enabled', False)

    def torrent_builder(self, output: str, folder: Union[str, Path]) -> list[str]:
        return ['py3createtorrent', *self.torrent_options.get('torrentOptions', []), '-o', f"{output}.torrent", str(folder)]

    # --- livestream_dl options ---

    def get_livestream_dl_options(self, info_dict: dict, output_template: str) -> dict:
        """Options passed to LiveStreamDownloader.download_segments for a normal (live) download."""
        options = {
            "ID": info_dict.get('id'),
            "resolution": self.get_quality(),
            "threads": self.get_download_threads(),
            "batch_size": 5,
            "segment_retries": 10,
            "merge": self.get_mux(),
            "cookies": self.get_cookies_file(),
            "temp_folder": str(self.get_temp_folder()),
            "write_thumbnail": self.get_thumbnail(),
            "embed_thumbnail": self.get_thumbnail(),
            "write_info_json": self.get_info_json(),
            "write_description": self.get_description(),
            "keep_temp_files": False,
            "keep_ts_files": self.get_keep_ts_files(),
            "live_chat": self.get_chat(),
            "stop_chat_when_done": self.get_stop_chat_when_done(),
            "keep_database_file": False,
            "recovery": False,
            "database_in_memory": False,
            "direct_to_ts": self.get_direct_to_ts(),
            "wait_limit": self.get_new_segment_wait_limit(),
            "json_file": None,
            "remove_ip_from_json": self.get_remove_ip(),
            "clean_urls": self.get_remove_url(),
            "log_level": self.get_log_level(),
            "log_file": self.get_log_file(),
            "write_ffmpeg_command": self.get_ffmpeg_command(),
            "proxy": self.get_proxy(),
            "log_file_options": self.get_log_file_options(),
            "dash": self.get_include_dash(),
            "m3u8": self.get_include_m3u8(),
        }
        apply_quality_alias(options)

        if self.get_remux_container() is not None:
            options['ext'] = str(self.get_remux_container())

        if info_dict.get('availability', None) == 'subscriber_only':
            options['output'] = str(self.get_membership_output_path(output_template))
        else:
            options['output'] = str(self.get_done_output_path(output_template))

        ytdlp_options = self.get_ytdlp_options()
        if ytdlp_options is not None:
            options['ytdlp_options'] = ytdlp_options
        return options

    def get_unarchived_livestream_dl_options(self, info_dict: dict) -> dict:
        """Options for recovering an unarchived (privated) stream from a saved info.json."""
        options = {
            "ID": info_dict.get('id', ""),
            "resolution": self.get_quality() or "bv+ba/best",
            "video_format": None,
            "audio_format": None,
            # Recovery is time limited by URL expiry, so it uses more threads than live downloads
            "threads": 20,
            "batch_size": 5,
            "segment_retries": 10,
            "merge": self.get_mux(),
            "output": str(self.get_unarchived_output_path(self.get_output_template())),
            "temp_folder": self.get_unarchived_temp_folder(),
            "write_thumbnail": self.get_thumbnail(),
            "embed_thumbnail": self.get_thumbnail(),
            "write_info_json": self.get_info_json(),
            "write_description": self.get_description(),
            "keep_database_file": False,
            "recovery": True,
            "force_recover_merge": self.get_unarchived_force_merge(),
            "recovery_failure_tolerance": self.get_unarchived_recovery_failure_tolerance(),
            "database_in_memory": False,
            "direct_to_ts": False,
            "wait_for_video": None,
            "json_file": None,
            "remove_ip_from_json": self.get_remove_ip(),
            "log_level": self.get_log_level(),
            "log_file": self.get_log_file(),
            "write_ffmpeg_command": self.get_ffmpeg_command(),
            "proxy": self.get_proxy(),
        }
        return apply_quality_alias(options)
