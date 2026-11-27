import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import pytest

import common
import scan
import yt_dlp

from base_downloader import LATE_BACKOFF_DIVISOR, MAX_DELAY_JITTER_FRACTION, describe_error, next_upcoming_check_delay
from common import DiscoveredVideo, WaitResult
from getConfig import MINIMUM_RECHECK_SECONDS, ConfigHandler
from getJson import parse_utc
from livestream_dl import getUrls
from monitor import REASON_FAILING, REASON_NO_NEW_SEGMENTS, REASON_RATE_LIMITED, StallTracker
from unarchived import CheapCheck, classify_oembed

RECHECK_MAX = 10800.0
LATE_MAX = 900.0


# --- Upcoming stream wait ---

def test_upcoming_delay_waits_until_start_when_close():
    assert next_upcoming_check_delay(1000.0 + 600, 1000.0, RECHECK_MAX, LATE_MAX) == 600


def _assert_jittered(delay, cap):
    assert cap * (1 - MAX_DELAY_JITTER_FRACTION) <= delay <= cap


def test_upcoming_delay_capped_by_recheck_max():
    _assert_jittered(next_upcoming_check_delay(1000.0 + 48 * 3600, 1000.0, RECHECK_MAX, LATE_MAX), RECHECK_MAX)


def test_capped_delays_are_spread_out():
    delays = {next_upcoming_check_delay(1000.0 + 48 * 3600, 1000.0, RECHECK_MAX, LATE_MAX) for _ in range(20)}
    assert len(delays) > 1


def test_uncapped_delay_has_no_jitter():
    # Just under the cap: wait exactly until the scheduled start
    assert next_upcoming_check_delay(1000.0 + RECHECK_MAX - 1, 1000.0, RECHECK_MAX, LATE_MAX) == RECHECK_MAX - 1


def test_upcoming_delay_never_below_minimum():
    assert next_upcoming_check_delay(1000.0 + 5, 1000.0, RECHECK_MAX, LATE_MAX) == MINIMUM_RECHECK_SECONDS
    assert next_upcoming_check_delay(1000.0 - 10, 1000.0, RECHECK_MAX, LATE_MAX) == MINIMUM_RECHECK_SECONDS


def test_late_stream_backs_off_up_to_late_max():
    start = 0.0
    assert next_upcoming_check_delay(start, start + 20 * 60, RECHECK_MAX, LATE_MAX) == 20 * 60 / LATE_BACKOFF_DIVISOR
    _assert_jittered(next_upcoming_check_delay(start, start + 10 * 3600, RECHECK_MAX, LATE_MAX), LATE_MAX)


def test_unknown_start_uses_late_max():
    _assert_jittered(next_upcoming_check_delay(None, 1000.0, RECHECK_MAX, LATE_MAX), LATE_MAX)


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


def test_stall_clears_when_segments_resume_after_stats_reset():
    # livestream_dl resets a stream's stats when handing it over to recovery: the count starts
    # again below the previous value, and the first new segment must clear the stall
    tracker = StallTracker()
    tracker.update("Recording", _stats(500, 500), 30.0, 900, now_monotonic=0)
    assert tracker.update("Recording", _stats(500, 500), 30.0, 900, now_monotonic=1000) is True
    tracker.update("Recording", {"status": "Recording", "video": {}, "audio": {}}, 30.0, 900, now_monotonic=1030)
    assert tracker.stalled
    tracker.update("Recording", _stats(450, 520), 30.0, 900, now_monotonic=1060)
    assert tracker.stalled
    tracker.update("Recording", _stats(451, 520), 30.0, 900, now_monotonic=1090)
    assert not tracker.stalled and tracker.reason is None
    # And it can stall (and auto merge) again later
    assert tracker.update("Recording", _stats(451, 520), 30.0, 900, now_monotonic=2000) is True


def test_stall_clears_when_only_one_stream_resumes():
    tracker = StallTracker()
    tracker.update("Recording", _stats(10, 10), 30.0, 900, now_monotonic=0)
    tracker.update("Recording", _stats(10, 10), 30.0, 900, now_monotonic=1000)
    stats = _stats(10, 10)
    stats["audio"]["downloaded_segments"] = 11
    tracker.update("Recording", stats, 30.0, 900, now_monotonic=1030)
    assert not tracker.stalled


def test_auto_merge_only_once_and_only_when_enabled():
    tracker = StallTracker()
    tracker.update("Recording", _stats(1, 1), 30.0, 900, now_monotonic=0)
    tracker.update("Recording", _stats(1, 1), 30.0, 900, now_monotonic=1000)
    assert tracker.should_auto_merge(0, now_monotonic=99999) is False
    assert tracker.should_auto_merge(600, now_monotonic=1500) is False
    assert tracker.should_auto_merge(600, now_monotonic=1700) is True
    assert tracker.should_auto_merge(600, now_monotonic=1800) is False


# --- Error reasons ---

@pytest.mark.parametrize("error, expected", [
    (getUrls.VideoInaccessibleError("Video abc is a membership or premium video"), "Membership"),
    (getUrls.VideoInaccessibleError("Video abc is private"), "Private"),
    (getUrls.VideoInaccessibleError("Video is age-restricted and requires valid cookies"), "Age restricted"),
    (getUrls.VideoInaccessibleError("This live stream recording is not available."), "Recording unavailable"),
    (getUrls.VideoInaccessibleError("Something else"), "Inaccessible"),
    (getUrls.VideoProcessedError("Video has been processed, please use yt-dlp directly"), "Processed"),
    (getUrls.VideoUnavailableError("Video abc is unavailable"), "Unavailable"),
    (getUrls.RateLimitException("Rate limited"), "Rate limited"),
    (getUrls.VideoDownloadError("outstanding segments"), "Missing segments"),
    (TimeoutError("Current time exceeds latest URL expiry time of x"), "URLs expired"),
    (yt_dlp.utils.DownloadError("ERROR: Join this channel to get access to members-only content"), "Membership"),
    (yt_dlp.utils.DownloadError("ERROR: something odd"), "yt-dlp error"),
    (ValueError("boom"), "ValueError"),
])
def test_describe_error(error, expected):
    assert describe_error(error) == expected


# --- Cheap check classification ---

