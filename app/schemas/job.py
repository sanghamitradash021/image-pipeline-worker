from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict


class JobCreate(BaseModel):
    image_path: str  # resolved relative to IMAGE_DIR, see app/services/job_service.py
    run_at: Optional[datetime] = None  # bonus: schedule a job for the future


class JobOut(BaseModel):
    id: str
    status: str
    result: Optional[Any] = None
    attempts: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobList(BaseModel):
    total: int
    items: List[JobOut]
