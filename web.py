"""
Flask web app and scheduler. This is the application entry point (gunicorn loads `web:app`;
`python web.py` runs a development server).

Downloads run in background threads. Each one is tracked as an ActiveJob in `jobs` until its
thread finishes, when the result is written to the history database.
"""
import json
import os
import queue
import random
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Union

import tomlkit
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import (Flask, abort, flash, jsonify, make_response, redirect, render_template, request,
                   send_from_directory, url_for)

import common
import communityPosts
import discord_web
import getMembers
import getVids
from base_downloader import STATUS_WAITING, BaseDownloader
from common import DiscoveredVideo, kill_all
from downloadVid import VideoDownloader
from getConfig import ConfigHandler, default_config_path
from livestream_dl.getUrls import extract_video_id
from monitor import StallTracker, segment_totals
from unarchived import UnarchivedDownloader

# Smaller thread stacks (default is often 8 MB) as there is a thread per download.
# This applies to all threads created after this point.
try:
    threading.stack_size(1024 * 1024)
except ValueError:
    pass  # Some platforms have strict page size requirements

# --- Configuration & Constants ---
DB_FILE = os.environ.get('HISTORY_DB', 'stream_history.db')
HISTORY_LIMIT = 50
# Browser cache time for static files (favicon etc.)
STATIC_CACHE_SECONDS = 3600
# How often active downloads are sampled for stall detection
STALL_CHECK_SECONDS = 30
# A finished download makes the active table response tell browsers to reload the history for
# this many seconds. Longer than the 3s active table poll, so every open browser sees it.
HISTORY_SIGNAL_SECONDS = 10

config_file_path: str = default_config_path()
LOCK = threading.Lock()   # Protects `jobs`
scheduler = BackgroundScheduler(daemon=True)
logger = common.initialize_logging(logger_name="web")

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-placeholder')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = STATIC_CACHE_SECONDS

GLOBAL_THEME = "dark"
_history_changed_at: float = 0.0


# --- Active jobs ---

@dataclass
class ActiveJob:
    video_id: str
    kind: str                       # "stream" or "unarchived"
    downloader: BaseDownloader
    started_at: datetime            # UTC
    thread: Optional[threading.Thread] = None   # Set by start_job (the thread needs the job as its argument)
    stall: StallTracker = field(default_factory=StallTracker)


DOWNLOADER_CLASSES: dict[str, type[BaseDownloader]] = {
    "stream": VideoDownloader,
    "unarchived": UnarchivedDownloader,
}
jobs: dict[str, dict[str, ActiveJob]] = {kind: {} for kind in DOWNLOADER_CLASSES}


def snapshot_jobs(kind: Optional[str] = None) -> list[ActiveJob]:
    with LOCK:
        kinds = [kind] if kind else list(jobs)
        return [job for k in kinds for job in jobs[k].values()]


def get_job(kind: str, video_id: str) -> Optional[ActiveJob]:
    with LOCK:
        return jobs.get(kind, {}).get(video_id)


# --- Database Management ---

def db_connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_FILE, timeout=10)


