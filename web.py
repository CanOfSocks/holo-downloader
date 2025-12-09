import common
import os
import threading
import time
import sqlite3
import random
import sys
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, flash, make_response # <-- ADD make_response
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import tomlkit
import uuid

from getConfig import ConfigHandler
import downloadVid
import getMembers
import communityPosts
import getChatOnly
import unarchived
import getVids

# --- Configuration & Constants ---
CONFIG_FILE = 'config.toml'
DB_FILE = 'stream_history.db'
LOCK = threading.Lock()

scheduler = BackgroundScheduler()

# Global State for Active Downloads
active_downloads = {}
active_unarchived_downloads = {}
other_threads = {}

# NEW: Global State for signaling history update from a background thread
recently_finished = [] 

app = Flask(__name__)
app.secret_key = 'dev-secret-key'

# --- Database Management ---

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id VARCHAR(11),
            type VARCHAR(20),
            status VARCHAR(20),
            total_size INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def save_to_history(video_id, stats, download_type="unknown"):
    """Saves finished stream to DB and ensures only last 50 exist."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    download_size = stats.get('video', {}).get("current_filesize", 0) + stats.get('audio', {}).get("current_filesize", 0)

    c.execute('INSERT INTO history (video_id, type, total_size, status) VALUES (?, ?, ?, ?)',
              (video_id, download_type, convert_bytes(download_size), stats.get("status", None)))
    
    c.execute('''
        DELETE FROM history WHERE id NOT IN (
            SELECT id FROM history ORDER BY id DESC LIMIT 50
        )
    ''')
    
    conn.commit()
    conn.close()

def get_history():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM history ORDER BY id DESC')
    rows = c.fetchall()
    conn.close()
    return rows

# --- Config Management (Unchanged from previous update) ---

def load_config():
    if not os.path.exists(CONFIG_FILE):
        doc = tomlkit.document()
        doc.add("app_name", "StreamArchiver")
        
        doc.add(tomlkit.comment("Format: Minute Hour Day_of_Month Month Day_of_Week (standard 5 fields)"))
        doc.add("cron_schedule", "*/30 * * * *") 
        
        with open(CONFIG_FILE, "w") as f:
            f.write(tomlkit.dumps(doc))
            
    return ConfigHandler(config_file=CONFIG_FILE)

def save_config(content):
    try:
        tomlkit.parse(content)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(content)
        return True, "Config saved"
    except Exception as e:
        return False, str(e)

# --- Core Logic & Threading ---

def thread_worker(video_id, downloader, thread_tracker: dict = active_downloads):
    global recently_finished # <-- Use global variable for signaling
    try:
        downloader.main()
        save_to_history(video_id, downloader.livestream_downloader.stats, download_type=thread_tracker.get(video_id, {}).get("type", "Unknown"))
        
        # Signal that history needs update
        with LOCK:
            recently_finished.append(True) 
            
    except Exception as e:
        common.logger.error(f"Error downloading {video_id}: {e}", file=sys.stderr)
    finally:
        with LOCK:
            if video_id in thread_tracker:
                del thread_tracker[video_id]

def start_download(video_id):
    with LOCK:
        if video_id in active_downloads:
            return False 

        downloader = downloadVid.VideoDownloader(id=video_id)
        thread = threading.Thread(target=thread_worker, args=(video_id, downloader, active_downloads), daemon=True)
        
        active_downloads[video_id] = {
            'downloader': downloader,
            'thread': thread,
            'type': "stream",
            'start_time': datetime.now()
        }
        
        thread.start()
        return True
    
def start_unarchived_download(video_id):
    with LOCK:
        if video_id in active_unarchived_downloads:
            return False 

        downloader = unarchived.UnarchivedDownloader(id=video_id)
        thread = threading.Thread(target=thread_worker, args=(video_id, downloader, active_unarchived_downloads), daemon=True)
        
        active_unarchived_downloads[video_id] = {
            'downloader': downloader,
            'thread': thread,
            'type': "unarchived",
            'start_time': datetime.now()
        }
        
        thread.start()
        return True

def get_streams():
    common.logger.info("Running scheduled stream check...")
    streams = getVids.main(unarchived=False)
    for stream in streams:
        start_download(stream)
        # Have slightly randomised stream to help prevent rate limiting
        if len(streams) > 1:
            time.sleep(random.uniform(5.0, 10.0))

def get_unarchived():
    common.logger.info("Running scheduled stream check...")
    streams = getVids.main(unarchived=True)
    for stream in streams:
        start_unarchived_download(stream)
        # Have slightly randomised stream to help prevent rate limiting
        if len(streams) > 1:
            time.sleep(random.uniform(5.0, 10.0))

def get_members():
    common.logger.info("Running scheduled stream check...")
    streams = getMembers.main()
    for stream in streams:
        start_download(stream)
        # Have slightly randomised stream to help prevent rate limiting
        if len(streams) > 1:
            time.sleep(random.uniform(5.0, 10.0))

def get_community_tab():
    common.logger.info("Running scheduled stream check...")
    communityPosts.main()

def update_scheduler():
    config = load_config()
    
    def create_schedule(name: str, method):
        schedule_name = f"{name}-checker"
        if scheduler.get_job(schedule_name):
            scheduler.remove_job(schedule_name)

        cron_string = config.get_cron_schedule(name)
        if not cron_string:
            return False, f"No schedule for {name}"
        
        try:
            trigger = CronTrigger.from_crontab(cron_string)
            
            scheduler.add_job(
                method, 
                trigger=trigger,
                id=schedule_name
            )
            return True, f"Scheduler updated with: {cron_string}"
        except ValueError as e:
            common.logger.error(f"Cron Error: {e}")
            return False, f"Invalid Cron expression: {e}"

    schedules = {
        "streams": get_streams,
        "unarchived": get_unarchived,
        "members_only": get_members,
        "community_posts": get_community_tab,
    }

    for schedule_type, method in schedules.items():
        created, message = create_schedule(name=schedule_type, method=method)
        if created is False:
            common.logger.debug("Error creating schedule for {0}: {1}".format(schedule_type, message))
    return True, "All valid schedules loaded"


# --- Helper for HTMX Data Routes ---

def get_active_jobs_data():
    """Prepares active download data for display."""
    with LOCK:
        current_jobs = []
        for vid, job in active_downloads.copy().items():
            current_jobs.append({
                'id': vid,
                'stats': job['downloader'].livestream_downloader.stats,
                'info': job['downloader'].info_dict.copy(),
                'start_time': job['start_time'].strftime('%H:%M:%S')
            })
    return current_jobs

# --- Helper to format byte strings ---
def convert_bytes(bytes):
        try:
            int(float(bytes))
        except Exception as e:
            common.logger.exception("Error converting {0} to number".format(bytes))
            return "Invalid Value"
        # List of units in order
        units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB']
        
        # Start with bytes and convert to larger units
        unit_index = 0
        while bytes >= 1024 and unit_index < len(units) - 1:
            bytes /= 1024
            unit_index += 1
        
        # Format and return the result
        return f"{bytes:.2f} {units[unit_index]}"

app.jinja_env.filters['convert_bytes'] = convert_bytes

# --- HTMX Data Routes ---

@app.route('/data/active')
def data_active():
    """Endpoint for HTMX to poll active downloads table AND signal history update."""
    global recently_finished
    current_jobs = get_active_jobs_data()
    # 1. Render the active table
    rendered_table = render_template_string(ACTIVE_TABLE_TEMPLATE, active_downloads=current_jobs)
    response = make_response(rendered_table)
    
    # 2. Check the signal list set by the background thread
    with LOCK:
        if recently_finished:
            # Send HTMX trigger header, instructing the client to fire 'historyUpdated' event
            response.headers['HX-Trigger'] = 'historyUpdated' 
            recently_finished.clear() # Reset the flag
            
    return response

@app.route('/data/history')
def data_history():
    """Endpoint for HTMX to poll history table."""
    history = get_history()
    return render_template_string(HISTORY_TABLE_TEMPLATE, history=history)


# --- Web Routes (Unchanged) ---

@app.route('/')
def index():
    history = get_history()
    active_jobs = get_active_jobs_data()
    
    return render_template_string(HTML_TEMPLATE, 
                                  history=history,
                                  active_downloads=active_jobs) 

@app.route('/actions/check', methods=['POST'])
def manual_check():
    """Triggers an existing job's function to run immediately."""

    manual_check_id = "manual-stream-check"

    # 1. Get the existing job object
    job = scheduler.get_job(manual_check_id)

    if job:
        common.logger.warning("Manual stream check already running")
        flash("Manual stream check already triggered, please wait for existing check to finish", "warning")
        return redirect(url_for('index'))

    # 2. Schedule a new one-off job with the same function/parameters
    scheduler.add_job(
        get_streams, 
        trigger='date',
        run_date=datetime.now(),
        id=manual_check_id
        # Copying job stores, executor, etc. is often unnecessary
        # but you might want to specify executor if you need a different one.
    )
    common.logger.debug(f"Successfully triggered immediate run for job ID: {manual_check_id}")
    flash("Manual check triggered! Tables will update shortly.", "success")
    return redirect(url_for('index'))

