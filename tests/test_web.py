"""Web app routes and templates, with fake jobs (no network or real downloads)."""
import time
from datetime import datetime, timezone

import pytest

import checks
import history
import jobs
import web
from common import DiscoveredVideo
from getConfig import default_config_path
from livestream_dl import getUrls
from downloadVid import VideoDownloader
from unarchived import UnarchivedDownloader


@pytest.fixture
def client():
    web.app.config["TESTING"] = True
    with web.app.test_client() as test_client:
        yield test_client
    with jobs.LOCK:
        for kind_jobs in jobs.jobs.values():
            kind_jobs.clear()


def add_fake_job(kind: str, video_id: str, status: str, downloader_cls) -> jobs.ActiveJob:
    downloader = downloader_cls(DiscoveredVideo(video_id))
    downloader.set_status(status)
    downloader.stats["video"] = {"downloaded_segments": 5, "latest_sequence": 9, "current_filesize": 1024}
    downloader.stats["audio"] = {"downloaded_segments": 5, "latest_sequence": 9, "current_filesize": 1024}
    job = jobs.ActiveJob(video_id=video_id, kind=kind, downloader=downloader, started_at=datetime.now(timezone.utc))
    with jobs.LOCK:
        jobs.jobs[kind][video_id] = job
    return job


def test_to_utc_iso():
    assert web.to_utc_iso("2026-01-02 03:04:05") == "2026-01-02T03:04:05Z"
    assert web.to_utc_iso(0) == "1970-01-01T00:00:00Z"
    assert web.to_utc_iso(None) is None
    assert web.to_utc_iso(float("inf")) is None


def test_index_and_tables_render(client):
    add_fake_job("stream", "dQw4w9WgXcQ", "Recording", VideoDownloader)
    add_fake_job("unarchived", "jNQXAC9IVRw", "Monitoring", UnarchivedDownloader)
    assert client.get("/").status_code == 200

    active = client.get("/data/active")
    assert active.status_code == 200
    body = active.get_data(as_text=True)
    assert "dQw4w9WgXcQ" in body and "/actions/merge/stream/dQw4w9WgXcQ" in body
    assert active.headers["Cache-Control"] == "no-store"

    unarchived = client.get("/data/unarchived").get_data(as_text=True)
    assert "jNQXAC9IVRw" in unarchived
    assert client.get("/data/history").status_code == 200
    assert client.get("/data/scheduler").status_code == 200
    assert client.get("/config").status_code == 200


def test_stalled_job_highlights_merge(client):
    job = add_fake_job("stream", "dQw4w9WgXcQ", "Recording", VideoDownloader)
    job.stall.update("Recording", job.downloader.stats, 30.0, 60, now_monotonic=time.monotonic())
    job.stall.update("Recording", job.downloader.stats, 30.0, 60, now_monotonic=time.monotonic() + 120)
    body = client.get("/data/active").get_data(as_text=True)
    assert "Stalled" in body and "btn-warning" in body


def test_history_shows_error_reason(client):
    history.init_db()
    job = add_fake_job("stream", "M7lc1UVf-VE", "Recording", VideoDownloader)
    job.downloader.set_error(getUrls.VideoInaccessibleError("Video M7lc1UVf-VE is a membership or premium video"))
    jobs.save_to_history(job)
    finished = add_fake_job("stream", "dQw4w9WgXcQ", "Finished", VideoDownloader)
    finished.downloader.error_reason = "Private"    # From an earlier attempt: not shown for a finished download
    jobs.save_to_history(finished)

    body = client.get("/data/history").get_data(as_text=True)
    assert "Membership" in body and "Private" not in body
    rows = {row["video_id"]: row for row in client.get("/api/history").get_json()}
    assert rows["M7lc1UVf-VE"]["status"] == "Error" and rows["M7lc1UVf-VE"]["reason"] == "Membership"
    assert rows["dQw4w9WgXcQ"]["reason"] is None


def test_api_active_includes_stall_fields(client):
    add_fake_job("stream", "dQw4w9WgXcQ", "Recording", VideoDownloader)
    data = client.get("/api/active").get_json()
    assert data[0]["id"] == "dQw4w9WgXcQ"
    assert data[0]["start_time"].endswith("Z")
    assert {"stalled", "stall_reason", "can_merge", "last_progress"} <= set(data[0])
    assert data[0]["stats"]["video"]["downloaded_segments"] == 5