@pytest.mark.parametrize("code, expected", [
    (200, CheapCheck.OK), (401, CheapCheck.SUSPICIOUS), (403, CheapCheck.SUSPICIOUS), (404, CheapCheck.SUSPICIOUS),
    (429, CheapCheck.RATE_LIMITED), (500, CheapCheck.UNKNOWN), (None, CheapCheck.UNKNOWN),
])
def test_classify_oembed(code, expected):
    assert classify_oembed(code) is expected


# --- Config ---

def test_stop_chat_when_done():
    assert ConfigHandler(config={}).get_stop_chat_when_done() == 900
    assert ConfigHandler(config={"download_options": {"stop_chat_when_done": 0}}).get_stop_chat_when_done() == 0
    options = ConfigHandler(config={"download_options": {"stop_chat_when_done": 60}}).get_livestream_dl_options({"id": "x"}, "a/b")
    assert options["stop_chat_when_done"] == 60


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
    monkeypatch.setattr(scan, "extract_video", lambda *a, **k: pytest.fail("description should not be fetched"))
    assert scan.passes_filters({"id": "abc", "title": "Karaoke night"}, "UCtitle", config) is True


def test_description_filter_uses_extraction_when_missing(monkeypatch):
    config = ConfigHandler()
    monkeypatch.setattr(scan, "extract_video", lambda *a, **k: {"description": "Singing stream"})
    assert scan.passes_filters({"id": "def", "title": "Chatting"}, "UCdesc", config) is True
    monkeypatch.setattr(scan, "extract_video", lambda *a, **k: {"description": "Gaming"})
    assert scan.passes_filters({"id": "ghi", "title": "Chatting"}, "UCdesc", config) is False


def test_no_filters_passes():
    assert scan.passes_filters({"id": "x", "title": "anything"}, "UCnofilter", ConfigHandler()) is True


# Channel ID body (the 22 characters after "UC") used by the members tests
MEMBERS_SUFFIX = "xsZ6NCzjU_t4YSxQLBcM5A"


class FakeYoutubeDL:
    """Stands in for yt-dlp: returns `FakeYoutubeDL.results[url]` and records the URLs requested."""
    results: dict = {}
    requested: list = []

    def __init__(self, options):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        FakeYoutubeDL.requested.append(url)
        return FakeYoutubeDL.results[url]