@app.route('/actions/add', methods=['POST'])
def manual_add():
    video_id = request.form.get('video_id')
    if video_id:
        if start_download(video_id):
            flash(f"Started download for {video_id}", "success")
        else:
            flash(f"Video {video_id} is already being downloaded.", "warning")
    return redirect(url_for('index'))

@app.route('/config', methods=['GET', 'POST'])
def config_page():
    if request.method == 'POST':
        new_toml = request.form.get('toml_content')
        if save_config(new_toml):
            success, message = update_scheduler()
            if success:
                load_config()
                flash(message, "success")                
            else:
                flash(message, "danger") 
        else:
            flash("Invalid TOML format. Configuration not saved.", "danger")
        return redirect(url_for('config_page'))

    with open(CONFIG_FILE, 'r', encoding="utf-8") as f:
        content = f.read()
    return render_template_string(CONFIG_TEMPLATE, config_content=content)

@app.route('/actions/cancel/<video_id>', methods=['POST'])
def cancel_download(video_id):
    """Sets the kill flag to True for a specific downloader."""
    with LOCK:
        if video_id in active_downloads:
            job_entry = active_downloads[video_id]
            downloader_instance: downloadVid.VideoDownloader = job_entry.get('downloader')
            
            # Navigate to the inner downloader object that holds the flag
            # Based on your existing code: downloader -> livestream_downloader
            try:
                downloader_instance.kill_this.set()
            except Exception as e:
                common.logger.error(f"Failed to cancel {video_id}: {e}")
                flash(f"Error cancelling {video_id}", "danger")
        else:
            flash(f"Stream {video_id} is not currently active.", "secondary")
            
    return redirect(url_for('index'))

