# gunicorn.conf.py
#
# Gunicorn installs its own signal handlers in the worker, so these hooks set the shared
# kill_all event that every download and wait loop checks.
#
# This file is loaded by the gunicorn master too, so it only imports the small shutdown module:
# importing the application here (e.g. common) would also set up its logging in the master.
import sys

from shutdown import kill_all

# How long a stopping worker waits for downloads to stop and close their files. Docker sends
# SIGKILL 10 seconds after SIGTERM by default (`docker stop -t` / compose `stop_grace_period`).
JOB_STOP_WAIT_SECONDS = 8


def worker_int(worker):
    worker.log.info("worker received SIGINT")
    kill_all.set()


def worker_abort(worker):
    worker.log.info("worker received SIGABRT")
    kill_all.set()


def worker_exit(server, worker):
    # Runs in the worker as it exits (e.g. SIGTERM when the container stops). The job threads are
    # daemon threads that end with the process, so wait for them to see kill_all and stop first.
    kill_all.set()
    jobs = sys.modules.get("jobs")   # Loaded with the app; not imported here if the app never loaded
    if jobs is not None:
        still_running = jobs.wait_for_jobs(JOB_STOP_WAIT_SECONDS)
        if still_running:
            worker.log.warning("{0} job(s) still running after {1}s, exiting anyway".format(still_running, JOB_STOP_WAIT_SECONDS))