@pytest.fixture
def fake_ytdlp(monkeypatch):
    monkeypatch.setattr(scan, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(FakeYoutubeDL, "results", {})
    monkeypatch.setattr(FakeYoutubeDL, "requested", [])
    return FakeYoutubeDL


def test_members_scan_ignores_filters(monkeypatch, fake_ytdlp):
    """The [members_only] scan downloads every members stream, even if the channel has filters."""
    import getMembers

    channel = "UC" + MEMBERS_SUFFIX
    fake_ytdlp.results["https://www.youtube.com/playlist?list=UUMO" + MEMBERS_SUFFIX] = {
        "entries": [{"id": "members0001", "title": "Members chat", "live_status": "is_upcoming",
                     "release_timestamp": None, "channel_id": channel}]}
    monkeypatch.setattr(scan, "extract_video", lambda *a, **k: {"description": "Gaming"})
    monkeypatch.setattr(getMembers.kill_all, "wait", lambda *a, **k: False)
    config = ConfigHandler(config={"members_only": {"filtered": channel},
                                   "title_filter": {channel: "(?i)karaoke"}})

    # The same video is filtered out of a normal scan...
    assert scan.get_upcoming_or_live_videos(channel, config, tab="membership") == []

    class Output(list):
        put = list.append

    found = Output()
    getMembers.main(found, config=config)
    assert [video.id for video in found] == ["members0001"]


def test_members_playlist_id_from_ids():
    config = ConfigHandler(config={})
    for value in ("UC", "UU", "UUMO", "UULF", "UUSH"):
        assert scan.members_playlist_id(value + MEMBERS_SUFFIX, config) == "UUMO" + MEMBERS_SUFFIX


def test_members_playlist_id_resolves_handle_once(fake_ytdlp):
    config = ConfigHandler(config={})
    fake_ytdlp.results["https://www.youtube.com/@testhandle"] = {"channel_id": "UC" + MEMBERS_SUFFIX, "entries": []}
    assert scan.members_playlist_id("@testhandle", config) == "UUMO" + MEMBERS_SUFFIX
    assert scan.members_playlist_id("@testhandle", config) == "UUMO" + MEMBERS_SUFFIX
    assert fake_ytdlp.requested == ["https://www.youtube.com/@testhandle"]


def test_members_playlist_id_unknown_channel(fake_ytdlp):
    fake_ytdlp.results["https://www.youtube.com/@missing"] = {"entries": []}
    with pytest.raises(ValueError):
        scan.members_playlist_id("@missing", ConfigHandler(config={}))


def test_members_scan_uses_uumo_for_handles(fake_ytdlp):
    config = ConfigHandler(config={})
    fake_ytdlp.results["https://www.youtube.com/@scanhandle"] = {"channel_id": "UC" + MEMBERS_SUFFIX}
    fake_ytdlp.results["https://www.youtube.com/playlist?list=UUMO" + MEMBERS_SUFFIX] = {"entries": []}
    assert scan.get_upcoming_or_live_videos("@scanhandle", config, tab="membership", apply_filters=False) == []
    assert fake_ytdlp.requested[-1] == "https://www.youtube.com/playlist?list=UUMO" + MEMBERS_SUFFIX


class FakeSession:
    def __init__(self):
        self.timeouts = []

    def request(self, method, url, *args, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


@pytest.fixture
def fake_ytct(monkeypatch):
    """A stand-in for ytct.py and the youtube_community_tab library. `on_download(log, link)` runs for
    each link and returns the result's (downloaded, failed) lists."""
    import sys
    import types

    import communityPosts

    session = FakeSession()
    ytct = types.SimpleNamespace(calls=[], on_download=lambda log, link: ([], []), session=session)

    class Downloader:
        def __init__(self, log=None, **options):
            self.log = log
            ytct.calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.log("ytct", "closed")

        def download_all(self, links):
            result = types.SimpleNamespace(downloaded=[], failed=[])
            for link in links:
                downloaded, failed = ytct.on_download(self.log, link)
                result.downloaded += downloaded
                result.failed += failed
            return result

    ytct.Downloader = Downloader
    handler = types.ModuleType("youtube_community_tab.requests_handler")
    handler.requests_cache = session
    monkeypatch.setitem(sys.modules, "ytct", ytct)
    monkeypatch.setitem(sys.modules, "youtube_community_tab", types.ModuleType("youtube_community_tab"))
    monkeypatch.setitem(sys.modules, "youtube_community_tab.requests_handler", handler)
    # Independent of any other test (or shutdown) having set the global stop flag
    monkeypatch.setattr(communityPosts, "kill_all", threading.Event())
    return ytct


def test_community_tab_uses_only_its_own_list(monkeypatch, tmp_path, fake_ytct):
    """Only [community_tab] channels are fetched, and filters are never involved."""
    import communityPosts

    links = []
    fake_ytct.on_download = lambda log, link: links.append(link) or ([], [])
    for name in ("passes_filters", "title_filter_matches", "description_filter_matches", "extract_video"):
        monkeypatch.setattr(scan, name, lambda *a, **k: pytest.fail("community tab must not use filters"))
    config = ConfigHandler(config={
        "channel_ids_to_match": {"normal": "UCnormal"},
        "members_only": {"members": "UCmembers"},
        "community_tab": {"posts": "UCposts"},
        "title_filter": {"UCposts": "(?i)karaoke"},
        "description_filter": {"UCposts": "(?i)singing"},
        "community_tab_options": {"community_dir": str(tmp_path), "archive_file": str(tmp_path / "archive.txt")},
    })
    communityPosts.main(config=config)
    assert links == ["https://www.youtube.com/channel/UCposts/posts"]
    options = fake_ytct.calls[0]
    assert options["directory"] == os.path.join(str(tmp_path), "posts")
    assert options["post_archive"] == str(tmp_path / "archive.txt")
    assert options["dates"] and options["reverse"] and options["ignore_errors"]


def test_community_tab_logs_to_the_holo_log(tmp_path, fake_ytct, caplog):
    """ytct's lines go to holo.community_posts at a level matching what they report."""
    import communityPosts

    def on_download(log, link):
        log("community tab", "getting posts from community tab (page 1)", "\r")
        log("community tab", "found 2 posts")
        log("post:Ugx1", "writing Ugx1.json")
        log("post:Ugx2", "failed: KeyError: 'x'")
        log("cookies", "WARNING: YouTube did not accept the login cookies (not logged in)")
        return ["Ugx1"], ["Ugx2: KeyError: 'x'"]

    fake_ytct.on_download = on_download
    config = ConfigHandler(config={"community_tab": {"posts": "UCposts"},
                                   "community_tab_options": {"community_dir": str(tmp_path)}})
    logger = logging.getLogger("holo.community_posts")
    logger.setLevel(logging.DEBUG)
    with caplog.at_level(logging.DEBUG, logger="holo.community_posts"):
        logger.addHandler(caplog.handler)
        try:
            communityPosts.main(config=config, logger=logger)
        finally:
            logger.removeHandler(caplog.handler)
    lines = {record.getMessage(): record.levelno for record in caplog.records}
    assert lines["Community tab posts: [community tab] getting posts from community tab (page 1)"] == logging.DEBUG
    assert lines["Community tab posts: [community tab] found 2 posts"] == logging.INFO
    assert lines["Community tab posts: [post:Ugx1] writing Ugx1.json"] == logging.DEBUG
    assert lines["Community tab posts: [post:Ugx2] failed: KeyError: 'x'"] == logging.WARNING
    assert lines["Community tab posts: [cookies] WARNING: YouTube did not accept the login cookies (not logged in)"] == logging.WARNING
    assert any(level == logging.WARNING and "1 new, 1 failed" in message for message, level in lines.items())
    # The library's own requests get a timeout
    fake_ytct.session.get("https://www.youtube.com/")
    assert fake_ytct.session.timeouts == [communityPosts.REQUEST_TIMEOUT_SECONDS]


def test_community_tab_stops_on_shutdown(tmp_path, fake_ytct):
    """A shutdown stops the channel at ytct's next log line and skips the remaining channels;
    ytct's cleanup still runs and may log."""
    import communityPosts

    links, after_stop = [], []

    def on_download(log, link):
        links.append(link)
        communityPosts.kill_all.set()
        log("post:Ugx1", "writing Ugx1.json")
        after_stop.append(link)
        return [], []

    fake_ytct.on_download = on_download
    config = ConfigHandler(config={"community_tab": {"a": "UCa", "b": "UCb"},
                                   "community_tab_options": {"community_dir": str(tmp_path)}})
    communityPosts.main(config=config)
    assert links == ["https://www.youtube.com/channel/UCa/posts"]
    assert after_stop == []


def test_community_tab_channel_timeout(monkeypatch, tmp_path, fake_ytct):
    import communityPosts

    reached = []

    monkeypatch.setattr(communityPosts, "CHANNEL_TIMEOUT_SECONDS", -1)

    def on_download(log, link):
        log("post:Ugx1", "writing Ugx1.json")
        reached.append(link)
        return [], []

    fake_ytct.on_download = on_download
    config = ConfigHandler(config={"community_tab": {"a": "UCa", "b": "UCb"},
                                   "community_tab_options": {"community_dir": str(tmp_path)}})
    communityPosts.main(config=config)
    # Each channel times out on its own; the next one still runs
    assert len(fake_ytct.calls) == 2 and reached == []


def test_playlist_url():
    channel = "UC" + MEMBERS_SUFFIX
    assert scan.playlist_url(channel, "streams") == ("https://www.youtube.com/playlist?list=UU" + MEMBERS_SUFFIX, True)
    assert scan.playlist_url(channel, "membership") == ("https://www.youtube.com/playlist?list=UUMO" + MEMBERS_SUFFIX, True)
    assert scan.playlist_url("UULF" + MEMBERS_SUFFIX, "membership") == ("https://www.youtube.com/playlist?list=UUMO" + MEMBERS_SUFFIX, True)
    assert scan.playlist_url("UUMO" + MEMBERS_SUFFIX, "streams") == ("https://www.youtube.com/playlist?list=UUMO" + MEMBERS_SUFFIX, True)
    assert scan.playlist_url("@handle", "streams") == ("https://www.youtube.com/channel/@handle/streams", False)


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
    downloader = _downloader(live_status="is_upcoming", release_timestamp=time.time() + 3600)
    monkeypatch.setattr(getUrls, "get_Video_Info", lambda **kw: pytest.fail("get_Video_Info fails for upcoming streams"))
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "is_upcoming", "release_timestamp": 123.0, "formats": []})
    info, status = downloader.extract_info()
    assert status == "is_upcoming" and info["release_timestamp"] == 123.0


