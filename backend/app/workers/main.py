"""Process entry points: `python -m app.workers.main worker|scheduler`."""

import logging
import signal
import sys
import time

from rq import Queue, Worker

from app.config import get_settings
from app.logging_config import configure_logging
from app.workers.queue import QUEUE_NAME, redis_connection
from app.workers.tasks import maintenance_task

log = logging.getLogger("datarescue.worker")


def run_worker() -> None:
    conn = redis_connection()
    worker = Worker([Queue(QUEUE_NAME, connection=conn)], connection=conn)
    # with_scheduler lets RQ honour Retry intervals (exponential backoff).
    worker.work(with_scheduler=True, logging_level=get_settings().log_level)


def run_scheduler() -> None:
    """Small built-in scheduler; Airflow is the optional heavier alternative."""
    interval = get_settings().freshness_check_interval_seconds
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("scheduler started", extra={"interval_seconds": interval})
    while not stopping:
        try:
            log.info("maintenance run", extra=maintenance_task())
        except Exception:
            log.exception("maintenance run failed")
        for _ in range(interval):
            if stopping:
                break
            time.sleep(1)


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    mode = sys.argv[1] if len(sys.argv) > 1 else "worker"
    {"worker": run_worker, "scheduler": run_scheduler}[mode]()
