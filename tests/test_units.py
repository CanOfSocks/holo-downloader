import threading
import time
from datetime import datetime, timezone

import pytest

import common
from base_downloader import LATE_BACKOFF_DIVISOR, next_upcoming_check_delay
from common import DiscoveredVideo, WaitResult
from getConfig import MINIMUM_RECHECK_SECONDS, ConfigHandler
from getJson import parse_utc
from monitor import REASON_FAILING, REASON_NO_NEW_SEGMENTS, REASON_RATE_LIMITED, StallTracker
from unarchived import CheapCheck, classify_oembed

RECHECK_MAX = 10800.0
LATE_MAX = 900.0


# --- Upcoming stream wait ---

def test_upcoming_delay_waits_until_start_when_close():
    assert next_upcoming_check_delay(1000.0 + 600, 1000.0, RECHECK_MAX, LATE_MAX) == 600


def test_upcoming_delay_capped_by_recheck_max():
    assert next_upcoming_check_delay(1000.0 + 48 * 3600, 1000.0, RECHECK_MAX, LATE_MAX) == RECHECK_MAX


def test_upcoming_delay_never_below_minimum():
    assert next_upcoming_check_delay(1000.0 + 5, 1000.0, RECHECK_MAX, LATE_MAX) == MINIMUM_RECHECK_SECONDS
    assert next_upcoming_check_delay(1000.0 - 10, 1000.0, RECHECK_MAX, LATE_MAX) == MINIMUM_RECHECK_SECONDS


def test_late_stream_backs_off_up_to_late_max():
    start = 0.0
    assert next_upcoming_check_delay(start, start + 20 * 60, RECHECK_MAX, LATE_MAX) == 20 * 60 / LATE_BACKOFF_DIVISOR
    assert next_upcoming_check_delay(start, start + 10 * 3600, RECHECK_MAX, LATE_MAX) == LATE_MAX


def test_unknown_start_uses_late_max():
    assert next_upcoming_check_delay(None, 1000.0, RECHECK_MAX, LATE_MAX) == LATE_MAX


# --- Interruptible wait ---

def test_wait_returns_early_when_woken():
    stop, wake = threading.Event(), threading.Event()
    wake.set()
    started = time.monotonic()
    assert common.interruptible_wait(30, stop, wake) is WaitResult.WOKEN
    assert time.monotonic() - started < 1
    assert not wake.is_set()


def test_wait_returns_when_stopped():
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    started = time.monotonic()
    assert common.interruptible_wait(30, stop) is WaitResult.STOPPED
    assert time.monotonic() - started < 2


def test_wait_elapses():
    assert common.interruptible_wait(0.05, threading.Event()) is WaitResult.ELAPSED


# --- Stall detection ---

def _stats(downloaded, latest, status="Recording"):
    return {"status": status, "video": {"downloaded_segments": downloaded, "latest_sequence": latest},
            "audio": {"downloaded_segments": downloaded, "latest_sequence": latest}}


def test_stall_detected_after_timeout_without_new_segments():
    tracker = StallTracker()
    assert tracker.update("Recording", _stats(10, 10), 30.0, 900, now_monotonic=0) is False
    assert tracker.update("Recording", _stats(10, 10), 30.0, 900, now_monotonic=899) is False
    assert tracker.update("Recording", _stats(10, 10), 30.0, 900, now_monotonic=901) is True
    assert tracker.stalled and tracker.reason == REASON_NO_NEW_SEGMENTS
    # Only reported once
    assert tracker.update("Recording", _stats(10, 10), 30.0, 900, now_monotonic=1000) is False


def test_stall_reason_failing_when_youtube_has_newer_segments():
    tracker = StallTracker()
    tracker.update("Recording", _stats(10, 10), 30.0, 900, now_monotonic=0)
    tracker.update("Recording", _stats(10, 50), 30.0, 900, now_monotonic=1000)
    assert tracker.reason == REASON_FAILING


def test_stall_reason_rate_limited():
    tracker = StallTracker()
    tracker.update("Recording", _stats(10, 10), 30.0, 900, now_monotonic=0)
    tracker.update("Recording", _stats(10, 10), 120.0, 900, now_monotonic=1000)
    assert tracker.reason == REASON_RATE_LIMITED