def test_upcoming_stream_that_went_live_gets_formats(monkeypatch):
    downloader = _downloader(live_status="is_upcoming")
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "is_live", "formats": [{"format_id": "1"}]})
    monkeypatch.setattr(getUrls, "get_Video_Info", lambda **kw: ({"live_status": "is_live", "formats": ["processed"]}, "is_live"))
    info, status = downloader.extract_info()
    assert status == "is_live" and info["formats"] == ["processed"]
    assert downloader.hinted_live_status == "is_live"


def test_no_formats_error_falls_back_to_probe(monkeypatch):

    def no_formats(**kw):
        raise yt_dlp.utils.DownloadError("requested format is not available")
    downloader = _downloader()   # Manual add: status unknown
    monkeypatch.setattr(getUrls, "get_Video_Info", no_formats)
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "is_upcoming", "release_timestamp": 5.0})
    assert downloader.extract_info()[1] == "is_upcoming"
    assert downloader.hinted_live_status == "is_upcoming"


def test_other_extraction_errors_still_raise(monkeypatch):

    def blocked(**kw):
        raise yt_dlp.utils.DownloadError("some other failure")
    downloader = _downloader()
    monkeypatch.setattr(getUrls, "get_Video_Info", blocked)
    with pytest.raises(yt_dlp.utils.DownloadError):
        downloader.extract_info()


def test_upcoming_hint_but_processed_video_raises(monkeypatch):
    downloader = _downloader(live_status="is_upcoming")
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "not_live"})
    monkeypatch.setattr(getUrls, "get_Video_Info", lambda **kw: pytest.fail("not_live is processed, no second extraction"))
    with pytest.raises(getUrls.VideoProcessedError):
        downloader.extract_info()


def test_upcoming_hint_was_live_is_decided_by_get_video_info(monkeypatch):
    downloader = _downloader(live_status="is_upcoming")
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "was_live", "formats": [{"format_id": "1"}]})
    # livestream_dl reports post_live while the formats still have live stream URLs
    monkeypatch.setattr(getUrls, "get_Video_Info", lambda **kw: ({"live_status": "was_live", "formats": ["live"]}, "post_live"))
    info, status = downloader.extract_info()
    assert status == "post_live" and downloader.hinted_live_status == "post_live"

    downloader = _downloader(live_status="is_upcoming")
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "was_live"})

    def processed(**kw):
        raise getUrls.VideoProcessedError("Video has been processed, please use yt-dlp directly")
    monkeypatch.setattr(getUrls, "get_Video_Info", processed)
    with pytest.raises(getUrls.VideoProcessedError):
        downloader.extract_info()


@pytest.mark.parametrize("hint", ["is_upcoming", None])
def test_ended_video_without_formats_raises_processed(monkeypatch, hint):

    def no_formats(**kw):
        raise yt_dlp.utils.DownloadError("requested format is not available")
    downloader = _downloader(live_status=hint)
    monkeypatch.setattr(getUrls, "get_Video_Info", no_formats)
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "was_live"})
    with pytest.raises(getUrls.VideoProcessedError):
        downloader.extract_info()


def test_wait_at_least_ignores_wake_but_not_check_now():
    downloader = _downloader()
    downloader.wake.set()    # A channel scan update
    started = time.monotonic()
    assert downloader.wait_at_least(0.3) is True
    assert time.monotonic() - started >= 0.29

    downloader.check_now()
    started = time.monotonic()
    assert downloader.wait_at_least(30) is True
    assert time.monotonic() - started < 5 and downloader._force_check is False

    downloader.kill_this.set()
    assert downloader.wait_at_least(30) is False


def test_private_report_is_confirmed_before_giving_up(monkeypatch):
    downloader = _downloader()
    calls = []

    def extract(**kw):
        calls.append(1)
        if len(calls) == 1:
            raise getUrls.VideoInaccessibleError("Video dQw4w9WgXcQ is private")
        return {"live_status": "is_live", "formats": ["f"]}, "is_live"
    monkeypatch.setattr(getUrls, "get_Video_Info", extract)
    waits = []
    monkeypatch.setattr(downloader, "wait", lambda seconds: waits.append(seconds) or WaitResult.ELAPSED)
    assert downloader.wait_for_stream() == {"live_status": "is_live", "formats": ["f"]}
    assert len(calls) == 2 and len(waits) == 1 and waits[0] == pytest.approx(60, abs=1)


def test_repeated_private_report_raises(monkeypatch):
    downloader = _downloader()

    def private(**kw):
        raise getUrls.VideoInaccessibleError("Video dQw4w9WgXcQ is private")
    monkeypatch.setattr(getUrls, "get_Video_Info", private)
    monkeypatch.setattr(downloader, "wait", lambda seconds: WaitResult.ELAPSED)
    with pytest.raises(getUrls.VideoInaccessibleError):
        downloader.wait_for_stream()


def test_other_inaccessible_errors_are_not_retried(monkeypatch):
    downloader = _downloader()

    def members(**kw):
        raise getUrls.VideoInaccessibleError("Video dQw4w9WgXcQ is a membership or premium video")
    monkeypatch.setattr(getUrls, "get_Video_Info", members)
    monkeypatch.setattr(downloader, "wait", lambda seconds: pytest.fail("membership errors are not confirmed"))
    with pytest.raises(getUrls.VideoInaccessibleError):
        downloader.wait_for_stream()


def test_unarchived_private_report_is_confirmed_before_recovery(monkeypatch, tmp_path):
    from unarchived import Refresh
    downloader = _unarchived(tmp_path)

    def private(**kw):
        raise getUrls.VideoInaccessibleError("Video dQw4w9WgXcQ is private")
    monkeypatch.setattr(getUrls, "get_Video_Info", private)
    before = time.time()
    assert downloader.refresh_urls() is Refresh.UNCONFIRMED
    assert before + 59 <= downloader.next_url_refresh <= time.time() + 61
    assert downloader.refresh_urls() is Refresh.PRIVATE
    # Still private after a failed recovery: no further confirmation
    assert downloader.refresh_urls() is Refresh.PRIVATE


def test_history_reason_reports_missing_segments():
    downloader = _downloader()
    assert downloader.history_reason() is None
    downloader.stats["video"] = {"missing_segments": 3}
    downloader.stats["audio"] = {"missing_segments": 1}
    downloader.set_status("Finished")
    assert downloader.history_reason() == "3 segments missing"
    downloader.stats["video"] = {"missing_segments": 1}
    assert downloader.history_reason() == "1 segment missing"
    downloader.set_status("Cancelled")
    assert downloader.history_reason() is None
    downloader.set_error(getUrls.VideoInaccessibleError("Video abc is private"))
    assert downloader.history_reason() == "Private"


