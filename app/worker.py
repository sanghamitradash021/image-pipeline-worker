"""Worker process: `python -m app.worker [--once]`.

Loops forever, claiming one job at a time via QueueRepository.claim_next_job
and processing it. Safe to run many copies of this process at once against
the same DB file (see app/repositories/job_repository.py and DESIGN.md).
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from app.db.session import engine, init_db
from app.repositories.job_repository import QueueRepository
from app.services.blur_service import compute_blur

logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(message)s")
log = logging.getLogger("worker")

MAX_ATTEMPTS = 3  # bonus: retries with exponential backoff
BACKOFF_BASE_SECONDS = 2
STUCK_TIMEOUT_SECONDS = 60  # bonus: crash recovery for stuck 'processing' jobs
POLL_INTERVAL_SECONDS = 0.5


def process_once() -> bool:
    """Claim and process a single job. Returns False if no job was pending."""
    with engine.begin() as conn:
        QueueRepository.reclaim_stuck_jobs(
            conn, cutoff=datetime.now(timezone.utc) - timedelta(seconds=STUCK_TIMEOUT_SECONDS)
        )
        row = QueueRepository.claim_next_job(conn)
        if row is None:
            return False
        job_id, image_path, attempts = row.id, row.image_path, row.attempts

    try:
        result = compute_blur(image_path)
        with engine.begin() as conn:
            QueueRepository.mark_done(conn, job_id, json.dumps(result))
        log.info("job %s done: %s", job_id, result)
    except Exception as exc:  # noqa: BLE001 - any processing failure marks the job failed
        error_result = json.dumps({"error": str(exc)})
        with engine.begin() as conn:
            if attempts < MAX_ATTEMPTS:
                backoff = BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
                run_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
                QueueRepository.mark_retry(conn, job_id, error_result, run_at)
                log.warning(
                    "job %s failed (attempt %d/%d), retrying in %ss: %s",
                    job_id, attempts, MAX_ATTEMPTS, backoff, exc,
                )
            else:
                QueueRepository.mark_failed(conn, job_id, error_result)
                log.error("job %s failed permanently after %d attempts: %s", job_id, attempts, exc)
    return True


def main():
    parser = argparse.ArgumentParser(description="Job processing worker")
    parser.add_argument("--once", action="store_true", help="process a single job and exit")
    args = parser.parse_args()

    init_db()
    log.info("worker started (pid=%s)", os.getpid())

    if args.once:
        process_once()
        return

    while True:
        if not process_once():
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
