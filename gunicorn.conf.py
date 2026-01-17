# gunicorn.conf.py
import signal
import os

def post_worker_init(worker):
    """
    This runs inside the worker process AFTER gunicorn has 
    initialized it and set up its own signal handlers.
    """
    def handle_shutdown(signum, frame):
        worker.log.info(f"Worker (pid:{os.getpid()}) received signal {signum}. Triggering cleanup...")
        
        # Access your app's global state
        # Note: You may need to import your 'kill_all' event here
        try:
            import common
            common.handle_shutdown(signum, frame)
            worker.log.info("kill_all event set successfully.")
        except ImportError:
            worker.log.error("Could not import kill_all event.")

        # Give it a moment to clean up before the worker actually dies
        # Gunicorn's graceful timeout will handle the eventual exit
        
    # Re-register the signals inside the worker
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)