def _unarchived(tmp_path, **hint):
    """An unarchived monitor whose temp folder is this test's own tmp_path (no files shared with other tests)."""
    from unarchived import UnarchivedDownloader
    config = ConfigHandler(config={"download_options": {"temp_dir": str(tmp_path), "unarchived_tempdir": str(tmp_path)}})
    return UnarchivedDownloader(DiscoveredVideo("dQw4w9WgXcQ", **hint), config=config)


def _unarchived_with_saved_json(tmp_path, age: float):
    """An unarchived monitor whose saved info.json was extracted `age` seconds ago."""
    downloader = _unarchived(tmp_path)
    with open(downloader.json_path, "w", encoding="utf-8") as f:
        json.dump({"id": "dQw4w9WgXcQ", "epoch": time.time() - age}, f)
    downloader.json_saved_at = downloader._saved_json_time()
    return downloader


def _bot_check(**kw):
    raise getUrls.RateLimitException("Rate limited or blocked by YouTube anti-bot measures")


def test_unarchived_rate_limit_retries_without_error_notification(monkeypatch, tmp_path):
    from unarchived import INFO_JSON_MAX_AGE, RECOVER_BEFORE_EXPIRY, URL_REFRESH_RETRY, Refresh
    downloader = _unarchived_with_saved_json(tmp_path, age=INFO_JSON_MAX_AGE - RECOVER_BEFORE_EXPIRY - 300)
    monkeypatch.setattr(getUrls, "get_Video_Info", _bot_check)
    monkeypatch.setattr(downloader, "notify", lambda *a, **kw: pytest.fail("no error notification for a rate limit"))
    assert downloader.refresh_urls() is Refresh.RATE_LIMITED
    # Retried at the recovery deadline (5 minutes away), not URL_REFRESH_RETRY later
    assert downloader.next_url_refresh == pytest.approx(downloader._recovery_deadline(), abs=1)
    assert downloader.next_url_refresh < time.time() + URL_REFRESH_RETRY


def test_unarchived_recovers_before_saved_urls_expire(monkeypatch, tmp_path):
    from unarchived import INFO_JSON_MAX_AGE, RECOVER_BEFORE_EXPIRY
    downloader = _unarchived_with_saved_json(tmp_path, age=INFO_JSON_MAX_AGE - RECOVER_BEFORE_EXPIRY + 60)
    monkeypatch.setattr(getUrls, "get_Video_Info", _bot_check)
    recovered = []
    monkeypatch.setattr(downloader, "recover", lambda: recovered.append(1) or True)
    downloader.next_url_refresh = 0.0
    downloader._monitor()
    assert recovered == [1]


def test_unarchived_expiry_recovery_conditions(tmp_path):
    from unarchived import INFO_JSON_MAX_AGE, RECOVER_BEFORE_EXPIRY
    early = _unarchived_with_saved_json(tmp_path, age=3600)
    assert early._recover_before_expiry(suspicious=False) is False
    late = _unarchived_with_saved_json(tmp_path, age=INFO_JSON_MAX_AGE - RECOVER_BEFORE_EXPIRY + 60)
    late.processed = True
    # Public archive: only if the cheap check has since reported the video inaccessible
    assert late._recover_before_expiry(suspicious=False) is False
    assert late._recover_before_expiry(suspicious=True) is True
    # Only tried once
    assert late._recover_before_expiry(suspicious=True) is False


def test_discovery_hint_wakes_waiting_job():
    from downloadVid import VideoDownloader
    downloader = VideoDownloader(DiscoveredVideo("dQw4w9WgXcQ", live_status="is_upcoming", release_timestamp=time.time() + 3600))
    downloader.apply_discovery_hint(DiscoveredVideo("dQw4w9WgXcQ", live_status="is_live"))
    assert downloader.wake.is_set() and downloader._force_check


# --- Look-ahead window while waiting ---

def test_waiting_job_stops_when_rescheduled_beyond_look_ahead():
    from base_downloader import STATUS_BEYOND_LOOK_AHEAD
    downloader = _downloader(live_status="is_upcoming", release_timestamp=time.time() + 3600)
    assert not downloader.beyond_look_ahead()
    # A channel scan reports the stream moved a week out: the wait ends instead of continuing
    downloader.apply_discovery_hint(DiscoveredVideo("dQw4w9WgXcQ", live_status="is_upcoming",
                                                    release_timestamp=time.time() + 7 * 24 * 3600))
    assert downloader.wait_until_next_check() is False
    assert downloader.status == STATUS_BEYOND_LOOK_AHEAD


def test_extraction_beyond_look_ahead_ends_job(monkeypatch):
    from base_downloader import STATUS_BEYOND_LOOK_AHEAD
    downloader = _downloader()   # Start time unknown when the job began
    far = time.time() + 30 * 24 * 3600
    monkeypatch.setattr(downloader, "load_embed_info", lambda: None)
    monkeypatch.setattr(downloader, "extract_info", lambda **kw: ({"live_status": "is_upcoming", "release_timestamp": far}, "is_upcoming"))
    downloader.main()
    assert downloader.status == STATUS_BEYOND_LOOK_AHEAD


def test_manual_add_ignores_look_ahead():
    downloader = _downloader(release_timestamp=time.time() + 30 * 24 * 3600, manual=True)
    assert not downloader.beyond_look_ahead()


def test_live_stream_never_beyond_look_ahead():
    downloader = _downloader(live_status="is_live", release_timestamp=time.time() + 30 * 24 * 3600)
    assert not downloader.beyond_look_ahead()


# --- Scheduled start from flat playlist listings ---

def _lockup(texts):
    return {'contentId': 'VKZZmT7gllw', 'metadata': {'lockupMetadataViewModel': {'metadata': {'contentMetadataViewModel': {
        'metadataRows': [{'metadataParts': [{'text': {'content': t}} for t in texts]}]}}}}}