# --- HTMX Table Templates (Unchanged) ---

ACTIVE_TABLE_TEMPLATE = """
{% if active_downloads %}
<table class="table table-striped align-middle">
    <thead>
        <tr>
            <th>Video ID</th>
            <th>Title</th>
            <th>Status</th>
            <th>Video Segments</th>
            <th>Audio Segments</th>
            <th>Latest Segment</th>
            <th>Size</th>
            <th>Started At</th>
            <th>Action</th>
        </tr>
    </thead>
    <tbody>
        {% for job in active_downloads %}
        <tr>
            <td>{{ job.id }}</td>
            <td style="max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="{{ job.info.get("fulltitle", "") }}">
                {{ job.info.get("fulltitle", "") }}
            </td>
            <td><span class="badge bg-info">{{ job.stats.get('status', "Unknown") }}</span></td>
            <td>{{ job.stats.get('video', {}).get('downloaded_segments', 0) }}</td>
            <td>{{ job.stats.get('audio', {}).get('downloaded_segments', 0) }}</td>
            <td>{{ job.stats.get('video', {}).get('latest_sequence', 0) or job.stats.get('audio', {}).get('latest_sequence', 0) }}</td>
            <td>{{ (job.stats.get('video', {}).get('current_filesize', 0) + job.stats.get('audio', {}).get('current_filesize', 0)) | convert_bytes }}</td>
            <td>{{ job.start_time }}</td>
            <td>
                <form action="/actions/cancel/{{ job.id }}" method="POST" style="margin:0;">
                    <button type="submit" class="btn btn-outline-danger btn-sm" onclick="return confirm('Stop recording {{ job.id }}?');" title="Stop Download">
                        🗑️
                    </button>
                </form>
            </td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% else %}
<div class="text-center p-3">
    <p class="text-muted mb-0">No active downloads.</p>
</div>
{% endif %}
"""

HISTORY_TABLE_TEMPLATE = """
<table class="table table-sm">
    <thead>
        <tr>
            <th>ID</th>
            <th>Video ID</th>
            <th>Download Type</th>
            <th>Status</th>
            <th>Size</th>
            <th>Date</th>
        </tr>
    </thead>
    <tbody>
        {% for row in history %}
        <tr>
            <td>{{ row.id }}</td>
            <td>{{ row.video_id }}</td>
            <td>{{ row.type }}</td>
            <td>{{ row.status }}</td>
            <td>{{ (row.total_size) | convert_bytes }}</td>
            <td>{{ row.timestamp }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
"""


