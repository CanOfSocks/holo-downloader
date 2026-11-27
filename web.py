"""
Flask web app. This is the application entry point (gunicorn loads `web:app`; `python web.py`
runs a development server). Importing it starts the scheduler (see the startup section at the end).

The work is done elsewhere:
    jobs.py     active downloads and unarchived monitors (one thread each)
    checks.py   scheduled channel scans and the stall check
    history.py  the history database
This module only shows their state and passes on the actions from the web UI.
"""
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from flask import (Flask, abort, flash, jsonify, make_response, redirect, render_template, request,
                   send_from_directory, url_for)

import checks
import common
import discord_web
import history
import jobs
from base_downloader import STATUS_WAITING
from common import DiscoveredVideo
from getConfig import default_config_path, load_or_create_config, save_config_text
from jobs import ActiveJob
from livestream_dl.getUrls import extract_video_id
from monitor import segment_totals
from unarchived import UnarchivedDownloader

# Browser cache time for static files (favicon etc.)
STATIC_CACHE_SECONDS = 3600

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-placeholder')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = STATIC_CACHE_SECONDS

# Light/dark theme of the web UI ([webui] theme at startup, then the toggle button)
GLOBAL_THEME = "dark"


# --- Formatting helpers ---

def to_utc_iso(value: Any) -> Optional[str]:
    """
    Convert a time to an ISO 8601 UTC string for the browser to show in its own timezone.
    Accepts epoch seconds, datetimes (naive ones are assumed to be UTC) and SQLite
    CURRENT_TIMESTAMP strings ("YYYY-MM-DD HH:MM:SS", which are UTC).
    """
    if value is None or value == "" or value == float('inf'):
        return None
    if isinstance(value, (int, float)):
        moment = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    else:
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def convert_bytes(size: Any) -> str:
    try:
        size = float(size or 0)
    except (TypeError, ValueError):
        return "Invalid Value"
    units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB']
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.2f} {units[unit_index]}"


app.jinja_env.filters['convert_bytes'] = convert_bytes
app.jinja_env.filters['utc_iso'] = to_utc_iso


def snapshot_stats(stats: dict) -> dict:
    """
    Copy of livestream_dl's stats dict, which its download threads change while it is read.
    dict() copies in one step (iterating the live dict could fail with "dictionary changed size
    during iteration"); the nested per-stream dicts are copied the same way.
    """
    return {key: (dict(value) if isinstance(value, dict) else value) for key, value in dict(stats).items()}


def job_view(job: ActiveJob, include_stats: bool = False) -> dict[str, Any]:
    """
    Data shown for an active job in the web UI and the JSON API (plain, JSON serialisable).
    The raw stats are only included for the JSON API (include_stats); the table doesn't use them.
    """
    downloader = job.downloader
    stats = snapshot_stats(downloader.stats)
    downloaded, _ = segment_totals(stats)
    streams = {
        kind: {
            'downloaded': int(stats.get(kind, {}).get('downloaded_segments') or 0),
            'latest': max(int(stats.get(kind, {}).get('latest_sequence') or 0), 0),
        }
        for kind in ("video", "audio") if kind in stats
    }
    view = {
        'id': job.video_id,
        'kind': job.kind,
        'info': downloader.display_info(),
        'status': stats.get('status') or "Unknown",
        'streams': streams,
        'total_size': jobs.stream_total_size(stats),
        'start_time': to_utc_iso(job.started_at),       # When the job was queued
        # Once recording: the stream's own start time if known, otherwise when recording began
        'recording_start': to_utc_iso(downloader.stream_started_at or downloader.recording_started_at),
        'recording_start_source': ('stream' if downloader.stream_started_at
                                   else 'recording' if downloader.recording_started_at else None),
        'scheduled_start': to_utc_iso(downloader.scheduled_start),
        'next_check_at': to_utc_iso(downloader.next_check_at),
        'is_waiting': stats.get('status') == STATUS_WAITING,
        'can_merge': downloader.is_recording() and not downloader.merge_requested,
        'merge_requested': downloader.merge_requested,
        'stalled': job.stall.stalled,
        'stall_reason': job.stall.reason,
        'last_progress': to_utc_iso(job.stall.last_progress_time) if downloaded else None,
    }
    if isinstance(downloader, UnarchivedDownloader):
        view.update({
            'urls_saved_at': to_utc_iso(downloader.json_saved_at),
            'next_url_refresh': to_utc_iso(downloader.next_url_refresh) if downloader.next_url_refresh else None,
            'last_cheap_check': to_utc_iso(downloader.last_cheap_check),
            'cheap_checks_active': downloader.next_cheap_check is not None,
        })
    if include_stats:
        view['stats'] = stats
    return view


def jobs_data(kind: str, include_stats: bool = False) -> list[dict[str, Any]]:
    return [job_view(job, include_stats) for job in sorted(jobs.snapshot_jobs(kind), key=lambda j: j.started_at)]


def schedule_data() -> list[dict[str, Any]]:
    """Every check with its schedule, with the next run time as an ISO string."""
    return [{**row, 'next_run': to_utc_iso(row['next_run'])} for row in checks.schedule_rows()]


def action_response(message: str, level: str = "success"):
    """
    Response for an action button. htmx requests get an empty response with an HX-Trigger header
    (shows the message and refreshes the tables without reloading the page); plain form posts
    (no JavaScript) get the usual flash message and redirect.
    """
    if request.headers.get("HX-Request"):
        response = make_response("", 204)
        response.headers["HX-Trigger"] = json.dumps({"showMessage": {"text": message, "level": level}, "refreshTables": True})
        return response
    flash(message, level)
    return redirect(url_for('index'))


