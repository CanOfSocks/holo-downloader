"""
Community tab archiving with ytct from the youtube-community-tab dev branch
(https://github.com/CanOfSocks/youtube-community-tab/tree/dev), run in this process.

The Dockerfile installs the youtube_community_tab library and puts ytct.py next to this file.
Everything ytct logs goes to the holo-downloader log (logger holo.community_posts), and so do the
library's own print() messages.
"""
import builtins
import errno
import logging
import os
import re
import shutil
import sys
import threading
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
# Lines that match _PROBLEM_PATTERN but are routine: a post linking a private or deleted video
# (its thumbnail is still saved)
_ROUTINE_PATTERN = re.compile(r"could not get video ID", re.IGNORECASE)
# Setup and summary lines (cookies, archive, "found N posts"); per-post and per-page lines are DEBUG
_SUMMARY_COMPONENTS = ("ytct", "cookies")


class StopDownload(BaseException):
    """
    Raised from ytct's log callback to stop a channel on shutdown or timeout. A BaseException,
    so ytct's ignore_errors handling (which catches Exception) can't swallow it.
    """


def log_level_for(component: str, message: str) -> int:
    """The holo log level for a line ytct logged."""
    if _ROUTINE_PATTERN.search(message):
        return logging.INFO
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


# The channel being downloaded on this thread, for the library's print() messages: (logger, channel)
_current = threading.local()


def _library_print(*args, sep=" ", end="\n", file=None, flush=False) -> None:
    """
    Replaces print() inside the youtube_community_tab modules. Its messages (e.g. "[Can't find
    yt_initial_data using the regex]") always come just before an error, so they are warnings.
    """
    target = getattr(_current, "target", None)
    if target is None or file not in (None, sys.stdout, sys.stderr):
        builtins.print(*args, sep=sep, end=end, file=file, flush=flush)
        return
    logger, channel = target
    logger.warning("Community tab {0}: [library] {1}".format(channel, sep.join(str(arg) for arg in args)))


def route_library_prints() -> None:
    """Send print() of every loaded youtube_community_tab module to _library_print."""
    for name, module in list(sys.modules.items()):
        if module is not None and (name == "youtube_community_tab" or name.startswith("youtube_community_tab.")):
            module.print = _library_print


def save_cookie_file_in_place(original: Callable) -> Callable:
    """
    Wrap the library's save_cookie_file, which writes <path>.tmp and renames it over the cookies
    file. A file bind-mounted into Docker (cookies.txt in compose.yml) can't be replaced by a rename
    (EBUSY), so then the new cookies are copied into the existing file instead. The library has
    already copied the old file to <path>.bak by then.
    """
    def save(jar, path, backup=True):
        temp_path = f"{path}.tmp"
        try:
            return original(jar, path, backup=backup)
        except OSError as e:
            if not os.path.isfile(temp_path) or e.errno not in (errno.EBUSY, errno.EXDEV, errno.EPERM, errno.EACCES):
                raise
            try:
                shutil.copyfile(temp_path, path)
            finally:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            backup_path = f"{path}.bak"
            return backup_path if backup and os.path.isfile(backup_path) else None

    save._holo_in_place = True
    return save


def add_request_timeout(session, timeout: float) -> None:
    """Give every request of the library's shared HTTP session a default timeout (once)."""
    if getattr(session, "_holo_request_timeout", None) is not None:
        session._holo_request_timeout = timeout
        return
    original_request = session.request

    def request(method, url, *args, **kwargs):
        # requests.Session.request(method, url, params, data, headers, cookies, files, auth, timeout, ...):
        # timeout is args[6] when it is passed positionally
        if len(args) < 7:
            kwargs.setdefault("timeout", session._holo_request_timeout)
        return original_request(method, url, *args, **kwargs)

    session._holo_request_timeout = timeout
    session.request = request


def download_channel(ytct, logger: logging.Logger, channel: str, channel_id: str, directory: str,
                     cookies: Optional[str], archive: Optional[str], save_cookies: bool = True) -> None:
    """Download one channel's posts. Problems are logged; nothing is raised except on shutdown."""
    log = make_ytct_log(logger, channel, time.monotonic() + CHANNEL_TIMEOUT_SECONDS)
    url = f"https://www.youtube.com/channel/{channel_id}/posts"
    logger.debug("Community tab {0}: downloading {1} to {2}".format(channel, url, directory))
    _current.target = (logger, channel)
    try:
        # ignore_errors: a post that fails is left out of the archive so the next run retries it,
        # instead of stopping the whole channel
        with ytct.Downloader(log=log, directory=directory, cookies=cookies, post_archive=archive, save_cookies=save_cookies,
                             dates=True, reverse=True, ignore_errors=True) as downloader:
            result = downloader.download_all([url])
    except StopDownload as e:
        logger.warning("Stopping community tab download of {0} ({1})".format(channel, e))
        return
    except Exception as e:
        logger.error("Community tab download of {0} failed: {1}: {2}".format(channel, type(e).__name__, e))
        return
    finally:
        _current.target = None
    # Each failure was already logged as a warning when it happened
    failed = ", {0} failed (retried on the next run)".format(len(result.failed)) if result.failed else ""
    logger.info("Community tab {0}: {1} new posts{2}".format(channel, len(result.downloaded), failed))


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
    route_library_prints()
    if callable(getattr(ytct, "save_cookie_file", None)) and not getattr(ytct.save_cookie_file, "_holo_in_place", False):
        ytct.save_cookie_file = save_cookie_file_in_place(ytct.save_cookie_file)

    if config.randomise_lists():
        community_tab = common.random_sample(community_tab)
    # [community_tab_options] cookies_file, falling back to [download_options] cookies_file
    cookies = config.get_community_tab_cookies()
    logger.debug("Community tab cookies file: {0}".format(cookies or "none"))
    save_cookies = config.get_community_tab_save_cookies()
    archive = config.get_community_tab_archive()
    for channel, channel_id in community_tab.items():
        if kill_all.is_set():
            break
        download_channel(ytct, logger, channel, channel_id, os.path.join(com_tab_folder, channel), cookies, archive, save_cookies)
