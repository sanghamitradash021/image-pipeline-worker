from typing import Generator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.repositories.job_repository import JobRepository
from app.schemas.job import JobCreate, JobList, JobOut
from app.services.job_service import JobService

router = APIRouter(prefix="/jobs", tags=["jobs"])


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_job_service(session: Session = Depends(get_db)) -> JobService:
    return JobService(JobRepository(session))


@router.post("", response_model=JobOut, status_code=201)
def create_job(payload: JobCreate, service: JobService = Depends(get_job_service)):
    try:
        return service.create_job(payload.image_path, payload.run_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, service: JobService = Depends(get_job_service)):
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("", response_model=JobList)
def list_jobs(
    status: Optional[str] = Query(default=None, pattern="^(pending|processing|done|failed)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: JobService = Depends(get_job_service),
):
    return service.list_jobs(status, page, page_size)