@app.after_request
def set_cache_headers(response):
    # Polled data and actions must never come from a cache. Static files use SEND_FILE_MAX_AGE_DEFAULT.
    if request.path.startswith(('/data/', '/api/', '/actions/')):
        response.headers['Cache-Control'] = 'no-store'
    return response


# --- HTMX Data Routes ---

def _render_jobs_table(kind: str):
    response = make_response(render_template('downloads_table.html', jobs=jobs_data(kind), kind=kind))
    # The page sends the version of the history table it shows (see index.html). If a job has
    # finished since, htmx is told to fire 'historyUpdated', which reloads the history table once.
    shown = request.args.get('history_version')
    if shown and shown != str(history.version()):
        response.headers['HX-Trigger'] = 'historyUpdated'
    return response


@app.route('/data/active')
def data_active():
    return _render_jobs_table(jobs.KIND_STREAM)


@app.route('/data/unarchived')
def data_unarchived():
    return _render_jobs_table(jobs.KIND_UNARCHIVED)


@app.route('/data/history')
def data_history():
    # Version read before the query: a change in between at worst causes one extra reload
    version = history.version()
    response = make_response(render_template('history_table.html', history=history.get_all()))
    response.headers['X-History-Version'] = str(version)
    return response


@app.route('/data/scheduler')
def data_scheduler():
    return render_template('schedule_table.html', jobs=schedule_data())


# --- Web Routes ---

@app.route('/')
def index():
    return render_template('index.html', theme=GLOBAL_THEME)


@app.route('/actions/add', methods=['POST'])
def manual_add():
    raw = str(request.form.get('video_id') or "")
    video_id = extract_video_id(raw)
    if not video_id:
        return action_response(f"'{raw.strip()}' is not a YouTube video ID or URL", "warning")
    if jobs.start_job(DiscoveredVideo(video_id, manual=True), jobs.KIND_STREAM):
        return action_response(f"Started download for {video_id}")
    return action_response(f"Video {video_id} is already being downloaded.", "warning")


@app.route('/actions/<action>/<kind>/<video_id>', methods=['POST'])
def job_action(action: str, kind: str, video_id: str):
    """cancel, merge (stop & merge) or check (check now) for an active job."""
    if kind not in jobs.DOWNLOADER_CLASSES or action not in ("cancel", "merge", "check"):
        abort(404)
    job = jobs.get_job(kind, video_id)
    if job is None:
        return action_response(f"{video_id} is not currently active.", "secondary")
    if action == "cancel":
        job.downloader.cancel()
        return action_response(f"Cancelling {video_id}")
    if action == "merge":
        if job.downloader.stop_and_merge():
            return action_response(f"Stopping {video_id} and merging the downloaded segments")
        return action_response(f"{video_id} is not recording, nothing to merge", "warning")
    job.downloader.check_now()
    return action_response(f"Checking {video_id} now")


@app.route('/actions/force_run/<job_name>', methods=['POST'])
def force_run_job(job_name: str):
    """Run a check now, in the background."""
    name = job_name.removesuffix("-checker")
    if name not in checks.CHECKS:
        return action_response(f"Unknown job: {job_name}", "danger")
    display = checks.CHECKS[name].display
    if checks.is_running(name):
        return action_response(f"{display} is already running, please wait for it to finish", "warning")
    checks.run_check_now(name)
    return action_response(f"{display} started", "info")


@app.route('/actions/check', methods=['POST'])
def manual_check():
    """Older endpoint: run the stream check now."""
    return force_run_job(checks.job_id("streams"))


@app.route('/actions/toggle_theme', methods=['POST'])
def toggle_theme():
    global GLOBAL_THEME
    GLOBAL_THEME = "dark" if GLOBAL_THEME == "light" else "light"
    return redirect(request.referrer or url_for('index'))


@app.route('/config', methods=['GET', 'POST'])
def config_page():
    if request.method == 'POST':
        saved, message = save_config_text(default_config_path(), request.form.get('toml_content') or "")
        if not saved:
            flash(message, "danger")
        else:
            success, schedule_message = checks.update_scheduler()
            flash(f"{message}. {schedule_message}", "success" if success else "warning")
        return redirect(url_for('config_page'))

    with open(default_config_path(), 'r', encoding="utf-8") as f:
        content = f.read()
    return render_template('config.html', config_content=content, theme=GLOBAL_THEME)


# --- JSON API Endpoints ---

@app.route('/api/active')
def api_active():
    return jsonify(jobs_data(jobs.KIND_STREAM, include_stats=True))


@app.route('/api/unarchived')
def api_unarchived():
    return jsonify(jobs_data(jobs.KIND_UNARCHIVED, include_stats=True))


@app.route('/api/history')
def api_history():
    return jsonify([{**dict(row), 'timestamp': to_utc_iso(row['timestamp'])} for row in history.get_all()])


@app.route('/api/scheduler')
def api_scheduler():
    return jsonify(schedule_data())


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico')


# ---------------------------------------------------------
# Startup (runs under gunicorn and with `python web.py`)
# ---------------------------------------------------------
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Run the holo-downloader web app (development server)")
    parser.add_argument('--config', type=str, default=default_config_path())
    args = parser.parse_args()
    # Every module reads the config path from CONFIG_FILE (see getConfig.default_config_path)
    os.environ["CONFIG_FILE"] = args.config
    # Logging was set up from the default path when the modules were imported: use this config's settings
    common.initialize_logging(force=True)
    print("Running in Developer Mode")

common.setup_umask()
_startup_config = load_or_create_config(default_config_path())
GLOBAL_THEME = _startup_config.get_webui_theme()
history.init_db()
checks.update_scheduler()
checks.scheduler.start()
discord_web.notify("0", "starting", config=_startup_config)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, load_dotenv=True)
