"""Web app routes and templates, with fake jobs (no network or real downloads)."""
import time
from datetime import datetime, timezone

import pytest

import web
from common import DiscoveredVideo
from downloadVid import VideoDownloader
from unarchived import UnarchivedDownloader


@pytest.fixture
def client():
    web.app.config["TESTING"] = True
    with web.app.test_client() as test_client:
        yield test_client
    with web.LOCK:
        for kind_jobs in web.jobs.values():
            kind_jobs.clear()


def add_fake_job(kind: str, video_id: str, status: str, downloader_cls) -> web.ActiveJob:
    downloader = downloader_cls(DiscoveredVideo(video_id))
    downloader.set_status(status)
    downloader.stats["video"] = {"downloaded_segments": 5, "latest_sequence": 9, "current_filesize": 1024}
    downloader.stats["audio"] = {"downloaded_segments": 5, "latest_sequence": 9, "current_filesize": 1024}
    job = web.ActiveJob(video_id=video_id, kind=kind, downloader=downloader, started_at=datetime.now(timezone.utc))
    with web.LOCK:
        web.jobs[kind][video_id] = job
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


def test_api_active_includes_stall_fields(client):
    add_fake_job("stream", "dQw4w9WgXcQ", "Recording", VideoDownloader)
    data = client.get("/api/active").get_json()
    assert data[0]["id"] == "dQw4w9WgXcQ"
    assert data[0]["start_time"].endswith("Z")
    assert {"stalled", "stall_reason", "can_merge", "last_progress"} <= set(data[0])


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
    with open(web.config_file_path, encoding="utf-8") as f:
        before = f.read()
    response = client.post("/config", data={"toml_content": "this is [not toml"}, follow_redirects=True)
    assert "Invalid TOML" in response.get_data(as_text=True)
    with open(web.config_file_path, encoding="utf-8") as f:
        assert f.read() == before


def test_static_files_are_cacheable(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert "max-age=3600" in response.headers["Cache-Control"]


def test_add_rejects_invalid_id(client):
    response = client.post("/actions/add", data={"video_id": "not a video"}, headers={"HX-Request": "true"})
    assert "warning" in response.headers["HX-Trigger"]
    assert not web.jobs["stream"]
