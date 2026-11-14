"""Download of a live (or upcoming) stream with livestream_dl."""
import json
import logging
import threading
from typing import Any, Dict, Optional, Union

import discord_web
from base_downloader import (STATUS_BEYOND_LOOK_AHEAD, STATUS_CANCELLED, STATUS_ERROR, STATUS_FINISHED,
                             STATUS_STOPPED_MERGED, BaseDownloader)
from common import DiscoveredVideo
from getConfig import ConfigHandler


class VideoDownloader(BaseDownloader):
    kind = "stream"

    def __init__(self, video: Union[DiscoveredVideo, str], config: Optional[ConfigHandler] = None,
                 logger: Optional[logging.Logger] = None, kill_this: Optional[threading.Event] = None):
        super().__init__(video, config=config, logger=logger, kill_this=kill_this, logger_name="Downloader")
        self.outputFile: Optional[str] = None

    def notify(self, status: str, message: Optional[str] = None) -> None:
        discord_web.notify(self.id, status, message=message, config=self.config, embed_info=self.embed_info or None)

    def prepare(self, info_dict: Dict[str, Any]) -> None:
        """Work out the output file name and keep a small copy of the info for the web UI."""
        # Same yt-dlp templating as yt-dlp's prepare_filename; "%" is replaced by livestream_dl
        self.outputFile = self.livestream_downloader.output_filename(info_dict, outtmpl=self.config.get_ytdlp(self.channel_id))
        self.logger.info("Output file: {0}".format(self.outputFile))
        if self.logger.isEnabledFor(logging.DEBUG):
            # Only serialise the (large) info dict when debug logging is on
            self.logger.debug("Info.json: {0}".format(json.dumps(info_dict)))
        self.info_dict = {key: info_dict.get(key) for key in ('id', 'title', 'fulltitle', 'uploader', 'thumbnail', 'webpage_url')}

    def download(self, info_dict: Dict[str, Any]) -> None:
        """Download the stream segments, then mux and move the files (all done by livestream_dl)."""
        options = self.config.get_livestream_dl_options(info_dict=info_dict, output_template=self.outputFile)
        self.notify("recording")
        self.set_status("Recording")
        self.livestream_downloader.download_segments(info_dict=info_dict, resolution=options.get("resolution"), options=options)

        if self.kill_this.is_set():
            self.set_status(STATUS_CANCELLED)
            return
        self.set_status(STATUS_STOPPED_MERGED if self.merge_requested else STATUS_FINISHED)
        self.notify("done")

    def main(self) -> None:
        self.load_embed_info()
        self.notify("waiting")
        try:
            info_dict = self.wait_for_stream(include_dash=self.config.get_include_dash(), include_m3u8=self.config.get_include_m3u8())
            if info_dict is None:
                if self.status != STATUS_BEYOND_LOOK_AHEAD:
                    self.set_status(STATUS_CANCELLED)
                return
            self.prepare(info_dict)
            self.download(info_dict)
        except KeyboardInterrupt:
            # livestream_dl raises KeyboardInterrupt when the job is cancelled or the app shuts down
            self.set_status(STATUS_CANCELLED)
            self.logger.warning("Download of {0} was cancelled".format(self.id))
        except Exception as e:
            self.set_status(STATUS_ERROR)
            self.logger.exception("Error downloading video")
            self.notify("error", message=f"{type(e).__name__}: {e}"[-500:])