def test_job_view_copies_stats_and_only_api_gets_them(client):
    job = add_fake_job("stream", "dQw4w9WgXcQ", "Recording", VideoDownloader)
    assert "stats" not in web.job_view(job)
    view = web.job_view(job, include_stats=True)
    view["stats"]["video"]["downloaded_segments"] = 999
    assert job.downloader.stats["video"]["downloaded_segments"] == 5


def test_config_save_replaces_file_or_writes_in_place(monkeypatch, tmp_path):
    import getConfig
    path = tmp_path / "config.toml"
    path.write_text("old = 1\n", encoding="utf-8")
    assert getConfig.save_config_text(str(path), "new = 2\n") == (True, "Config saved")
    assert path.read_text(encoding="utf-8") == "new = 2\n"

    # A config mounted into Docker as a single file can't be replaced: written in place instead
    def busy(*args):
        raise OSError(16, "Device or resource busy")
    monkeypatch.setattr(getConfig.os, "replace", busy)
    assert getConfig.save_config_text(str(path), "newer = 3\n")[0] is True
    assert path.read_text(encoding="utf-8") == "newer = 3\n"
    assert not (tmp_path / "config.toml.tmp").exists()


def test_started_shows_stream_start_once_recording(client):
    job = add_fake_job("stream", "dQw4w9WgXcQ", "Waiting", VideoDownloader)
    data = client.get("/api/active").get_json()[0]
    assert data["recording_start"] is None and data["recording_start_source"] is None
    assert "Stream start time" not in client.get("/data/active").get_data(as_text=True)

    job.downloader.start_recording({"release_timestamp": 1_700_000_000})
    data = client.get("/api/active").get_json()[0]
    assert data["recording_start"] == "2023-11-14T22:13:20Z" and data["recording_start_source"] == "stream"
    body = client.get("/data/active").get_data(as_text=True)
    assert "Stream start time" in body and 'datetime="2023-11-14T22:13:20Z"' in body

    # No stream start reported (or a scheduled time in the future): the time recording began
    other = add_fake_job("stream", "jNQXAC9IVRw", "Waiting", VideoDownloader)
    other.downloader.start_recording({"release_timestamp": time.time() + 3600})
    row = {r["id"]: r for r in client.get("/api/active").get_json()}["jNQXAC9IVRw"]
    assert row["recording_start_source"] == "recording" and row["recording_start"].endswith("Z")


def test_merge_action_sets_graceful_stop(client):
    job = add_fake_job("stream", "dQw4w9WgXcQ", "Recording", VideoDownloader)
    response = client.post("/actions/merge/stream/dQw4w9WgXcQ", headers={"HX-Request": "true"})
    assert response.status_code == 204
    assert "showMessage" in response.headers["HX-Trigger"]
    assert job.downloader.livestream_downloader.graceful_stop.is_set()


def test_merge_refused_when_not_recording(client):
    job = add_fake_job("stream", "dQw4w9WgXcQ", "Waiting", VideoDownloader)
    client.post("/actions/merge/stream/dQw4w9WgXcQ", headers={"HX-Request": "true"})
    assert not job.downloader.livestream_downloader.graceful_stop.is_set()


def test_cancel_and_check_now_actions(client):
    job = add_fake_job("stream", "dQw4w9WgXcQ", "Waiting", VideoDownloader)
    client.post("/actions/check/stream/dQw4w9WgXcQ", headers={"HX-Request": "true"})
    assert job.downloader.wake.is_set()
    # Without htmx the form posts get a redirect (works without JavaScript)
    assert client.post("/actions/cancel/stream/dQw4w9WgXcQ").status_code == 302
    assert job.downloader.kill_this.is_set()


def test_unknown_action_is_404(client):
    assert client.post("/actions/explode/stream/dQw4w9WgXcQ").status_code == 404


def test_invalid_config_is_not_saved(client):
    with open(default_config_path(), encoding="utf-8") as f:
        before = f.read()
    response = client.post("/config", data={"toml_content": "this is [not toml"}, follow_redirects=True)
    assert "Invalid TOML" in response.get_data(as_text=True)
    with open(default_config_path(), encoding="utf-8") as f:
        assert f.read() == before


