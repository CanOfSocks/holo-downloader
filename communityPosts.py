"""
Community tab archiving with ytct from the youtube-community-tab dev branch
(https://github.com/CanOfSocks/youtube-community-tab/tree/dev), run in this process.

The Dockerfile installs the youtube_community_tab library and puts ytct.py next to this file.
Everything ytct logs goes to the holo-downloader log (logger holo.community_posts).
"""
import logging
import os
import re
import time
from typing import Callable, Optional

import common
from common import initialize_logging, kill_all
from getConfig import ConfigHandler

# A single channel should never take this long; without a limit a stuck download would block the
# community tab job forever (the scheduler never starts a second copy of the same job).
CHANNEL_TIMEOUT_SECONDS = 3600
# The youtube_community_tab library sends its requests without a timeout, so one that never
# answers would hang the job. ytct already sets its own timeout for image downloads.
REQUEST_TIMEOUT_SECONDS = 60

# ytct logs plain text; these words mark the lines that report a problem
_PROBLEM_PATTERN = re.compile(r"fail|could not|cannot|warning|not saving|yt-dlp:|no youtube cookies", re.IGNORECASE)
# Setup and summary lines (cookies, archive, "found N posts"); per-post and per-page lines are DEBUG
_SUMMARY_COMPONENTS = ("ytct", "cookies")


class StopDownload(BaseException):
    """
    Raised from ytct's log callback to stop a channel on shutdown or timeout. A BaseException,
    so ytct's ignore_errors handling (which catches Exception) can't swallow it.
    """


def log_level_for(component: str, message: str) -> int:
    """The holo log level for a line ytct logged."""
    if _PROBLEM_PATTERN.search(message):
        return logging.WARNING
    if component in _SUMMARY_COMPONENTS or message.startswith("found "):
        return logging.INFO
    return logging.DEBUG


def make_ytct_log(logger: logging.Logger, channel: str, deadline: float) -> Callable:
    """
    The log callback given to ytct: writes each line to the holo log. ytct logs before every page,
    post and image, so this is also where a shutdown or the channel timeout stops the download.
    """
    stopped = False

    def log(component: str, message: str, end: str = "\n") -> None:
        nonlocal stopped
        logger.log(log_level_for(component, message), "Community tab {0}: [{1}] {2}".format(channel, component, message))
        # Once stopped, ytct's cleanup (saving the cookies) may still log without being stopped again
        if not stopped and (kill_all.is_set() or time.monotonic() > deadline):
            stopped = True
            raise StopDownload("shutdown" if kill_all.is_set() else "timed out")

    return log


def add_request_timeout(session, timeout: float) -> None:
    """Give every request of the library's shared HTTP session a default timeout (once)."""
    if getattr(session, "_holo_request_timeout", None) is not None:
        session._holo_request_timeout = timeout
        return
    original_request = session.request

    def request(method, url, *args, **kwargs):
        if len(args) < 8:  # timeout is the 9th positional argument of requests.Session.request
            kwargs.setdefault("timeout", session._holo_request_timeout)
        return original_request(method, url, *args, **kwargs)

    session._holo_request_timeout = timeout
    session.request = request


def download_channel(ytct, logger: logging.Logger, channel: str, channel_id: str, directory: str,
                     cookies: Optional[str], archive: Optional[str]) -> None:
    """Download one channel's posts. Problems are logged; nothing is raised except on shutdown."""
    log = make_ytct_log(logger, channel, time.monotonic() + CHANNEL_TIMEOUT_SECONDS)
    url = f"https://www.youtube.com/channel/{channel_id}/posts"
    logger.debug("Community tab {0}: downloading {1} to {2}".format(channel, url, directory))
    try:
        # ignore_errors: a post that fails is left out of the archive so the next run retries it,
        # instead of stopping the whole channel
        with ytct.Downloader(log=log, directory=directory, cookies=cookies, post_archive=archive,
                             dates=True, reverse=True, ignore_errors=True) as downloader:
            result = downloader.download_all([url])
    except StopDownload as e:
        logger.warning("Stopping community tab download of {0} ({1})".format(channel, e))
        return
    except Exception as e:
        logger.error("Community tab download of {0} failed: {1}: {2}".format(channel, type(e).__name__, e))
        return
    if result.failed:
        logger.warning("Community tab {0}: {1} new, {2} failed (retried on the next run): {3}".format(
            channel, len(result.downloaded), len(result.failed), "; ".join(str(f) for f in result.failed)))
    else:
        logger.info("Community tab {0}: {1} new posts".format(channel, len(result.downloaded)))


def main(config: Optional[ConfigHandler] = None, logger: Optional[logging.Logger] = None) -> None:
    config = config or ConfigHandler()
    logger = logger or initialize_logging(config, "community_posts")

    com_tab_folder = config.get_community_tab_directory()
    if not com_tab_folder:
        return
    community_tab: dict[str, str] = config.community_tab
    if not community_tab:
        return
    try:
        # Imported here so the rest of the app still runs where ytct isn't installed
        import ytct
        from youtube_community_tab.requests_handler import requests_cache
    except ImportError as e:
        logger.error("Community tab download needs ytct.py and the youtube_community_tab library "
                     "(installed by the Dockerfile): {0}".format(e))
        return
    add_request_timeout(requests_cache, REQUEST_TIMEOUT_SECONDS)

    if config.randomise_lists():
        community_tab = common.random_sample(community_tab)
    cookies = config.get_community_tab_cookies()
    archive = config.get_community_tab_archive()
    for channel, channel_id in community_tab.items():
        if kill_all.is_set():
            break
        download_channel(ytct, logger, channel, channel_id, os.path.join(com_tab_folder, channel), cookies, archive)