# --- Main HTML Template (Updated History Trigger) ---

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stream Downloader</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://unpkg.com/htmx.org@1.9.10"></script> 
</head>
<body class="bg-light">
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container">
            <a class="navbar-brand" href="/">StreamArchiver</a>
            <div class="navbar-nav ms-auto">
                <a class="nav-link" href="/">Dashboard</a>
                <a class="nav-link" href="/config">Configuration</a>
            </div>
        </div>
    </nav>

    <div class="container mt-4">
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="alert alert-{{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <div class="card mb-4 shadow-sm">
            <div class="card-body">
                <h5 class="card-title">Manual Actions</h5>
                <div class="d-flex gap-2">
                    <form action="/actions/check" method="POST">
                        <button type="submit" class="btn btn-primary">Run Stream Check Now</button>
                    </form>
                    <form action="/actions/add" method="POST" class="d-flex gap-2">
                        <input type="text" name="video_id" class="form-control" placeholder="Video ID" required>
                        <button type="submit" class="btn btn-success">Download ID</button>
                    </form>
                </div>
            </div>
        </div>

        <div class="card mb-4 shadow-sm">
            <div class="card-header bg-primary text-white">
                Active Downloads
            </div>
            <div class="card-body" 
                 id="active-downloads-container"
                 hx-get="/data/active"
                 hx-trigger="load, every 3s" 
                 hx-swap="innerHTML">
                 
                 <p class="text-center text-muted">Loading active downloads...</p>
            </div>
        </div>

        <div class="card shadow-sm">
            <div class="card-header bg-secondary text-white">
                History (Last 50)
            </div>
            <div class="card-body" 
                 id="history-container"
                 hx-get="/data/history"
                 hx-trigger="load, every 30s, historyUpdated from:body" 
                 hx-swap="innerHTML">
                 
                 <p class="text-center text-muted">Loading history...</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

CONFIG_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Configuration</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">

    <!-- CodeMirror CSS -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.15/codemirror.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.15/theme/eclipse.min.css">

    <style>
        html, body {
            height: 100%;
        }
        body {
            display: flex;
            flex-direction: column;
        }
        .content-wrapper {
            flex: 1 0 auto; /* Take remaining height */
            display: flex;
            flex-direction: column;
        }
        .card-body {
            flex: 1 1 auto;
            display: flex;
            flex-direction: column;
        }
        .CodeMirror {
            flex: 1 1 auto; /* Make CodeMirror fill the card body */
        }
    </style>
</head>
<body class="bg-light">
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container">
            <a class="navbar-brand" href="/">StreamArchiver</a>
            <div class="navbar-nav ms-auto">
                <a class="nav-link" href="/">Dashboard</a>
                <a class="nav-link active" href="/config">Configuration</a>
            </div>
        </div>
    </nav>

    <div class="container-fluid content-wrapper mt-3">
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="alert alert-{{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <div class="card shadow-sm flex-grow-1">
            <div class="card-header">Edit Config (TOML)</div>
            <div class="card-body d-flex flex-column">
                <form method="POST" class="d-flex flex-column flex-grow-1">
                    <textarea id="toml_editor" name="toml_content" class="form-control" style="font-family: monospace;">{{ config_content }}</textarea>
                    <button type="submit" class="btn btn-primary mt-2">Save & Reload Scheduler</button>
                </form>
            </div>
        </div>
    </div>

    <!-- CodeMirror JS -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.15/codemirror.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.15/mode/toml/toml.min.js"></script>
    <script>
        var editor = CodeMirror.fromTextArea(document.getElementById("toml_editor"), {
            lineNumbers: true,
            mode: "toml",
            theme: "eclipse",
            lineWrapping: true
        });

        document.querySelector('form').addEventListener('submit', function() {
            editor.save();
        });
    </script>
</body>
</html>
"""


# --- Main Entry Point ---

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Web App runner")
    parser.add_argument('--config', type=str, default="config.toml", help='Config file (defaults to "config.toml")')
    args = parser.parse_args()

    CONFIG_FILE = args.config
    # Initialize DB
    init_db()
    
    # Initialize Config & Scheduler
    update_scheduler()
    scheduler.start()
    
    app.run(debug=True, host='0.0.0.0', use_reloader=False, port=5000)