"""Community tab archiving with ytct.py (downloaded into the image by the Dockerfile)."""
import logging
import os
import subprocess
import time
from typing import Optional

import common
from common import initialize_logging, kill_all
from getConfig import ConfigHandler

YTCT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ytct.py")
# A single channel should never take this long; without a limit a hung ytct.py would block the
# community tab job forever (the scheduler never starts a second copy of the same job).
CHANNEL_TIMEOUT_SECONDS = 3600


def run_until_done(command: list[str], log_file, timeout: float, logger: logging.Logger) -> Optional[int]:
    """Run a command with output to log_file. Stops it on timeout or shutdown. Returns the exit code, or None if stopped."""
    process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                return process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            if kill_all.is_set() or time.monotonic() > deadline:
                logger.warning("Stopping community tab download ({0})".format("shutdown" if kill_all.is_set() else "timed out"))
                return None
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main(config: Optional[ConfigHandler] = None, logger: Optional[logging.Logger] = None) -> None:
    config = config or ConfigHandler()
    logger = logger or initialize_logging(config, "community_posts")

    com_tab_folder = config.get_community_tab_directory()
    if not com_tab_folder:
        return
    community_tab: dict[str, str] = config.community_tab
    if config.randomise_lists():
        community_tab = common.random_sample(community_tab)

    com_tab_archive = config.get_community_tab_archive()
    for channel, channel_id in community_tab.items():
        if kill_all.is_set():
            break
        command = ["python", YTCT_SCRIPT, "--reverse", "--dates", "-d", os.path.join(com_tab_folder, channel)]
        if config.get_community_tab_cookies():
            command += ["--cookies", config.get_community_tab_cookies()]
        if com_tab_archive:
            command += ["--post-archive", com_tab_archive]
        # Passed as a list (no shell), so the URL must not be wrapped in quotes
        command.append(f"https://www.youtube.com/channel/{channel_id}/posts")
        logger.debug(f"Executing command: {' '.join(command)}")

        log_path = os.path.join(com_tab_folder, channel, "log.txt")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as log_file:
            # Output goes straight to the file instead of being held in memory
            run_until_done(command, log_file, CHANNEL_TIMEOUT_SECONDS, logger)