@pytest.mark.parametrize("text, expected", [
    ("Scheduled for 11/14/26, 11:00 AM", datetime(2026, 11, 14, 11, 0, tzinfo=timezone.utc)),
    ("Scheduled for 12/7/26, 9:00\u202fPM", datetime(2026, 12, 7, 21, 0, tzinfo=timezone.utc)),
    ("Premieres 1/2/2027, 12:30 AM", datetime(2027, 1, 2, 0, 30, tzinfo=timezone.utc)),
    ("Scheduled for 13/40/26, 9:00 AM", None),
    ("141 waiting", None),
])
def test_parse_scheduled_text(text, expected):
    from lockup_schedule import parse_scheduled_text
    assert parse_scheduled_text(text) == (int(expected.timestamp()) if expected else None)


def test_scheduled_start_from_lockup_metadata():
    from lockup_schedule import scheduled_start_from_lockup
    assert scheduled_start_from_lockup(_lockup(["141 waiting", "Scheduled for 11/14/26, 11:00 AM"])) == 1794654000
    assert scheduled_start_from_lockup({'contentId': 'x'}) is None


def test_flat_listing_entries_get_release_timestamp():
    # lockup_schedule.install() runs when scan is imported
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.youtube import YoutubeTabIE
    ie = YoutubeTabIE(YoutubeDL({'quiet': True}))
    view_model = _lockup(["Scheduled for 11/14/26, 11:00 AM"])
    view_model['contentType'] = 'LOCKUP_CONTENT_TYPE_VIDEO'
    view_model['contentImage'] = {'thumbnailViewModel': {'overlays': [{'thumbnailBottomOverlayViewModel': {
        'badges': [{'thumbnailBadgeViewModel': {'text': 'Upcoming'}}]}}]}}
    entry = ie._extract_lockup_view_model(view_model)
    if entry.get('live_status') != 'is_upcoming':
        pytest.skip("yt-dlp's lockup format differs from this sample")
    assert entry['release_timestamp'] == 1794654000


def test_far_future_schedule_remembered_for_scans():
    downloader = _downloader(live_status="is_upcoming", release_timestamp=time.time() + 30 * 24 * 3600)
    assert downloader.stop_if_beyond_look_ahead()
    assert scan.known_schedules.get(downloader.id) == downloader.scheduled_start


# --- Unexpected status of an upcoming stream is confirmed ---

@pytest.mark.parametrize("reported", [None, "not_live"])
def test_unexpected_status_of_upcoming_stream_is_confirmed(monkeypatch, reported):
    downloader = _downloader(live_status="is_upcoming")
    probes = iter([{"live_status": reported}, {"live_status": "is_live", "formats": [{"format_id": "1"}]}])
    monkeypatch.setattr(downloader, "probe_status", lambda: next(probes))
    monkeypatch.setattr(getUrls, "get_Video_Info", lambda **kw: ({"live_status": "is_live", "formats": ["f"]}, "is_live"))
    waits = []
    monkeypatch.setattr(downloader, "wait", lambda seconds: waits.append(seconds) or WaitResult.ELAPSED)
    assert downloader.wait_for_stream() == {"live_status": "is_live", "formats": ["f"]}
    assert len(waits) == 1 and waits[0] == pytest.approx(60, abs=1)


def test_repeated_unexpected_status_raises(monkeypatch):
    from base_downloader import UnexpectedStatusError
    downloader = _downloader(live_status="is_upcoming")
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "not_live"})
    monkeypatch.setattr(downloader, "wait", lambda seconds: WaitResult.ELAPSED)
    with pytest.raises(UnexpectedStatusError):
        downloader.wait_for_stream()
    # Shown in history as what it is, not as a processed (public) video
    assert describe_error(UnexpectedStatusError("x")) == "Unexpected status"


def test_unarchived_unexpected_status_is_confirmed_then_stops(monkeypatch, tmp_path):
    from base_downloader import STATUS_ERROR
    from unarchived import Refresh
    downloader = _unarchived(tmp_path, live_status="is_upcoming")
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": None})
    assert downloader.refresh_urls() is Refresh.UNCONFIRMED
    assert downloader.refresh_urls() is Refresh.UNEXPECTED
    assert downloader.status == STATUS_ERROR and downloader.history_reason() == "Unexpected status"
    assert not downloader.processed

    # The monitor loop stops on it
    downloader = _unarchived(tmp_path, live_status="is_upcoming")
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": None})

    def confirming_check_due(seconds):
        downloader.next_url_refresh = 0.0   # As if CONFIRM_DELAY had passed
        return WaitResult.ELAPSED
    monkeypatch.setattr(downloader, "wait", confirming_check_due)
    downloader._monitor()
    assert downloader.status == STATUS_ERROR


def test_unarchived_unexpected_status_with_saved_urls_keeps_watching(monkeypatch, tmp_path):
    from unarchived import Refresh
    downloader = _unarchived_with_saved_json(tmp_path, age=60)
    downloader.hinted_live_status = "is_upcoming"
    monkeypatch.setattr(downloader, "probe_status", lambda: {"live_status": "not_live"})
    monkeypatch.setattr(downloader, "_record_baseline", lambda: None)
    assert downloader.refresh_urls() is Refresh.UNCONFIRMED
    # Saved URLs could still be needed: handled like a public archive, so monitoring continues
    assert downloader.refresh_urls() is Refresh.PROCESSED


# --- Unarchived monitor ---

def test_unarchived_cancel_during_wait_is_recorded_as_cancelled(monkeypatch, tmp_path):
    from base_downloader import STATUS_CANCELLED
    downloader = _unarchived(tmp_path)
    downloader.set_status("Monitoring")
    downloader.next_url_refresh = time.time() + 3600   # Nothing due: goes straight to the wait
    threading.Timer(0.2, downloader.cancel).start()
    downloader._monitor()
    assert downloader.status == STATUS_CANCELLED


def test_unarchived_recovery_retry_uses_fresh_downloader(monkeypatch, tmp_path):
    downloader = _unarchived_with_saved_json(tmp_path, age=60)
    monkeypatch.setattr(downloader, "notify", lambda *a, **kw: None)
    used = []

    def failing_download(self, **kw):
        used.append(self)
        raise RuntimeError("recovery failed")
    monkeypatch.setattr(type(downloader.livestream_downloader), "download_segments", failing_download)
    assert downloader.recover() is False
    # "Stop & merge" during the failed attempt, then a successful refresh changes the status
    downloader.livestream_downloader.graceful_stop.set()
    downloader.set_status("Monitoring")
    assert downloader.recover() is False
    assert used[0] is not used[1]
    assert not used[1].graceful_stop.is_set()


