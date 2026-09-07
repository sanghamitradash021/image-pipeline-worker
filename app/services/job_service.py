import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.db.models import Job
from app.repositories.job_repository import JobRepository
from app.schemas.job import JobList, JobOut


def resolve_image_path(image_path: str) -> str:
    """Resolve a user-supplied image_path against IMAGE_DIR and reject
    anything that escapes it (e.g. "../../etc/passwd").

    Images are never committed to the repo — each user points IMAGE_DIR at
    wherever their own images live (see .env.example / README.md) and
    requests reference files by name/relative-path within it.
    """
    image_dir = Path(get_settings().image_dir).resolve()
    candidate = (image_dir / image_path).resolve()
    if candidate != image_dir and image_dir not in candidate.parents:
        raise ValueError(f"image_path escapes IMAGE_DIR: {image_path}")
    return str(candidate)


class JobService:
    def __init__(self, repository: JobRepository):
        self.repository = repository

    def create_job(self, image_path: str, run_at: Optional[datetime]) -> JobOut:
        resolved_path = resolve_image_path(image_path)
        job = self.repository.create(image_path=resolved_path, run_at=run_at)
        return self._to_schema(job)

    def get_job(self, job_id: str) -> Optional[JobOut]:
        job = self.repository.get_by_id(job_id)
        return self._to_schema(job) if job else None

    def list_jobs(self, status: Optional[str], page: int, page_size: int) -> JobList:
        total, items = self.repository.list(status, page, page_size)
        return JobList(total=total, items=[self._to_schema(j) for j in items])

    @staticmethod
    def _to_schema(job: Job) -> JobOut:
        result = json.loads(job.result) if job.result else None
        return JobOut(
            id=job.id,
            status=job.status,
            result=result,
            attempts=job.attempts,
            created_at=job.created_at,
            updated_at=job.updated_at,
            claimed_at=job.claimed_at,
        )
