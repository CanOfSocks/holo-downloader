"""
Scheduled checks: the channel scans and the community tab download (run on the cron schedules
in [cron_schedule]), and the stall check of active downloads (run every STALL_CHECK_SECONDS).
They all run on APScheduler's background thread pool.
"""
import logging
import queue
import random
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import common
import communityPosts
import discord_web
import getMembers
import getVids
import jobs
from common import DiscoveredVideo, kill_all
from getConfig import ConfigHandler, default_config_path, load_or_create_config

logger = common.initialize_logging(logger_name="checks")
scheduler = BackgroundScheduler(daemon=True)

# How often active downloads are sampled for stall detection
STALL_CHECK_SECONDS = 30
STALL_CHECK_JOB_ID = "stall-checker"

# Scheduler job ids whose routine "Running job" / "executed successfully" lines are logged at DEBUG
# instead of INFO. Add an id here to quiet another job (the cron checks use "<check name>-checker").
# The filter reads this set when each line is logged, so adding to it at runtime works too.
QUIET_JOB_IDS: set[str] = {
    STALL_CHECK_JOB_ID,
}


class _QuietJobFilter(logging.Filter):
    """
    Log APScheduler's INFO lines for the jobs in `job_ids` at DEBUG instead.
    Warnings and errors (missed runs, exceptions) keep their level.
    """
    def __init__(self, job_ids: set[str]):
        super().__init__()
        self.job_ids = job_ids   # Kept by reference so later additions apply

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != logging.INFO or not isinstance(record.args, tuple) or not record.args:
            return True
        if getattr(record.args[0], 'id', None) not in self.job_ids:
            return True
        # Handlers further up don't recheck the logger level, so drop it here unless DEBUG is enabled
        if not logging.getLogger(record.name).isEnabledFor(logging.DEBUG):
            return False
        record.levelno = logging.DEBUG
        record.levelname = logging.getLevelName(logging.DEBUG)
        return True


logging.getLogger("apscheduler.executors.default").addFilter(_QuietJobFilter(QUIET_JOB_IDS))


# --- Channel scans ---

def get_videos_with_queue(discovery_func: Callable, kind: str, **kwargs) -> None:
    """
    Run a channel scan in a background thread and start jobs as videos are found, rather
    than waiting for every channel to be scanned. New jobs are spread out by 5-10 seconds.
    """
    found: "queue.Queue[DiscoveredVideo]" = queue.Queue()
    active_ids = {job.video_id for job in jobs.snapshot_jobs(kind)}

    def scan() -> None:
        try:
            discovery_func(found, active_ids=active_ids, **kwargs)
        except Exception:
            logger.exception("Channel scan failed")

    scan_thread = threading.Thread(target=scan, name=f"scan-{kind}", daemon=True)
    scan_thread.start()
    while True:
        try:
            video = found.get(timeout=1)
        except queue.Empty:
            if not scan_thread.is_alive() and found.empty():
                break
            continue
        try:
            started = jobs.start_job(video, kind)
        except Exception:
            logger.exception(f"Unable to start a job for {video.id}")
            continue
        # Only new jobs are spread out (existing ones were just updated). kill_all.wait()
        # returns True if the application is shutting down.
        if started and kill_all.wait(random.uniform(5.0, 10.0)):
            break


@dataclass(frozen=True)
class Check:
    display: str
    func: Callable[[], None]


CHECKS: dict[str, Check] = {
    "streams": Check("Stream Checker", lambda: get_videos_with_queue(getVids.main, jobs.KIND_STREAM, unarchived=False)),
    "unarchived": Check("Unarchived Checker", lambda: get_videos_with_queue(getVids.main, jobs.KIND_UNARCHIVED, unarchived=True)),
    "members_only": Check("Members Only Checker", lambda: get_videos_with_queue(getMembers.main, jobs.KIND_STREAM)),
    "community_posts": Check("Community Tab Checker", lambda: communityPosts.main()),
}
# One lock per check so a manual "Run now" and the scheduled run never overlap (which would
# double the requests to YouTube)
_check_locks: dict[str, threading.Lock] = {name: threading.Lock() for name in CHECKS}


def job_id(name: str) -> str:
    """Scheduler job id of a check."""
    return f"{name}-checker"