def test_progress_clears_stall_and_other_statuses_never_stall():
    tracker = StallTracker()
    tracker.update("Recording", _stats(10, 10), 30.0, 900, now_monotonic=0)
    tracker.update("Recording", _stats(10, 10), 30.0, 900, now_monotonic=1000)
    tracker.update("Recording", _stats(11, 11), 30.0, 900, now_monotonic=1001)
    assert not tracker.stalled
    tracker.update("Muxing", _stats(11, 11), 30.0, 900, now_monotonic=5000)
    assert not tracker.stalled


def test_auto_merge_only_once_and_only_when_enabled():
    tracker = StallTracker()
    tracker.update("Recording", _stats(1, 1), 30.0, 900, now_monotonic=0)
    tracker.update("Recording", _stats(1, 1), 30.0, 900, now_monotonic=1000)
    assert tracker.should_auto_merge(0, now_monotonic=99999) is False
    assert tracker.should_auto_merge(600, now_monotonic=1500) is False
    assert tracker.should_auto_merge(600, now_monotonic=1700) is True
    assert tracker.should_auto_merge(600, now_monotonic=1800) is False


# --- Cheap check classification ---

@pytest.mark.parametrize("code, expected", [
    (200, CheapCheck.OK), (401, CheapCheck.SUSPICIOUS), (403, CheapCheck.SUSPICIOUS), (404, CheapCheck.SUSPICIOUS),
    (429, CheapCheck.RATE_LIMITED), (500, CheapCheck.UNKNOWN), (None, CheapCheck.UNKNOWN),
])
def test_classify_oembed(code, expected):
    assert classify_oembed(code) is expected


# --- Config ---

def test_wait_limit_prefers_documented_key():
    assert ConfigHandler(config={"download_options": {"new_segment_wait_limit": 1800, "segment_wait_limit": 5}}).get_new_segment_wait_limit() == 1800
    assert ConfigHandler(config={"download_options": {"segment_wait_limit": 5}}).get_new_segment_wait_limit() == 5
    assert ConfigHandler(config={}).get_new_segment_wait_limit() == 0
    options = ConfigHandler(config={"download_options": {"new_segment_wait_limit": 1800}}).get_livestream_dl_options({"id": "x"}, "a/b")
    assert options["wait_limit"] == 1800


def test_config_defaults_and_minimums():
    config = ConfigHandler(config={"download_options": {"upcoming_wait_for_video_max": 5}})
    assert config.upcoming_video_max_wait() == MINIMUM_RECHECK_SECONDS
    assert ConfigHandler(config={}).upcoming_video_max_wait() == 900
    assert ConfigHandler(config={}).get_upcoming_recheck_max() == 10800
    assert ConfigHandler(config={}).get_stall_auto_merge() == 0


def test_invalid_ytdlp_options_json_is_ignored():
    assert ConfigHandler(config={"download_options": {"ytdlp_options": "{not json"}}).get_ytdlp_options() is None
    assert ConfigHandler(config={"download_options": {"ytdlp_options": '{"a": 1}'}}).get_ytdlp_options() == {"a": 1}


def test_config_file_env_is_default_path():
    # conftest points CONFIG_FILE at the test config
    assert ConfigHandler().channel_ids_to_match == {"test": "UCxxxxxxxxxxxxxxxxxxxxxx"}


# --- Filters ---

def test_title_match_skips_description_lookup(monkeypatch):
    config = ConfigHandler()
    monkeypatch.setattr(common, "extract_video", lambda *a, **k: pytest.fail("description should not be fetched"))
    assert common.filtering({"id": "abc", "title": "Karaoke night"}, "UCtitle", config) is True


def test_description_filter_uses_extraction_when_missing(monkeypatch):
    config = ConfigHandler()
    monkeypatch.setattr(common, "extract_video", lambda *a, **k: {"description": "Singing stream"})
    assert common.filtering({"id": "def", "title": "Chatting"}, "UCdesc", config) is True
    monkeypatch.setattr(common, "extract_video", lambda *a, **k: {"description": "Gaming"})
    assert common.filtering({"id": "ghi", "title": "Chatting"}, "UCdesc", config) is False


