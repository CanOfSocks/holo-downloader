import common
import os
import threading
import time
import sqlite3
import random
import sys
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, make_response # <-- ADD make_response
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import tomlkit

from getConfig import ConfigHandler, config_file_path
import downloadVid
import getMembers
import communityPosts
import unarchived
import getVids

from flask_caching import Cache


# --- Configuration & Constants ---
config_file_path = 'config.toml'
DB_FILE = 'stream_history.db'
LOCK = threading.Lock()

scheduler = BackgroundScheduler(daemon=True)

# Global State for Active Downloads
active_downloads = {}
active_unarchived_downloads = {}
other_threads = {}

# NEW: Global State for signaling history update from a background thread
recently_finished = [] 

app = Flask(__name__)
#app.secret_key = 'dev-secret-key'

# Configure SimpleCache (stores in RAM)
app.config['CACHE_TYPE'] = 'SimpleCache' 
app.config['CACHE_DEFAULT_TIMEOUT'] = 300 # Default 5 minutes

cache = Cache(app)

GLOBAL_THEME = "dark"

history_update_event = threading.Event()

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
              (video_id, download_type, download_size, stats.get("status", None)))
    
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
    if not os.path.exists(config_file_path):
        doc = tomlkit.document()
        doc.add("app_name", "StreamArchiver")
        
        doc.add(tomlkit.comment("Format: Minute Hour Day_of_Month Month Day_of_Week (standard 5 fields)"))
        doc.add("cron_schedule", "*/30 * * * *") 
        
        with open(config_file_path, "w") as f:
            f.write(tomlkit.dumps(doc))
            
    return ConfigHandler(config_file=config_file_path)

def save_config(content):
    try:
        tomlkit.parse(content)
        with open(config_file_path, "w", encoding="utf-8") as f:
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

        # Clear cache of history table
        with app.app_context():
            cache.delete_memoized(data_history)
        
        history_update_event.set()
            
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
            downloader: downloadVid.VideoDownloader = job['downloader']
            display_info = {
                'fulltitle': downloader.info_dict.get('fulltitle'),
                'title': downloader.embed_info.get('title')
            }

            current_jobs.append({
                'id': vid,
                'stats': downloader.livestream_downloader.stats,
                'info': display_info, # Pass the small dict, not the huge one
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
        units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB']
        
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
    current_jobs = get_active_jobs_data()
    # 1. Render the active table
    rendered_table = render_template('active_table.html', active_downloads=current_jobs)
    response = make_response(rendered_table)
    
    # 2. Check the signal list set by the background thread
    with LOCK:
        if history_update_event.is_set():
            # Send HTMX trigger header, instructing the client to fire 'historyUpdated' event
            response.headers['HX-Trigger'] = 'historyUpdated' 
            history_update_event.clear() # Reset the flag
            
    return response

@app.route('/data/history')
@cache.cached(timeout=600) # Cache this result for 10 minutes (or until cleared)
def data_history():
    """Endpoint for HTMX to poll history table."""
    history = get_history()
    return render_template('history_table.html', history=history)


# --- Web Routes (Unchanged) ---

@app.route('/')
def index():
    history = get_history()
    active_jobs = get_active_jobs_data()
    
    return render_template('index.html', 
                                  history=history,
                                  active_downloads=active_jobs,
                                  theme=GLOBAL_THEME,
                                  ) 

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

    with open(config_file_path, 'r', encoding="utf-8") as f:
        content = f.read()
    return render_template('config.html', config_content=content, theme=GLOBAL_THEME)

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

@app.route('/actions/toggle_theme', methods=['POST'])
def toggle_theme():
    global GLOBAL_THEME
    with LOCK:
        GLOBAL_THEME = "dark" if GLOBAL_THEME == "light" else "light"
    # Redirect to the page that made the request (config or index)
    return redirect(request.referrer or url_for('index'))

# --- Main Entry Point ---

if __name__ == '__main__':
    common.setup_umask()
    import argparse
    parser = argparse.ArgumentParser(description="Web App runner")
    parser.add_argument('--config', type=str, default="config.toml", help='Config file (defaults to "config.toml")')
    args = parser.parse_args()

    config_file_path = args.config

    config = load_config()
    GLOBAL_THEME = config.get_webui_theme()

    # Initialize DB
    init_db()
    
    # Initialize Config & Scheduler
    update_scheduler()
    scheduler.start()
    
    app.run(host='0.0.0.0', port=5000)