def test_unarchived_scan_ignores_filters(monkeypatch, fake_ytdlp):
    """Title/description filters only apply to normal downloads, never to the unarchived monitor."""
    import getYTDLP
    monkeypatch.setattr(scan, "saved_unarchived_ids", lambda *a, **k: [])
    fake_ytdlp.results["https://www.youtube.com/playlist?list=UU" + MEMBERS_SUFFIX] = {
        "entries": [{"id": "unarchived1", "title": "Chatting", "live_status": "is_live", "channel_id": "UC" + MEMBERS_SUFFIX}]}
    monkeypatch.setattr(scan, "extract_video", lambda *a, **k: {"description": "Gaming"})
    channel = "UC" + MEMBERS_SUFFIX
    config = ConfigHandler(config={"channel_ids_to_match": {"c": channel}, "unarchived_channel_ids_to_match": {"c": channel},
                                   "title_filter": {channel: "(?i)karaoke"}})

    class Output(list):
        put = list.append

    normal, unarchived = Output(), Output()
    getYTDLP.main(normal, unarchived=False, config=config)
    getYTDLP.main(unarchived, unarchived=True, config=config)
    assert normal == [] and [video.id for video in unarchived] == ["unarchived1"]


def test_unarchived_deleted_video_is_confirmed_then_recovered(monkeypatch, tmp_path):
    """'Video unavailable' (deleted) starts recovery like a private video, after one more check."""
    from unarchived import Refresh
    downloader = _unarchived(tmp_path)

    def deleted(**kw):
        raise getUrls.VideoUnavailableError("Video dQw4w9WgXcQ is unavailable")
    monkeypatch.setattr(getUrls, "get_Video_Info", deleted)
    monkeypatch.setattr(downloader, "notify", lambda *a, **kw: pytest.fail("no error notification for a deleted video"))
    assert downloader.refresh_urls() is Refresh.UNCONFIRMED
    assert downloader.refresh_urls() is Refresh.PRIVATE


# --- yt-dlp options ---

def test_user_ytdlp_options_override_scan_defaults():
    config = ConfigHandler(config={"download_options": {"ytdlp_options": '{"sleep_interval_requests": 5}'}})
    assert scan.scan_ytdlp_options(config, common.logger, flat=False)["sleep_interval_requests"] == 5
    assert scan.scan_ytdlp_options(ConfigHandler(config={}), common.logger, flat=False)["sleep_interval_requests"] == 1
    # Flat listings never use the user's options
    assert scan.scan_ytdlp_options(config, common.logger, flat=True)["sleep_interval_requests"] == 1


# --- Shutdown ---

def test_wait_for_jobs_waits_until_threads_stop(monkeypatch):
    import jobs
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, daemon=True)
    thread.start()
    job = jobs.ActiveJob(video_id="dQw4w9WgXcQ", kind="stream", downloader=None, started_at=datetime.now(timezone.utc), thread=thread)
    monkeypatch.setitem(jobs.jobs["stream"], "dQw4w9WgXcQ", job)
    assert jobs.wait_for_jobs(0.1) == 1
    threading.Timer(0.1, stop.set).start()
    assert jobs.wait_for_jobs(5) == 0


