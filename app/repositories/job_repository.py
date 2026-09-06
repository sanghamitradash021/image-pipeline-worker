"""Data access layer for jobs. This is also where the queue lives: the
`jobs` table's `status='pending'` rows are the queue, and claiming a row
is a single atomic UPDATE...RETURNING statement.

Why this is safe with N worker processes hitting the same SQLite file:
SQLite only ever allows one writer transaction to be mid-flight at a time
(others block on the file lock, up to PRAGMA busy_timeout, then retry).
CLAIM_SQL both selects and flips status in one statement, inside one
transaction, so there is no gap between "read pending row" and "mark it
processing" for a second worker to race into. See DESIGN.md for the full
exactly-once argument, and for the Postgres equivalent
(`SELECT ... FOR UPDATE SKIP LOCKED`).
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import Job

CLAIM_SQL = text(
    """
    UPDATE jobs
    SET status = 'processing',
        attempts = attempts + 1,
        claimed_at = :now
    WHERE id = (
        SELECT id FROM jobs
        WHERE status = 'pending'
          AND (run_at IS NULL OR run_at <= :now)
        ORDER BY created_at
        LIMIT 1
    )
    AND status = 'pending'
    RETURNING id, image_path, attempts
    """
)

RECLAIM_STUCK_SQL = text(
    """
    UPDATE jobs
    SET status = 'pending'
    WHERE status = 'processing'
      AND claimed_at < :cutoff
    """
)

MARK_DONE_SQL = text("UPDATE jobs SET status = 'done', result = :result WHERE id = :id")
MARK_FAILED_SQL = text("UPDATE jobs SET status = 'failed', result = :result WHERE id = :id")
MARK_RETRY_SQL = text(
    "UPDATE jobs SET status = 'pending', result = :result, run_at = :run_at WHERE id = :id"
)


class JobRepository:
    """CRUD for the API layer, backed by an ORM Session."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, image_path: str, run_at: Optional[datetime] = None) -> Job:
        job = Job(image_path=image_path, status="pending", run_at=run_at)
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    def get_by_id(self, job_id: str) -> Optional[Job]:
        return self.session.get(Job, job_id)

    def list(self, status: Optional[str], page: int, page_size: int) -> tuple[int, list[Job]]:
        query = self.session.query(Job)
        if status:
            query = query.filter(Job.status == status)
        total = query.count()
        items = (
            query.order_by(Job.created_at)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return total, items


class QueueRepository:
    """Queue operations for the worker, backed by a raw Connection so each
    call can run as one atomic transaction (see module docstring)."""

    @staticmethod
    def claim_next_job(conn):
        now = datetime.now(timezone.utc)
        return conn.execute(CLAIM_SQL, {"now": now}).first()

    @staticmethod
    def reclaim_stuck_jobs(conn, cutoff: datetime) -> None:
        conn.execute(RECLAIM_STUCK_SQL, {"cutoff": cutoff})

    @staticmethod
    def mark_done(conn, job_id: str, result_json: str) -> None:
        conn.execute(MARK_DONE_SQL, {"result": result_json, "id": job_id})

    @staticmethod
    def mark_failed(conn, job_id: str, result_json: str) -> None:
        conn.execute(MARK_FAILED_SQL, {"result": result_json, "id": job_id})

    @staticmethod
    def mark_retry(conn, job_id: str, result_json: str, run_at: datetime) -> None:
        conn.execute(MARK_RETRY_SQL, {"result": result_json, "run_at": run_at, "id": job_id})
