# gunicorn.conf.py
#
# Gunicorn installs its own signal handlers in the worker, so these hooks set the shared
# kill_all event that every download and wait loop checks.
from common import kill_all


def worker_int(worker):
    worker.log.info("worker received SIGINT")
    kill_all.set()


def worker_abort(worker):
    worker.log.info("worker received SIGABRT")
    kill_all.set()


def worker_exit(server, worker):
    # Runs in the worker as it exits (e.g. SIGTERM when the container stops), so background
    # threads stop their loops and release files instead of being cut off mid-write.
    kill_all.set()
