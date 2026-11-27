"""
Shutdown handling: the kill_all event every download and wait loop checks, and the signal handlers
that set it.

Kept in its own small module (no logging or config) because gunicorn.conf.py imports it in the
gunicorn master process: anything set up here at import (such as a log file handler) would also
exist in the master, which never uses it.
"""
import signal
import threading
import time
from platform import system

# Set when the application is shutting down. Every download and wait loop checks it.
kill_all = threading.Event()

original_sigint = signal.getsignal(signal.SIGINT)


def handle_shutdown(signum, frame) -> None:
    """Signal handler: tell every thread to stop, give them a moment, then run the original handler."""
    kill_all.set()
    time.sleep(5)
    if callable(original_sigint):
        original_sigint(signum, frame)


# Signal handlers can only be installed from the main thread. Under gunicorn this module is first
# imported by gunicorn.conf.py in the master process; gunicorn then installs its own handlers, and
# the worker uses gunicorn's hooks (see gunicorn.conf.py) to set kill_all.
if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGINT, handle_shutdown)
    if system() == "Windows":
        # SIGTERM won't fire on Windows, but SIGBREAK will on Ctrl-Break
        signal.signal(signal.SIGBREAK, handle_shutdown)
    else:
        signal.signal(signal.SIGTERM, handle_shutdown)
