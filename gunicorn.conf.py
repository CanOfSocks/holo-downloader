# gunicorn.conf.py
#import signal
#import os
'''
def post_worker_init(worker):
    """
    This runs inside the worker process AFTER gunicorn has 
    initialized it and set up its own signal handlers.
    """
    def handle_shutdown(signum, frame):        
        # Access your app's global state
        # Note: You may need to import your 'kill_all' event here
        try:
            import common
            common.logger.info(f"Worker (pid:{os.getpid()}) received signal {signum}. Triggering cleanup...")
            common.handle_shutdown(signum, frame)
            
        except ImportError:
            worker.log.error("Could not import kill_all event.")

        # Give it a moment to clean up before the worker actually dies
        # Gunicorn's graceful timeout will handle the eventual exit
        
    # Re-register the signals inside the worker
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
'''
from common import kill_all
def worker_int(worker):
    worker.log.info("worker received SIGINT")
    kill_all.set()

def worker_abort(worker):
    worker.log.info("worker received SIGABRT")
    kill_all.set()