def test_scheduler_shows_configured_cron(client, monkeypatch):
    original = default_config_path()
    with open(original, encoding="utf-8") as f:
        content = f.read()
    test_config = original + ".cron.toml"
    with open(test_config, "w", encoding="utf-8") as f:
        f.write(content.replace("[cron_schedule]", '[cron_schedule]\nstreams = "*/30 * * * *"\nunarchived = "not a cron"'))
    monkeypatch.setenv("CONFIG_FILE", test_config)
    try:
        checks.update_scheduler()
        rows = {row["id"]: row for row in client.get("/api/scheduler").get_json()}
        assert rows["streams-checker"]["cron"] == "*/30 * * * *" and rows["streams-checker"]["status"] == "Scheduled"
        assert rows["streams-checker"]["next_run"].endswith("Z")
        assert rows["unarchived-checker"]["status"] == "Invalid"
        assert rows["members_only-checker"]["cron"] is None
        body = client.get("/data/scheduler").get_data(as_text=True)
        assert "*/30 * * * *" in body and "Invalid schedule" in body
    finally:
        monkeypatch.setenv("CONFIG_FILE", original)
        checks.update_scheduler()


def test_static_files_are_cacheable(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert "max-age=3600" in response.headers["Cache-Control"]


def test_start_job_builds_downloader_outside_lock_and_updates_existing(client, monkeypatch):
    started = []
    monkeypatch.setattr(jobs.threading.Thread, "start", lambda self: started.append(self.name))

    class CheckLock(VideoDownloader):
        def __init__(self, video):
            assert not jobs.LOCK.locked(), "the downloader must be built without holding LOCK"
            super().__init__(video)
    monkeypatch.setitem(jobs.DOWNLOADER_CLASSES, "stream", CheckLock)

    assert jobs.start_job(DiscoveredVideo("dQw4w9WgXcQ"), "stream") is True
    assert started == ["stream-dQw4w9WgXcQ"]
    # Found again by a scan: the running job gets the update and nothing new starts
    assert jobs.start_job(DiscoveredVideo("dQw4w9WgXcQ", live_status="is_live"), "stream") is False
    assert jobs.get_job("stream", "dQw4w9WgXcQ").downloader.hinted_live_status == "is_live"
    assert started == ["stream-dQw4w9WgXcQ"]


def test_polled_tables_send_history_version_without_passing_it_to_actions(client):
    body = client.get("/").get_data(as_text=True)
    assert body.count("hx-vals='js:{history_version") == 2
    # The pollers are separate elements targeting the table containers, so the action forms
    # swapped into the containers have no ancestor with hx-vals
    assert 'hx-target="#active-downloads-container"' in body and 'hx-target="#unarchive-monitor-container"' in body
    for container in ('id="active-downloads-container">', 'id="unarchive-monitor-container">'):
        start = body.index(container)
        assert "hx-vals" not in body[body.rindex("<div", 0, start):body.index("</div>", start)]


def test_history_reloaded_only_when_it_changed(client):
    history.init_db()
    shown = client.get("/data/history").headers["X-History-Version"]
    # Up to date, or the page hasn't loaded the history yet: no reload
    assert "HX-Trigger" not in client.get(f"/data/active?history_version={shown}").headers
    assert "HX-Trigger" not in client.get("/data/active?history_version=").headers
    # A job finishes: the page showing the old version reloads the history, once it has the new one it stops
    jobs.save_to_history(add_fake_job("stream", "dQw4w9WgXcQ", "Finished", VideoDownloader))
    assert client.get(f"/data/active?history_version={shown}").headers["HX-Trigger"] == "historyUpdated"
    assert client.get(f"/data/unarchived?history_version={shown}").headers["HX-Trigger"] == "historyUpdated"
    current = client.get("/data/history").headers["X-History-Version"]
    assert current != shown
    assert "HX-Trigger" not in client.get(f"/data/active?history_version={current}").headers


def test_add_rejects_invalid_id(client):
    response = client.post("/actions/add", data={"video_id": "not a video"}, headers={"HX-Request": "true"})
    assert "warning" in response.headers["HX-Trigger"]
    assert not jobs.jobs["stream"]