def init_db() -> None:
    # closing() closes the connection; "with conn" on its own only commits
    with closing(db_connect()) as conn, conn:
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id VARCHAR(11),
                type VARCHAR(20),
                status VARCHAR(20),
                total_size INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                reason TEXT
            )
        ''')
        # Databases created before the reason column was added
        columns = {row[1] for row in conn.execute('PRAGMA table_info(history)')}
        if 'reason' not in columns:
            conn.execute('ALTER TABLE history ADD COLUMN reason TEXT')


def save_to_history(job: ActiveJob) -> None:
    """Save a finished job and keep only the last HISTORY_LIMIT entries."""
    downloader = job.downloader
    stats = downloader.stats
    total_size = sum(int(stats.get(kind, {}).get("current_filesize") or 0) for kind in ("video", "audio"))
    # Brief reason for an error (e.g. "Membership", "Private", "Processed"), or missing segments of a finished download
    reason = downloader.history_reason()
    with closing(db_connect()) as conn, conn:
        conn.execute('INSERT INTO history (video_id, type, total_size, status, reason) VALUES (?, ?, ?, ?, ?)',
                     (job.video_id, job.kind, total_size, downloader.status, reason))
        conn.execute('DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT ?)', (HISTORY_LIMIT,))


def get_history() -> list[sqlite3.Row]:
    with closing(db_connect()) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute('SELECT * FROM history ORDER BY id DESC').fetchall()


def mark_history_changed() -> None:
    global _history_changed_at
    _history_changed_at = time.time()


# --- Config Management ---

def load_config() -> ConfigHandler:
    if not os.path.exists(config_file_path):
        doc = tomlkit.document()
        schedule = tomlkit.table()
        schedule.add(tomlkit.comment("Format: Minute Hour Day_of_Month Month Day_of_Week (standard 5 fields)"))
        schedule.add("streams", "*/30 * * * *")
        doc.add("cron_schedule", schedule)
        with open(config_file_path, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))
    return ConfigHandler(config_file=config_file_path)


def save_config(content: str) -> tuple[bool, str]:
    try:
        tomlkit.parse(content)
    except Exception as e:
        return False, f"Invalid TOML, configuration not saved: {e}"
    with open(config_file_path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return True, "Config saved"


# --- Starting and running downloads ---

def run_job(job: ActiveJob) -> None:
    """Thread target for a download. Always records the result in history and removes the job."""
    try:
        job.downloader.main()
    except Exception as e:
        logger.exception(f"Error downloading {job.video_id}")
        job.downloader.set_error(e)
    finally:
        try:
            save_to_history(job)
        except Exception:
            logger.exception("Unable to save {0} to history".format(job.video_id))
        mark_history_changed()
        with LOCK:
            jobs[job.kind].pop(job.video_id, None)


def start_job(video: Union[DiscoveredVideo, str], kind: str) -> bool:
    """
    Start a download thread for the video unless one is already running. If it is, the channel
    scan's information is passed to it instead (e.g. so a waiting job starts when the stream goes live).
    Returns True if a new download was started.
    """
    if isinstance(video, str):
        video = DiscoveredVideo(video)
    with LOCK:
        existing = jobs[kind].get(video.id)
        if existing is not None:
            existing.downloader.apply_discovery_hint(video)
            return False
        # The constructor doesn't make network requests, so holding the lock here is quick
        downloader = DOWNLOADER_CLASSES[kind](video)
        job = ActiveJob(video_id=video.id, kind=kind, downloader=downloader, started_at=datetime.now(timezone.utc))
        job.thread = threading.Thread(target=run_job, args=(job,), name=f"{kind}-{video.id}", daemon=True)
        jobs[kind][video.id] = job
    job.thread.start()
    return True


def start_download(video: Union[DiscoveredVideo, str]) -> bool:
    return start_job(video, "stream")


def start_unarchived_download(video: Union[DiscoveredVideo, str]) -> bool:
    return start_job(video, "unarchived")


# --- Scheduled checks ---

def get_videos_with_queue(discovery_func: Callable, kind: str, **kwargs) -> None:
    """
    Run a channel scan in a background thread and start downloads as videos are found, rather
    than waiting for every channel to be scanned. New downloads are spread out by 5-10 seconds.
    """
    found: "queue.Queue[DiscoveredVideo]" = queue.Queue()
    active_ids = {job.video_id for job in snapshot_jobs(kind)}

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
            started = start_job(video, kind)
        except Exception:
            logger.exception("Unable to start download of {0}".format(video.id))
            continue
        # Only new downloads are spread out (existing ones were just updated). kill_all.wait()
        # returns True if the application is shutting down.
        if started and kill_all.wait(random.uniform(5.0, 10.0)):
            break


@dataclass(frozen=True)
class Check:
    display: str
    func: Callable[[], None]


CHECKS: dict[str, Check] = {
    "streams": Check("Stream Checker", lambda: get_videos_with_queue(getVids.main, "stream", unarchived=False)),
    "unarchived": Check("Unarchived Checker", lambda: get_videos_with_queue(getVids.main, "unarchived", unarchived=True)),
    "members_only": Check("Members Only Checker", lambda: get_videos_with_queue(getMembers.main, "stream")),
    "community_posts": Check("Community Tab Checker", lambda: communityPosts.main()),
}
# One lock per check so a manual "Run now" and the scheduled run never overlap (which would
# double the requests to YouTube)
_check_locks: dict[str, threading.Lock] = {name: threading.Lock() for name in CHECKS}


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


def check_stalls() -> None:
    """Sample every active download, notify about new stalls and auto stop & merge if configured."""
    try:
        config = ConfigHandler(config_file=config_file_path)
    except Exception:
        logger.exception("Unable to read config for stall check")
        return
    stall_timeout = config.get_stall_timeout()
    auto_merge = config.get_stall_auto_merge()

    for job in snapshot_jobs():
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


# The cron expression from the config for each check, as written there, for display.
# (APScheduler's own description of a trigger is not in cron syntax.)
_configured_crons: dict[str, str] = {}
# Checks whose configured cron expression could not be parsed
_invalid_crons: set[str] = set()


def update_scheduler() -> tuple[bool, str]:
    config = load_config()
    messages = []
    _configured_crons.clear()
    _invalid_crons.clear()
    for name in CHECKS:
        job_id = f"{name}-checker"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        cron_string = config.get_cron_schedule(name)
        if not cron_string:
            continue
        cron_string = str(cron_string).strip()
        _configured_crons[name] = cron_string
        try:
            scheduler.add_job(run_check, trigger=CronTrigger.from_crontab(cron_string), args=[name], id=job_id,
                              max_instances=1,       # Prevent overlapping runs of the scheduled job
                              coalesce=True,         # If runs were missed, run once only
                              replace_existing=True)
        except ValueError as e:
            logger.error(f"Cron Error for {name}: {e}")
            _invalid_crons.add(name)
            messages.append(f"Invalid cron expression for {name}: {e}")
    scheduler.add_job(check_stalls, trigger='interval', seconds=STALL_CHECK_SECONDS, id="stall-checker",
                      max_instances=1, coalesce=True, replace_existing=True)
    if messages:
        return False, "; ".join(messages)
    return True, "All valid schedules loaded"


def get_scheduler_jobs() -> list[dict[str, Any]]:
    """Every check with its schedule status."""
    scheduled = {job.id: job for job in scheduler.get_jobs()}
    rows = []
    for name, check in CHECKS.items():
        job = scheduled.get(f"{name}-checker")
        if job:
            status = "Scheduled"
        elif name in _invalid_crons:
            status = "Invalid"
        else:
            status = "Inactive"
        rows.append({
            'id': f"{name}-checker",
            'name': check.display,
            'cron': _configured_crons.get(name),   # As written in [cron_schedule]
            'trigger': str(job.trigger) if job else "No Schedule Set",
            'next_run': to_utc_iso(job.next_run_time) if job and job.next_run_time else None,
            'status': status,
            'running': _check_locks[name].locked(),
        })
    return rows


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


def job_view(job: ActiveJob) -> dict[str, Any]:
    """Data shown for an active job in the web UI and the JSON API (plain, JSON serialisable)."""
    downloader = job.downloader
    stats = downloader.stats
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
        'status': downloader.status,
        'stats': {key: (dict(value) if isinstance(value, dict) else value) for key, value in stats.items()},
        'streams': streams,
        'total_size': sum(int(stats.get(kind, {}).get('current_filesize') or 0) for kind in ("video", "audio")),
        'start_time': to_utc_iso(job.started_at),
        'scheduled_start': to_utc_iso(downloader.scheduled_start),
        'next_check_at': to_utc_iso(downloader.next_check_at),
        'is_waiting': downloader.status == STATUS_WAITING,
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
    return view


def jobs_data(kind: str) -> list[dict[str, Any]]:
    return [job_view(job) for job in sorted(snapshot_jobs(kind), key=lambda j: j.started_at)]


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
    if time.time() - _history_changed_at < HISTORY_SIGNAL_SECONDS:
        # Tells htmx to fire the 'historyUpdated' event, which reloads the history table
        response.headers['HX-Trigger'] = 'historyUpdated'
    return response


@app.route('/data/active')
def data_active():
    return _render_jobs_table("stream")


@app.route('/data/unarchived')
def data_unarchived():
    return _render_jobs_table("unarchived")


@app.route('/data/history')
def data_history():
    return render_template('history_table.html', history=get_history())


@app.route('/data/scheduler')
def data_scheduler():
    return render_template('schedule_table.html', jobs=get_scheduler_jobs())


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
    if start_download(DiscoveredVideo(video_id, manual=True)):
        return action_response(f"Started download for {video_id}")
    return action_response(f"Video {video_id} is already being downloaded.", "warning")


@app.route('/actions/<action>/<kind>/<video_id>', methods=['POST'])
def job_action(action: str, kind: str, video_id: str):
    """cancel, merge (stop & merge) or check (check now) for an active job."""
    if kind not in DOWNLOADER_CLASSES or action not in ("cancel", "merge", "check"):
        abort(404)
    job = get_job(kind, video_id)
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
    if name not in CHECKS:
        return action_response(f"Unknown job: {job_name}", "danger")
    if _check_locks[name].locked():
        return action_response(f"{CHECKS[name].display} is already running, please wait for it to finish", "warning")
    scheduler.add_job(run_check, trigger='date', run_date=datetime.now(timezone.utc), args=[name],
                      id=f"manual-{name}", replace_existing=True)
    return action_response(f"{CHECKS[name].display} started", "info")


@app.route('/actions/check', methods=['POST'])
def manual_check():
    """Older endpoint: run the stream check now."""
    return force_run_job("streams-checker")


@app.route('/actions/toggle_theme', methods=['POST'])
def toggle_theme():
    global GLOBAL_THEME
    GLOBAL_THEME = "dark" if GLOBAL_THEME == "light" else "light"
    return redirect(request.referrer or url_for('index'))


@app.route('/config', methods=['GET', 'POST'])
def config_page():
    if request.method == 'POST':
        saved, message = save_config(request.form.get('toml_content') or "")
        if not saved:
            flash(message, "danger")
        else:
            success, schedule_message = update_scheduler()
            flash(f"{message}. {schedule_message}", "success" if success else "warning")
        return redirect(url_for('config_page'))

    with open(config_file_path, 'r', encoding="utf-8") as f:
        content = f.read()
    return render_template('config.html', config_content=content, theme=GLOBAL_THEME)


# --- JSON API Endpoints ---

@app.route('/api/active')
def api_active():
    return jsonify(jobs_data("stream"))


@app.route('/api/unarchived')
def api_unarchived():
    return jsonify(jobs_data("unarchived"))


@app.route('/api/history')
def api_history():
    return jsonify([{**dict(row), 'timestamp': to_utc_iso(row['timestamp'])} for row in get_history()])


@app.route('/api/scheduler')
def api_scheduler():
    return jsonify(get_scheduler_jobs())


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico')


# ---------------------------------------------------------
# Startup (runs under gunicorn and with `python web.py`)
# ---------------------------------------------------------
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Run the holo-downloader web app (development server)")
    parser.add_argument('--config', type=str, default=config_file_path)
    args = parser.parse_args()
    config_file_path = args.config
    # Every ConfigHandler() created later reads this path
    os.environ["CONFIG_FILE"] = config_file_path
    print("Running in Developer Mode")

common.setup_umask()
_startup_config = load_config()
GLOBAL_THEME = _startup_config.get_webui_theme()
init_db()
update_scheduler()
scheduler.start()
discord_web.notify("0", "starting", config=_startup_config)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, load_dotenv=True)