def test_gunicorn_config_does_not_load_the_app():
    """The gunicorn master loads gunicorn.conf.py: it must not set up the app's logging (log file) there."""
    import subprocess
    import sys
    code = ("import runpy, sys, logging; runpy.run_path('gunicorn.conf.py'); "
            "print('common' in sys.modules, bool(logging.getLogger('holo').handlers))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert result.stdout.split() == ["False", "False"], result.stderr


def _shutting_down(monkeypatch):
    stopping = threading.Event()
    stopping.set()
    monkeypatch.setattr(common, "kill_all", stopping)


def test_jobs_cancelled_by_shutdown_are_not_saved_to_history(monkeypatch):
    import jobs
    from base_downloader import STATUS_CANCELLED

    class StopsForShutdown:
        status = "Waiting"

        def main(self):
            self.status = STATUS_CANCELLED
    saved = []
    monkeypatch.setattr(jobs, "save_to_history", lambda job: saved.append(job.video_id))
    job = jobs.ActiveJob(video_id="dQw4w9WgXcQ", kind="stream", downloader=StopsForShutdown(), started_at=datetime.now(timezone.utc))
    monkeypatch.setitem(jobs.jobs["stream"], "dQw4w9WgXcQ", job)
    _shutting_down(monkeypatch)
    jobs.run_job(job)
    assert saved == [] and "dQw4w9WgXcQ" not in jobs.jobs["stream"]

    # Finished (or failed) jobs are still saved during shutdown, and a cancel without shutdown too
    job.downloader = StopsForShutdown()
    job.downloader.main = lambda: setattr(job.downloader, "status", "Finished")
    jobs.run_job(job)
    assert saved == ["dQw4w9WgXcQ"]
    monkeypatch.setattr(common, "kill_all", threading.Event())
    job.downloader = StopsForShutdown()
    jobs.run_job(job)
    assert saved == ["dQw4w9WgXcQ", "dQw4w9WgXcQ"]


def test_no_new_jobs_during_shutdown(monkeypatch):
    import jobs
    monkeypatch.setitem(jobs.DOWNLOADER_CLASSES, "stream", lambda video: pytest.fail("nothing is built during shutdown"))
    _shutting_down(monkeypatch)
    assert jobs.start_job(DiscoveredVideo("dQw4w9WgXcQ"), "stream") is False


def test_discord_flush_waits_for_queued_messages(monkeypatch):
    import discord_web
    sent = []

    def slow_send(notification):
        time.sleep(0.2)
        sent.append(notification.status)
    monkeypatch.setattr(discord_web, "send_webhook", slow_send)
    monkeypatch.setattr(discord_web, "_worker", None)
    for status in ("done", "error"):
        discord_web._queue.put_nowait(discord_web._Notification("https://example.invalid", "dQw4w9WgXcQ", status, None, None, None))
    # No sender running yet: flush starts one and waits for both messages
    assert discord_web.flush(5) is True
    assert sent == ["done", "error"]
    assert discord_web.flush(0) is True


def test_discord_sends_queued_messages_on_shutdown(monkeypatch):
    import discord_web
    sent = []
    monkeypatch.setattr(discord_web, "send_webhook", lambda notification: sent.append(notification.status))
    stopping = threading.Event()
    stopping.set()
    monkeypatch.setattr(common, "kill_all", stopping)
    monkeypatch.setattr(discord_web, "SHUTDOWN_SEND_SECONDS", 2)
    for status in ("done", "error"):
        discord_web._queue.put_nowait(discord_web._Notification("https://example.invalid", "dQw4w9WgXcQ", status, None, None, None))
    started = time.monotonic()
    discord_web._worker_loop()
    assert sent == ["done", "error"]
    assert time.monotonic() - started < 5


# --- Shared HTTP client ---

def test_http_clients_per_proxy_and_idle_ones_closed(monkeypatch):
    first = common.get_http_client(None)
    assert common.get_http_client(None) is first
    # Jobs started before a proxy change use the old setting: both clients stay open and are reused
    second = common.get_http_client("http://127.0.0.1:9")
    assert second is not first
    assert common.get_http_client(None) is first and common.get_http_client("http://127.0.0.1:9") is second
    assert not first.is_closed
    # Once the old setting hasn't been used for a while, its client is closed
    monkeypatch.setattr(common, "IDLE_CLIENT_CLOSE_SECONDS", -1)
    assert common.get_http_client("http://127.0.0.1:9") is second
    assert first.is_closed and not second.is_closed


# --- Cookies file ---

def test_cookies_path_that_is_a_directory_is_ignored(tmp_path):
    folder = tmp_path / "cookies.txt"
    folder.mkdir()   # What Docker creates when the mounted file doesn't exist on the host
    assert ConfigHandler(config={"download_options": {"cookies_file": str(folder)}}).get_cookies_file() is None
    cookies = tmp_path / "real_cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    config = ConfigHandler(config={"download_options": {"cookies_file": str(cookies)}})
    assert config.get_cookies_file() == str(cookies)
    assert config.get_community_tab_cookies() == str(cookies)
    # A path that doesn't exist yet is passed on (yt-dlp then has no cookies)
    missing = str(tmp_path / "missing.txt")
    assert ConfigHandler(config={"download_options": {"cookies_file": missing}}).get_cookies_file() == missing


# --- Saving the config ---

def test_failed_config_write_leaves_config_untouched(tmp_path, monkeypatch):
    import builtins
    import getConfig
    path = tmp_path / "config.toml"
    path.write_text("old = 1\n", encoding="utf-8")
    real_open = builtins.open

    class FullDisk:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def write(self, text):
            raise OSError(28, "No space left on device")

    def fake_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        return FullDisk(handle) if str(file).endswith(".tmp") else handle
    monkeypatch.setattr(getConfig, "open", fake_open, raising=False)
    saved, message = getConfig.save_config_text(str(path), "new = 2\n")
    assert saved is False and "No space left" in message
    assert path.read_text(encoding="utf-8") == "old = 1\n"
    assert not (tmp_path / "config.toml.tmp").exists()


def test_config_swap_keeps_file_mode(tmp_path, monkeypatch):
    import stat
    import getConfig
    path = tmp_path / "config.toml"
    path.write_text("old = 1\n", encoding="utf-8")
    original_mode = stat.S_IMODE(os.stat(path).st_mode)
    modes = []
    real_chmod = os.chmod
    monkeypatch.setattr(getConfig.os, "chmod", lambda p, mode: modes.append(mode) or real_chmod(p, mode))
    assert getConfig.save_config_text(str(path), "new = 2\n") == (True, "Config saved")
    assert modes == [original_mode] and stat.S_IMODE(os.stat(path).st_mode) == original_mode


def test_config_of_another_user_is_written_in_place(tmp_path, monkeypatch):
    import getConfig
    path = tmp_path / "config.toml"
    path.write_text("old = 1\n", encoding="utf-8")
    monkeypatch.setattr(getConfig.os, "getuid", lambda: os.stat(path).st_uid + 1, raising=False)
    monkeypatch.setattr(getConfig.os, "replace", lambda *a: pytest.fail("a file of another user must not be replaced"))
    assert getConfig.save_config_text(str(path), "new = 2\n") == (True, "Config saved")
    assert path.read_text(encoding="utf-8") == "new = 2\n"


def test_config_written_in_place_when_temp_file_cannot_be_created(tmp_path, monkeypatch):
    import getConfig
    path = tmp_path / "config.toml"
    path.write_text("old = 1\n", encoding="utf-8")
    real_open = open

    def read_only_folder(file, mode="r", *args, **kwargs):
        if str(file).endswith(".tmp"):
            raise PermissionError(13, "Permission denied")
        return real_open(file, mode, *args, **kwargs)
    monkeypatch.setattr(getConfig, "open", read_only_folder, raising=False)
    assert getConfig.save_config_text(str(path), "new = 2\n") == (True, "Config saved")
    assert path.read_text(encoding="utf-8") == "new = 2\n"


# --- Logging ---

def test_named_loggers_share_one_set_of_handlers(tmp_path):
    config = ConfigHandler(config={"download_options": {"log_file": str(tmp_path / "holo.log"), "log_file_max_size": 1000}})
    try:
        web_logger = common.initialize_logging(config, "web", force=True)
        job_logger = common.initialize_logging(config, "Downloader", video_id="dQw4w9WgXcQ")
        app_logger = logging.getLogger(common.APP_LOGGER_NAME)
        file_handlers = [h for h in app_logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        assert not web_logger.handlers and not job_logger.logger.handlers
        job_logger.warning("hello")
        file_handlers[0].flush()
        assert "[WARNING] [dQw4w9WgXcQ]" in (tmp_path / "holo.log").read_text(encoding="utf-8")
    finally:
        # Back to the test config's console-only logging, releasing the temporary log file
        common.initialize_logging(ConfigHandler(), force=True)


def test_console_logging_kept_until_config_is_readable(monkeypatch):
    app_logger = logging.getLogger(common.APP_LOGGER_NAME)
    monkeypatch.setattr(common, "_logging_state", None)
    try:
        common._configure_app_logger(None, force=False)
        console_handlers = list(app_logger.handlers)
        assert len(console_handlers) == 1
        # Still no readable config: the handlers are not rebuilt on every call
        common._configure_app_logger(None, force=False)
        assert app_logger.handlers == console_handlers
        # A readable config replaces them once
        common._configure_app_logger(ConfigHandler(), force=False)
        assert common._logging_state == "config" and app_logger.handlers != console_handlers
        assert len(app_logger.handlers) == 1
    finally:
        common.initialize_logging(ConfigHandler(), force=True)
