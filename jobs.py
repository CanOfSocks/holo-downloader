"""
Active jobs: one background thread per download (VideoDownloader) or unarchived monitor
(UnarchivedDownloader). Each one is tracked as an ActiveJob in `jobs` until its thread finishes,
when the result is written to the history database and the job is removed.
"""
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Union

import common
import history
from base_downloader import BaseDownloader
from common import DiscoveredVideo
from downloadVid import VideoDownloader
from monitor import StallTracker
from unarchived import UnarchivedDownloader

# Smaller thread stacks (default is often 8 MB) as there is a thread per download.
# This applies to all threads created after this point.
try:
    threading.stack_size(1024 * 1024)
except ValueError:
    pass  # Some platforms have strict page size requirements

logger = common.initialize_logging(logger_name="jobs")

KIND_STREAM = "stream"
KIND_UNARCHIVED = "unarchived"

DOWNLOADER_CLASSES: dict[str, type[BaseDownloader]] = {
    KIND_STREAM: VideoDownloader,
    KIND_UNARCHIVED: UnarchivedDownloader,
}


@dataclass
class ActiveJob:
    video_id: str
    kind: str                       # KIND_STREAM or KIND_UNARCHIVED
    downloader: BaseDownloader
    started_at: datetime            # UTC
    thread: Optional[threading.Thread] = None   # Set by start_job (the thread needs the job as its argument)
    stall: StallTracker = field(default_factory=StallTracker)


LOCK = threading.Lock()   # Protects `jobs`
jobs: dict[str, dict[str, ActiveJob]] = {kind: {} for kind in DOWNLOADER_CLASSES}


def snapshot_jobs(kind: Optional[str] = None) -> list[ActiveJob]:
    """The active jobs of one kind (or all), as a list that is safe to use without the lock."""
    with LOCK:
        kinds = [kind] if kind else list(jobs)
        return [job for k in kinds for job in jobs[k].values()]


def get_job(kind: str, video_id: str) -> Optional[ActiveJob]:
    with LOCK:
        return jobs.get(kind, {}).get(video_id)


def stream_total_size(stats: dict) -> int:
    """Bytes downloaded so far, video and audio together."""
    return sum(int((stats.get(kind) or {}).get("current_filesize") or 0) for kind in ("video", "audio"))


def save_to_history(job: ActiveJob) -> None:
    downloader = job.downloader
    # history_reason(): brief reason for an error (e.g. "Membership"), or missing segments of a finished download
    history.save(job.video_id, job.kind, downloader.status, stream_total_size(downloader.stats), downloader.history_reason())


def run_job(job: ActiveJob) -> None:
    """Thread target for a job. Always records the result in history and removes the job."""
    try:
        job.downloader.main()
    except Exception as e:
        logger.exception(f"Error downloading {job.video_id}")
        job.downloader.set_error(e)
    finally:
        try:
            save_to_history(job)
        except Exception:
            logger.exception(f"Unable to save {job.video_id} to history")
        with LOCK:
            jobs[job.kind].pop(job.video_id, None)


def start_job(video: Union[DiscoveredVideo, str], kind: str) -> bool:
    """
    Start a job thread for the video unless one is already running. If it is, the channel
    scan's information is passed to it instead (e.g. so a waiting job starts when the stream goes live).
    Returns True if a new job was started.
    """
    if isinstance(video, str):
        video = DiscoveredVideo(video)
    existing = get_job(kind, video.id)
    if existing is not None:
        existing.downloader.apply_discovery_hint(video)
        return False
    # Built before taking the lock: the constructor reads the config file, and the web UI and the
    # stall check need the lock in the meantime. Nothing has started yet, so if another thread
    # adds the same video first this instance is simply discarded.
    downloader = DOWNLOADER_CLASSES[kind](video)
    with LOCK:
        existing = jobs[kind].get(video.id)
        if existing is None:
            job = ActiveJob(video_id=video.id, kind=kind, downloader=downloader, started_at=datetime.now(timezone.utc))
            job.thread = threading.Thread(target=run_job, args=(job,), name=f"{kind}-{video.id}", daemon=True)
            jobs[kind][video.id] = job
    if existing is not None:
        existing.downloader.apply_discovery_hint(video)
        return False
    job.thread.start()
    return True
