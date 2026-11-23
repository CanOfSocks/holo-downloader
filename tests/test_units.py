import threading
import time
from datetime import datetime, timezone

import pytest

import common
import yt_dlp

from base_downloader import LATE_BACKOFF_DIVISOR, describe_error, next_upcoming_check_delay
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
    monkeypatch.setattr(common, "YoutubeDL", FakeYoutubeDL)
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
    monkeypatch.setattr(common, "extract_video", lambda *a, **k: {"description": "Gaming"})
    monkeypatch.setattr(getMembers.kill_all, "wait", lambda *a, **k: False)
    config = ConfigHandler(config={"members_only": {"filtered": channel},
                                   "title_filter": {channel: "(?i)karaoke"}})

    # The same video is filtered out of a normal scan...
    assert common.get_upcoming_or_live_videos(channel, config, tab="membership") == []

    class Output(list):
        put = list.append

    found = Output()
    getMembers.main(found, config=config)
    assert [video.id for video in found] == ["members0001"]


def test_members_playlist_id_from_ids():
    config = ConfigHandler(config={})
    for value in ("UC", "UU", "UUMO", "UULF", "UUSH"):
        assert common.members_playlist_id(value + MEMBERS_SUFFIX, config) == "UUMO" + MEMBERS_SUFFIX


def test_members_playlist_id_resolves_handle_once(fake_ytdlp):
    config = ConfigHandler(config={})
    fake_ytdlp.results["https://www.youtube.com/@testhandle"] = {"channel_id": "UC" + MEMBERS_SUFFIX, "entries": []}
    assert common.members_playlist_id("@testhandle", config) == "UUMO" + MEMBERS_SUFFIX
    assert common.members_playlist_id("@testhandle", config) == "UUMO" + MEMBERS_SUFFIX
    assert fake_ytdlp.requested == ["https://www.youtube.com/@testhandle"]


def test_members_playlist_id_unknown_channel(fake_ytdlp):
    fake_ytdlp.results["https://www.youtube.com/@missing"] = {"entries": []}
    with pytest.raises(ValueError):
        common.members_playlist_id("@missing", ConfigHandler(config={}))


def test_members_scan_uses_uumo_for_handles(fake_ytdlp):
    config = ConfigHandler(config={})
    fake_ytdlp.results["https://www.youtube.com/@scanhandle"] = {"channel_id": "UC" + MEMBERS_SUFFIX}
    fake_ytdlp.results["https://www.youtube.com/playlist?list=UUMO" + MEMBERS_SUFFIX] = {"entries": []}
    assert common.get_upcoming_or_live_videos("@scanhandle", config, tab="membership", apply_filters=False) == []
    assert fake_ytdlp.requested[-1] == "https://www.youtube.com/playlist?list=UUMO" + MEMBERS_SUFFIX


def test_community_tab_uses_only_its_own_list(monkeypatch, tmp_path):
    """Only [community_tab] channels are fetched, and filters are never involved."""
    import communityPosts

    commands = []
    monkeypatch.setattr(communityPosts, "run_until_done", lambda command, *a, **k: commands.append(command))
    # Independent of any other test (or shutdown) having set the global stop flag
    monkeypatch.setattr(communityPosts.kill_all, "is_set", lambda: False)
    for name in ("filtering", "titleFilter", "descriptionFilter", "extract_video"):
        monkeypatch.setattr(common, name, lambda *a, **k: pytest.fail("community tab must not use filters"))
    config = ConfigHandler(config={
        "channel_ids_to_match": {"normal": "UCnormal"},
        "members_only": {"members": "UCmembers"},
        "community_tab": {"posts": "UCposts"},
        "title_filter": {"UCposts": "(?i)karaoke"},
        "description_filter": {"UCposts": "(?i)singing"},
        "community_tab_options": {"community_dir": str(tmp_path)},
    })
    communityPosts.main(config=config)
    assert [command[-1] for command in commands] == ["https://www.youtube.com/channel/UCposts/posts"]


def test_playlist_url():
    channel = "UC" + MEMBERS_SUFFIX
    assert common.playlist_url(channel, "streams") == ("https://www.youtube.com/playlist?list=UU" + MEMBERS_SUFFIX, True)
    assert common.playlist_url(channel, "membership") == ("https://www.youtube.com/playlist?list=UUMO" + MEMBERS_SUFFIX, True)
    assert common.playlist_url("UULF" + MEMBERS_SUFFIX, "membership") == ("https://www.youtube.com/playlist?list=UUMO" + MEMBERS_SUFFIX, True)
    assert common.playlist_url("UUMO" + MEMBERS_SUFFIX, "streams") == ("https://www.youtube.com/playlist?list=UUMO" + MEMBERS_SUFFIX, True)
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
    import lockup_schedule  # noqa: F401 (installed by common)
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
    assert common.known_schedules.get(downloader.id) == downloader.scheduled_start
