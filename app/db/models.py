import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.session import Base


def _now():
    return datetime.now(timezone.utc)


def _new_id():
    return uuid.uuid4().hex


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=_new_id)
    image_path = Column(String, nullable=False)  # resolved against settings.image_dir
    status = Column(String, nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    result = Column(Text, nullable=True)  # JSON-encoded dict
    run_at = Column(DateTime(timezone=True), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
