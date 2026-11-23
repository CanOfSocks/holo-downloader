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
    # Downloaded segments per stream type at the last sample. Tracked per stream (not as a total)
    # because livestream_dl resets a stream's stats when it restarts it, e.g. when handing a
    # privated stream over to recovery: the count then drops and starts again from a lower value.
    last_downloaded: dict[str, int] = field(default_factory=dict)
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

    def _sample_segments(self, stats: dict) -> tuple[bool, bool]:
        """
        Compare each stream's downloaded segments with the previous sample.
        Returns (progressed, new_stream): a count went up / a stream was seen for the first time.
        A count that went down (the stream was restarted) becomes the new baseline, so the next
        downloaded segment counts as progress instead of having to catch up with the old count.
        """
        progressed = new_stream = False
        for kind in STREAM_TYPES:
            stream = stats.get(kind)
            if not isinstance(stream, dict) or stream.get("downloaded_segments") is None:
                # Not started, or just reset and not reported yet: keep the previous baseline
                continue
            count = int(stream.get("downloaded_segments") or 0)
            previous = self.last_downloaded.get(kind)
            if previous is None:
                new_stream = True
            elif count > previous:
                progressed = True
            self.last_downloaded[kind] = count
        return progressed, new_stream

    def update(self, status: str, stats: dict, refresh_backoff: float, stall_timeout: float,
               now_monotonic: Optional[float] = None) -> bool:
        """
        Record a sample. Returns True if the download has just become stalled.
        `refresh_backoff` is LiveStreamDownloader.minimum_refresh_time.
        """
        now = time.monotonic() if now_monotonic is None else now_monotonic
        _, latest = segment_totals(stats)
        self.last_latest = latest
        progressed, new_stream = self._sample_segments(stats)
        # The latest sequence total also drops when a stream's stats are reset
        self.latest_at_last_progress = min(self.latest_at_last_progress, latest)

        if str(status).lower() != "recording":
            # Waiting, muxing etc. are not stalls; the timer starts again when recording (re)starts
            self._reset(now)
            self.latest_at_last_progress = latest
            return False

        if progressed or new_stream:
            # Segments are downloading (again): no longer stalled
            self.latest_at_last_progress = latest
            self._reset(now)
            self.notified = False
            self.auto_merge_triggered = False
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
