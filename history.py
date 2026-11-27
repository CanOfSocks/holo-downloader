"""
Download history: an SQLite table of the last HISTORY_LIMIT finished jobs, shown in the web UI.
"""
import os
import sqlite3
import threading
import time
from contextlib import closing
from typing import Optional

DB_FILE = os.environ.get('HISTORY_DB', 'stream_history.db')
HISTORY_LIMIT = 50

# Increases each time an entry is added. The web UI compares it with the version it last loaded,
# so each browser reloads the history table once per change (see web._render_jobs_table).
# It starts from the time the app started, so a browser left open across a restart reloads too.
_version: int = int(time.time())
_version_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_FILE, timeout=10)


def init_db() -> None:
    # closing() closes the connection; "with conn" on its own only commits
    with closing(connect()) as conn, conn:
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


def save(video_id: str, kind: str, status: str, total_size: int, reason: Optional[str]) -> None:
    """Add a finished job and keep only the last HISTORY_LIMIT entries."""
    with closing(connect()) as conn, conn:
        conn.execute('INSERT INTO history (video_id, type, total_size, status, reason) VALUES (?, ?, ?, ?, ?)',
                     (video_id, kind, total_size, status, reason))
        conn.execute('DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT ?)', (HISTORY_LIMIT,))
    mark_changed()


def get_all() -> list[sqlite3.Row]:
    """Every entry, newest first."""
    with closing(connect()) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute('SELECT * FROM history ORDER BY id DESC').fetchall()


def mark_changed() -> None:
    global _version
    with _version_lock:
        _version += 1


def version() -> int:
    """The current history version (see _version)."""
    return _version
