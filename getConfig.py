from sys import argv
from pathlib import Path, PurePath
import os
import tomlkit

class ConfigHandler:
    def __init__(self, config=None, config_file="config.toml"):
        # If no dict provided, load via tomlkit
        if config is None and config_file:
            #config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), config_file)
            with open(config_file, "rt", encoding="utf-8") as toml_file:
                config_doc = tomlkit.load(toml_file)
            # Convert tomlkit document to plain dict for easier usage
            config = config_doc.unwrap()  # or access config_doc[...] directly if you prefer
        self.channel_ids_to_match: dict = config.get("channel_ids_to_match", {})
        self.unarchived_channel_ids_to_match: dict = config.get("unarchived_channel_ids_to_match", {})
        self.community_tab_channels: dict = config.get("community_tab", {})
        
        self.title_filter: dict = config.get("title_filter", {})   
        self.description_filter: dict = config.get("description_filter", {})
        self.members_only: dict = config.get("members_only", {})
        self.community_tab: dict = config.get("community_tab", {})
        self.webhook: dict = config.get("webhook", {})

        self.cron_schedule = config.get("cron_schedule", {})
        
        self.download_options: dict = config.get("download_options", {})
        self.torrent_options: dict = config.get("torrent_options", {})
        self.community_tab_options: dict = config.get("community_tab_options", {})
        
        # If you want to keep the TOML document for writes:
        self._config_doc = config_doc if 'config_doc' in locals() else None

    def save(self, config_file="config.toml"):
        """
        If the config was loaded via tomlkit and you modified it,
        you can write it back (preserving comments/format) using tomlkit.
        Requires that self._config_doc is not None.
        """
        if self._config_doc is None:
                raise RuntimeError("No TOML document loaded — cannot save")

        # Write file with preserved formatting + comments
        with open(config_file, "wt", encoding="utf-8") as f:
            tomlkit.dump(self._config_doc, f)
        
        

    def get_cookies_file(self):
        if self.download_options.get("cookies_file", None) is not None:
            return str(Path(self.download_options.get("cookies_file")))
        else:
            return None
       
    def get_title_filter(self):
        return self.title_filter
    
    def get_desc_filter(self):
        return self.description_filter

    def get_ytdlp(self):
        output_folder = self.download_options.get('output_path', "")
        if not output_folder:
            output_folder = "%(fulltitle)s"

        if len(Path(output_folder).parents) <= 1:
            output_folder = str(PurePath(output_folder, output_folder))

        return str(Path(output_folder))

    def vid_only(self):
        return self.download_options.get("video_only", False)

    def get_chat(self):
        if not self.download_options.get("video_only", False) and self.download_options.get("download_chat", False):
            return True
        else:
            return False

    def get_thumbnail(self):
        return not self.download_options.get("video_only", False) and self.download_options.get("thumbnail", False)

    def get_description(self):
        return not self.download_options.get("video_only", False) and self.download_options.get("description", False)

    def get_info_json(self):
        return not self.download_options.get("video_only", False) and self.download_options.get("info_json", False)

    def get_info(self):
        out = []
        if self.vid_only():
            return out

        if self.get_thumbnail():
            out += ["--write-thumbnail", "--convert-thumbnails", "png"]
        if self.get_info_json():
            out += ["--write-info-json"]
        if self.get_description():
            out += ["--write-description"]

        return out

    def get_mux(self):
        return self.download_options.get("mux_file", True)

    def get_download_threads(self):
        return self.download_options.get("download_threads", 4)

    def get_ytarchive_options(self):
        try:
            ytarchive_options = self.config.ytarchive_options
        except AttributeError:
            ytarchive_options = ""

        options = ytarchive_options.split(' ')
        options += ["--threads", str(self.get_download_threads())]

        if self.get_thumbnail():
            options += ["-t"]
        if not self.get_mux():
            options += ["--write-mux-file"]

        return options

    def get_quality(self):
        return self.download_options.get("video_quality", "best")

    def get_temp_folder(self):
        return self.download_options.get('temp_dir', "/app/temp/")

    def get_done_folder(self):
        return self.download_options.get('done_dir', "/app/Done/")

    def get_temp_output_path(self, output):
        return Path(self.get_temp_folder()) / Path(output)

    def get_done_output_path(self, output):
        return Path(self.get_done_folder()) / Path(output)
    
    def get_direct_to_ts(self):
        return self.download_options.get("direct_to_ts", False)

    def ytarchive_builder(self, video_id, output):
        out = ["ytarchive", "--error"]
        out += self.get_ytarchive_options()

        cookies = self.get_cookies_file()
        if cookies:
            out += ["--cookies", cookies]

        output_folder = self.get_temp_output_path(output)
        out += ["--output", str(output_folder)]
        out.append(f"https://www.youtube.com/watch?v={video_id}")
        out.append(self.get_quality())
        return out

    def get_torrent(self):
        return self.torrent_options.get('enabled', False)

    def torrent_builder(self, output, folder):
        options = ['py3createtorrent']
        try:
            options += self.config.torrentOptions
        except AttributeError:
            pass
        options += ['-o', f"{output}.torrent"]
        options += [str(folder)]
        return options

    def get_look_ahead(self):
        # Return option, defaulting to 48 hours/2 days in the future
        return self.download_options.get('look_ahead', 48)
    
    def get_look_behind(self):
        # Return option, defaulting to 336 hours/2 weeks in the past
        return self.download_options.get('look_behind', 336)

    def get_membership_directory(self):
        return self.download_options.get('members_dir', None) or self.get_done_folder()

    def get_membership_output_path(self, output):
        return Path(self.get_membership_directory()) / Path(output)

    def get_community_tab_archive(self):
        return self.community_tab_options.get('archive_file', None)

    def get_community_tab_directory(self):
        return self.community_tab_options.get("community_dir", "")

    def get_unarchived_temp_folder(self):
        return self.download_options.get("unarchived_tempdir", None) or self.get_temp_folder()
    
    def get_unarchived_output_path(self, output):
        return Path(self.get_unarchived_folder()) / Path(output)

    def get_remove_ip(self):
        return self.download_options.get('remove_ip', False)
    
    def get_remove_url(self):
        return self.download_options.get('clean_urls', False)

    def get_unarchived_folder(self):
        return self.download_options.get("unarchived_dir", None) or self.get_done_folder()
    
    def get_unarchived_chat_dl(self):
        return self.download_options.get("unarchived_download_chat", False)
    
    def get_unarchived_force_merge(self):
        return self.download_options.get("unarchived_force_merge", False)
    
    def get_unarchived_recovery_failure_tolerance(self):
        return self.download_options.get("recovery_failure_tolerance", 0)

    def get_output_template_yta_raw(self):
        if self.download_options.get("output_folder", None):
            return self.download_options.get("output_folder").replace('%(fulltitle)s', '%(title)s')
        return None

    def get_fetch_method(self):
        return self.download_options.get('video_fetch_method', "ytdlp")
    
    def get_ffmpeg_command(self):
        return self.download_options.get('write_ffmpeg_command', False)
    
    def get_keep_ts_files(self):
        return self.download_options.get('keep_ts_files', False)
    
    def get_ytdlp_options(self):
        return self.download_options.get('ytdlp_options', None)
    
    def get_include_dash(self):
        return self.download_options.get('include_dash', False)
    
    def get_include_m3u8(self):
        return self.download_options.get('include_m3u8', False)
    
    def get_discord_webhook(self):
        return self.webhook.get("url", None)
    
    def get_proxy(self):
        proxies = self.download_options.get('proxies', None)
        if proxies is not None:
            from livestream_dl import runner
            proxies = runner.process_proxies(proxies)
            return proxies
        return None
    
    def get_log_level(self):
        return str(self.download_options.get('log_level', "INFO")).upper()
    
    def get_log_file(self):
        return self.download_options.get('log_file', None)
    
    def get_log_file_rotate_size(self):
        return self.download_options.get('log_file_max_size', None)
    
    def get_log_file_rotate_time(self):
        return self.download_options.get('log_file_rotate_when', None)
    
    def get_log_file_backups(self):
        return self.download_options.get('log_file_keep_backup', None)
    
    def get_log_file_options(self):
        return {
                "maxBytes": self.get_log_file_rotate_size(),
                "when": self.get_log_file_rotate_time(),
                "backupCount": self.get_log_file_backups(),
            }
    
    def randomise_lists(self):
        return self.download_options.get('randomise_lists', False)
    
    def get_remux_container(self):
        return self.download_options.get('remux_extension', None)
    
    def get_cron_schedule(self, timer=None):
        if not timer:
            return self.cron_schedule
        else:
            return self.cron_schedule.get(timer, None)
    
    def get_livestream_dl_options(self, info_dict, output_template):
        options = {
            "ID": info_dict.get('id'),
            "resolution": self.get_quality(),
            "video_format": None,
            "audio_format": None,
            "threads": self.get_download_threads(),
            "batch_size": 5,
            "segment_retries": 10,
            "merge": self.get_mux(),
            "cookies": self.get_cookies_file(),
            #"output": self.get_done_output_path(),
            "temp_folder": str(self.get_temp_folder()),
            "write_thumbnail": self.get_thumbnail(),
            "embed_thumbnail": self.get_thumbnail(),
            "write_info_json": self.get_info_json(),
            "write_description": self.get_description(),
            "keep_temp_files": False,
            "keep_ts_files": self.get_keep_ts_files(),
            "live_chat": self.get_chat(),
            "keep_database_file": False,
            "recovery": False,
            "database_in_memory": False,
            "direct_to_ts": self.get_direct_to_ts(),
            "wait_for_video": None,
            "json_file": None,
            "remove_ip_from_json": self.get_remove_ip(),
            "clean_urls": self.get_remove_url(),
            "log_level": self.get_log_level(),
            "log_file": self.get_log_file(),
            'write_ffmpeg_command': self.get_ffmpeg_command(),
            "proxy": self.get_proxy(),
            "log_file_options": self.get_log_file_options(),
            "dash": self.get_include_dash(),
            "m3u8": self.get_include_m3u8(),
        }

        if self.get_remux_container() is not None:
            options.update({'ext': str(self.get_remux_container())})

        if info_dict.get('availability', None) == 'subscriber_only':
            options.update({'output': str(self.get_membership_output_path(output_template))})
        else:
            options.update({'output': str(self.get_done_output_path(output_template))})

        if self.get_ytdlp_options() is not None:
            import json
            options.update({'ytdlp_options': json.loads(self.get_ytdlp_options())})

            
        return options

if __name__ == "__main__":
    handler = ConfigHandler()
    function = argv[1]
    match function:
        case "cookies":
            print(handler.get_cookies_file())
        case "yt-dlp_options":
            print(handler.get_ytdlp())
        case "info_options":
            print(handler.get_info())
        case "get_chat":
            print(handler.get_chat())
        case "ytarchive_options":
            print(handler.get_ytarchive_options())
        case "mux_file":
            print(handler.get_mux())
        case "quality":
            print(handler.get_quality())
