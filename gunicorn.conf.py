# gunicorn.conf.py
#
# Gunicorn installs its own signal handlers in the worker, so these hooks set the shared
# kill_all event that every download and wait loop checks.
#
# This file is loaded by the gunicorn master too, so it only imports the small shutdown module:
# importing the application here (e.g. common) would also set up its logging in the master.
import sys
import time

from shutdown import kill_all

# How long a stopping worker waits in total for downloads to stop and close their files, and then
# for queued Discord messages to be sent. Docker sends SIGKILL 10 seconds after SIGTERM by default
# (`docker stop -t` / compose `stop_grace_period`).
SHUTDOWN_WAIT_SECONDS = 8


def worker_int(worker):
    worker.log.info("worker received SIGINT")
    kill_all.set()


def worker_abort(worker):
    worker.log.info("worker received SIGABRT")
    kill_all.set()


def worker_exit(server, worker):
    # Runs in the worker as it exits (e.g. SIGTERM when the container stops). The job threads and
    # the Discord sender are daemon threads that end with the process, so wait for the jobs to see
    # kill_all and stop, then for their last notifications to be sent, within SHUTDOWN_WAIT_SECONDS.
    kill_all.set()
    deadline = time.monotonic() + SHUTDOWN_WAIT_SECONDS
    # Modules loaded with the app; not imported here if the app never loaded
    jobs = sys.modules.get("jobs")
    if jobs is not None:
        still_running = jobs.wait_for_jobs(max(0.0, deadline - time.monotonic()))
        if still_running:
            worker.log.warning("{0} job(s) still running after {1}s, exiting anyway".format(still_running, SHUTDOWN_WAIT_SECONDS))
    discord_web = sys.modules.get("discord_web")
    if discord_web is not None and not discord_web.flush(max(0.0, deadline - time.monotonic())):
        worker.log.warning("Some Discord notifications could not be sent before exiting")
