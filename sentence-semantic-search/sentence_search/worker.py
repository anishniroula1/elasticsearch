import logging
import signal
import time

from sentence_search.components import (
    match_service,
    opensearch_store,
    postgres_store,
)
from sentence_search.config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("sentence-match-worker")
running = True


def stop_worker(*_):
    """Ask the worker loop to stop after its current job."""

    global running
    running = False


def run_worker():
    """Continuously process new sentence matching jobs."""

    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)
    postgres_store.wait_until_ready()
    postgres_store.init_schema()
    postgres_store.requeue_stale_jobs()
    opensearch_store.wait_until_ready()
    opensearch_store.ensure_indices()

    last_stale_check = time.monotonic()
    while running:
        if time.monotonic() - last_stale_check >= 60:
            postgres_store.requeue_stale_jobs()
            last_stale_check = time.monotonic()

        job = postgres_store.claim_job()
        if not job:
            time.sleep(config.worker_poll_seconds)
            continue

        try:
            result = match_service.process_job(job)
            postgres_store.complete_job(job["globalId"])
            logger.info("Completed sentence match job: %s", result)
        except Exception as error:
            logger.exception(
                "Sentence match job %s failed on attempt %s",
                job["globalId"],
                job["matchAttempts"],
            )
            postgres_store.fail_job(job, error)
            # The pause prevents a temporary OpenSearch or proxy problem from
            # immediately sending another request through the same gateway.
            time.sleep(config.worker_retry_seconds)

    postgres_store.close()


if __name__ == "__main__":
    run_worker()