def test_no_filters_passes():
    assert common.filtering({"id": "x", "title": "anything"}, "UCnofilter", ConfigHandler()) is True


def test_playlist_url():
    assert common.playlist_url("UCabc", "streams") == ("https://www.youtube.com/playlist?list=UUabc", True)
    assert common.playlist_url("UCabc", "membership") == ("https://www.youtube.com/playlist?list=UUMOabc", True)
    assert common.playlist_url("UUMOabc", "streams") == ("https://www.youtube.com/playlist?list=UUMOabc", True)
    assert common.playlist_url("@handle", "streams") == ("https://www.youtube.com/channel/@handle/streams", False)


# --- Misc ---

def test_bounded_cache_evicts_oldest_and_expires():
    cache = common.BoundedCache(max_entries=2, ttl=None)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.get("a") is None and cache.get("c") == 3
    expiring = common.BoundedCache(max_entries=2, ttl=0.0)
    expiring.set("a", 1)
    time.sleep(0.01)
    assert expiring.get("a") is None


def test_holodev_times_are_utc():
    assert parse_utc("2026-01-01T12:00:00.000Z") == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def _downloader(**hint):
    from downloadVid import VideoDownloader
    return VideoDownloader(DiscoveredVideo("dQw4w9WgXcQ", **hint))


def test_upcoming_hint_checks_without_format_selection(monkeypatch):
    from livestream_dl import getUrls
    downloader = _downloader(live_status="is_upcoming", release_timestamp=time.time() + 3600)
    monkeypatch.setattr(getUrls, "get_Video_Info", lambda **kw: pytest.fail("get_Video_Info fails for upcoming streams"))
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "is_upcoming", "release_timestamp": 123.0, "formats": []})
    info, status = downloader.extract_info()
    assert status == "is_upcoming" and info["release_timestamp"] == 123.0


def test_upcoming_stream_that_went_live_gets_formats(monkeypatch):
    from livestream_dl import getUrls
    downloader = _downloader(live_status="is_upcoming")
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "is_live", "formats": [{"format_id": "1"}]})
    monkeypatch.setattr(getUrls, "get_Video_Info", lambda **kw: ({"live_status": "is_live", "formats": ["processed"]}, "is_live"))
    info, status = downloader.extract_info()
    assert status == "is_live" and info["formats"] == ["processed"]
    assert downloader.hinted_live_status == "is_live"


def test_no_formats_error_falls_back_to_probe(monkeypatch):
    import yt_dlp
    from livestream_dl import getUrls

    def no_formats(**kw):
        raise yt_dlp.utils.DownloadError("requested format is not available")
    downloader = _downloader()   # Manual add: status unknown
    monkeypatch.setattr(getUrls, "get_Video_Info", no_formats)
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "is_upcoming", "release_timestamp": 5.0})
    assert downloader.extract_info()[1] == "is_upcoming"
    assert downloader.hinted_live_status == "is_upcoming"


def test_other_extraction_errors_still_raise(monkeypatch):
    import yt_dlp
    from livestream_dl import getUrls

    def blocked(**kw):
        raise yt_dlp.utils.DownloadError("some other failure")
    downloader = _downloader()
    monkeypatch.setattr(getUrls, "get_Video_Info", blocked)
    with pytest.raises(yt_dlp.utils.DownloadError):
        downloader.extract_info()


def test_upcoming_hint_but_processed_video_raises(monkeypatch):
    from livestream_dl import getUrls
    downloader = _downloader(live_status="is_upcoming")
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "was_live"})
    with pytest.raises(getUrls.VideoProcessedError):
        downloader.extract_info()


def test_discovery_hint_wakes_waiting_job():
    from downloadVid import VideoDownloader
    downloader = VideoDownloader(DiscoveredVideo("dQw4w9WgXcQ", live_status="is_upcoming", release_timestamp=time.time() + 3600))
    downloader.apply_discovery_hint(DiscoveredVideo("dQw4w9WgXcQ", live_status="is_live"))
    assert downloader.wake.is_set() and downloader._force_check