def run_check(name: str) -> None:
    lock = _check_locks[name]
    if not lock.acquire(blocking=False):
        logger.warning(f"{CHECKS[name].display} is already running, skipping this run")
        return
    try:
        logger.info(f"Running {CHECKS[name].display}...")
        CHECKS[name].func()
    except Exception:
        logger.exception(f"{CHECKS[name].display} failed")
    finally:
        lock.release()


def is_running(name: str) -> bool:
    return _check_locks[name].locked()


def run_check_now(name: str) -> None:
    """Run a check on the scheduler's thread pool now (run_check skips it if it is already running)."""
    scheduler.add_job(run_check, trigger='date', run_date=datetime.now(timezone.utc), args=[name],
                      id=f"manual-{name}", replace_existing=True)


# --- Stall check ---

def check_stalls() -> None:
    """Sample every active download, notify about new stalls and auto stop & merge if configured."""
    try:
        config = ConfigHandler(config_file=default_config_path())
    except Exception:
        logger.exception("Unable to read config for stall check")
        return
    stall_timeout = config.get_stall_timeout()
    auto_merge = config.get_stall_auto_merge()

    for job in jobs.snapshot_jobs():
        downloader = job.downloader
        was_stalled = job.stall.stalled
        became_stalled = job.stall.update(downloader.status, downloader.stats,
                                          downloader.livestream_downloader.minimum_refresh_time, stall_timeout)
        if was_stalled and not job.stall.stalled:
            downloader.logger.info("Download is no longer stalled")
        if became_stalled:
            message = f"No new segments for {stall_timeout / 60:.0f} minutes. {job.stall.reason}."
            if auto_merge > 0:
                message += f" It will be stopped and merged automatically in {auto_merge / 60:.0f} minutes if it doesn't recover."
            downloader.logger.warning(f"Download stalled: {message}")
            discord_web.notify(job.video_id, "stalled", message=message, config=config, embed_info=downloader.embed_info or None)
        if job.stall.should_auto_merge(auto_merge) and downloader.stop_and_merge():
            downloader.logger.warning("Download stalled for too long, stopping and merging automatically")


# --- Schedules ---

# The cron expression from the config for each check, as written there, for display.
# (APScheduler's own description of a trigger is not in cron syntax.)
_configured_crons: dict[str, str] = {}
# Checks whose configured cron expression could not be parsed
_invalid_crons: set[str] = set()


def update_scheduler() -> tuple[bool, str]:
    """(Re)schedule every check from [cron_schedule]. Returns (all valid, message)."""
    config = load_or_create_config(default_config_path())
    messages = []
    _configured_crons.clear()
    _invalid_crons.clear()
    for name in CHECKS:
        if scheduler.get_job(job_id(name)):
            scheduler.remove_job(job_id(name))
        cron_string = config.get_cron_schedule(name)
        if not cron_string:
            continue
        cron_string = str(cron_string).strip()
        _configured_crons[name] = cron_string
        try:
            scheduler.add_job(run_check, trigger=CronTrigger.from_crontab(cron_string), args=[name], id=job_id(name),
                              max_instances=1,       # Prevent overlapping runs of the scheduled job
                              coalesce=True,         # If runs were missed, run once only
                              replace_existing=True)
        except ValueError as e:
            logger.error(f"Cron Error for {name}: {e}")
            _invalid_crons.add(name)
            messages.append(f"Invalid cron expression for {name}: {e}")
    scheduler.add_job(check_stalls, trigger='interval', seconds=STALL_CHECK_SECONDS, id=STALL_CHECK_JOB_ID,
                      max_instances=1, coalesce=True, replace_existing=True)
    if messages:
        return False, "; ".join(messages)
    return True, "All valid schedules loaded"


def schedule_rows() -> list[dict[str, Any]]:
    """Every check with its schedule status. next_run is a datetime (or None)."""
    scheduled = {job.id: job for job in scheduler.get_jobs()}
    rows = []
    for name, check in CHECKS.items():
        job = scheduled.get(job_id(name))
        if job:
            status = "Scheduled"
        elif name in _invalid_crons:
            status = "Invalid"
        else:
            status = "Inactive"
        next_run: Optional[datetime] = job.next_run_time if job else None
        rows.append({
            'id': job_id(name),
            'name': check.display,
            'cron': _configured_crons.get(name),   # As written in [cron_schedule]
            'trigger': str(job.trigger) if job else "No Schedule Set",
            'next_run': next_run,
            'status': status,
            'running': is_running(name),
        })
    return rows
