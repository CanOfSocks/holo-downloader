"""
Stall detection for active downloads.

The web app samples every active job periodically (and when the UI asks for data) by reading
the LiveStreamDownloader's `stats` dict directly:
    stats["status"]                                   overall status ("Recording", "Muxing"...)
    stats["video" | "audio"]["downloaded_segments"]   segments saved so far
    stats["video" | "audio"]["latest_sequence"]       newest segment YouTube says exists
and LiveStreamDownloader.minimum_refresh_time, which livestream_dl doubles (from
MIN_REFRESH_INTERVAL) each time a URL refresh is rate limited.

A download is "stalled" when it is recording but the number of downloaded segments hasn't
increased for `stall_timeout` seconds. The default (900s) is longer than livestream_dl's own
normal pauses (300s before handing a privated stream to recovery, 300s between refreshes for
segments that ran out of retries, about 100s per idle refresh cycle), so those are not reported.
"""
import time
from dataclasses import dataclass, field
from typing import Optional

from livestream_dl.download_Live import LiveStreamDownloader

STREAM_TYPES = ("video", "audio")

REASON_RATE_LIMITED = "yt-dlp is backing off after rate limiting"
REASON_FAILING = "Segments are failing to download"
REASON_NO_NEW_SEGMENTS = "No new segments from YouTube (stream paused, or ended without being detected)"


def segment_totals(stats: dict) -> tuple[int, int]:
    """(downloaded segments, latest sequence) summed over the video and audio streams."""
    downloaded = sum(int(stats.get(kind, {}).get("downloaded_segments") or 0) for kind in STREAM_TYPES)
    latest = sum(int(stats.get(kind, {}).get("latest_sequence") or 0) for kind in STREAM_TYPES)
    return downloaded, latest


@dataclass
class StallTracker:
    # time.monotonic() is used for durations (unaffected by clock changes), time.time() for display
    last_progress_monotonic: float = field(default_factory=time.monotonic)
    last_progress_time: float = field(default_factory=time.time)
    last_downloaded: int = -1
    # Latest sequence when progress was last seen, to tell "YouTube has no new segments" apart
    # from "new segments exist but aren't downloading"
    latest_at_last_progress: int = -1
    last_latest: int = -1
    stalled: bool = False
    stalled_since: Optional[float] = None    # monotonic
    reason: Optional[str] = None
    notified: bool = False
    auto_merge_triggered: bool = False

    def _reset(self, now_monotonic: float) -> None:
        self.last_progress_monotonic = now_monotonic
        self.last_progress_time = time.time()
        self.stalled = False
        self.stalled_since = None
        self.reason = None

    def update(self, status: str, stats: dict, refresh_backoff: float, stall_timeout: float,
               now_monotonic: Optional[float] = None) -> bool:
        """
        Record a sample. Returns True if the download has just become stalled.
        `refresh_backoff` is LiveStreamDownloader.minimum_refresh_time.
        """
        now = time.monotonic() if now_monotonic is None else now_monotonic
        downloaded, latest = segment_totals(stats)
        self.last_latest = latest

        if str(status).lower() != "recording":
            # Waiting, muxing etc. are not stalls; the timer starts again when recording (re)starts
            self._reset(now)
            self.last_downloaded = downloaded
            self.latest_at_last_progress = latest
            return False

        if downloaded > self.last_downloaded:
            self.last_downloaded = downloaded
            self.latest_at_last_progress = latest
            self._reset(now)
            self.notified = False
            return False

        if now - self.last_progress_monotonic < stall_timeout:
            return False

        if refresh_backoff > LiveStreamDownloader.MIN_REFRESH_INTERVAL:
            self.reason = REASON_RATE_LIMITED
        elif latest > self.latest_at_last_progress:
            self.reason = REASON_FAILING
        else:
            self.reason = REASON_NO_NEW_SEGMENTS

        if self.stalled:
            return False
        self.stalled = True
        self.stalled_since = now
        return True

    def stalled_for(self, now_monotonic: Optional[float] = None) -> float:
        if not self.stalled or self.stalled_since is None:
            return 0.0
        return (time.monotonic() if now_monotonic is None else now_monotonic) - self.stalled_since

    def should_auto_merge(self, auto_merge_after: float, now_monotonic: Optional[float] = None) -> bool:
        """True once, when a stalled download has been stalled for auto_merge_after seconds (0 = disabled)."""
        if auto_merge_after <= 0 or self.auto_merge_triggered or not self.stalled:
            return False
        if self.stalled_for(now_monotonic) >= auto_merge_after:
            self.auto_merge_triggered = True
            return True
        return False
