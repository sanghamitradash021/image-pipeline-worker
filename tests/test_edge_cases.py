"""Negative / edge-case test suite.

Complements the happy-path coverage in test_api.py and test_worker.py.
Each section below maps to one item of the edge-case checklist this suite
was written against (numbers kept in the comments for traceability).
"""

import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

SAMPLE = "sharp_checkerboard.png"


# 1. Invalid/missing image_path ---------------------------------------------

def test_missing_image_path_rejected(client):
    resp = client.post("/jobs", json={})
    assert resp.status_code == 422


def test_null_image_path_rejected(client):
    resp = client.post("/jobs", json={"image_path": None})
    assert resp.status_code == 422


def test_empty_image_path_accepted_but_fails_at_processing(client, app_env):
    """Empty string resolves to IMAGE_DIR itself (not outside it, so the
    traversal guard doesn't reject it) but isn't a readable image, so the
    worker fails it rather than the API rejecting it up front."""
    _main, worker = app_env
    created = client.post("/jobs", json={"image_path": ""}).json()
    assert created["status"] == "pending"

    worker.process_once()
    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] in ("pending", "failed")
    assert job["result"] is not None and "error" in job["result"]


# 2. Non-existent image ------------------------------------------------------

def test_nonexistent_image_fails_gracefully(client, app_env):
    _main, worker = app_env
    created = client.post("/jobs", json={"image_path": "no_such_file.png"}).json()

    worker.process_once()

    job = client.get(f"/jobs/{created['id']}").json()
    assert job["attempts"] == 1
    assert job["status"] == "pending"  # first failure -> retried, not failed yet
    assert "error" in job["result"]


# 3. Unsupported/corrupted image ---------------------------------------------

def test_corrupted_image_fails_gracefully(client, app_env):
    from app.core.config import get_settings

    image_dir = get_settings().image_dir
    corrupt_path = os.path.join(image_dir, "corrupt.png")
    with open(corrupt_path, "wb") as f:
        f.write(b"not a real image, just garbage bytes")

    try:
        created = client.post("/jobs", json={"image_path": "corrupt.png"}).json()
        _main, worker = app_env
        worker.process_once()

        job = client.get(f"/jobs/{created['id']}").json()
        assert job["status"] in ("pending", "failed")
        assert "error" in job["result"]
    finally:
        os.remove(corrupt_path)


# 4. Non-existent job ID ------------------------------------------------------

def test_get_nonexistent_job_returns_404(client):
    resp = client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


# 5. Worker failure (any exception, not just a missing file) -----------------

def test_worker_handles_unexpected_exception_without_crashing(client, app_env, monkeypatch):
    _main, worker = app_env
    created = client.post("/jobs", json={"image_path": SAMPLE}).json()

    def boom(_path):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(worker, "compute_blur", boom)
    did_work = worker.process_once()

    assert did_work is True  # the worker loop itself must not crash/raise
    job = client.get(f"/jobs/{created['id']}").json()
    assert job["result"]["error"] == "simulated unexpected failure"
    assert job["status"] == "pending"  # attempt 1 of 3 -> retried


# 6. Worker crash during processing (simulated) ------------------------------

def test_worker_crash_leaves_job_processing_until_reclaimed(client, app_env):
    from app.repositories.job_repository import QueueRepository

    _main, worker = app_env
    created = client.post("/jobs", json={"image_path": SAMPLE}).json()

    # Simulate a worker that claims the job then crashes before it can mark
    # done/failed: run only the claim step, skip the rest of process_once.
    with worker.engine.begin() as conn:
        row = QueueRepository.claim_next_job(conn)
    assert row is not None and row.id == created["id"]

    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "processing"  # orphaned by the "crashed" worker

    # Not past STUCK_TIMEOUT_SECONDS yet -> a healthy worker leaves it alone.
    assert worker.process_once() is False

    # Once the stuck window elapses, it gets reclaimed and finished normally.
    with worker.engine.begin() as conn:
        conn.execute(
            text("UPDATE jobs SET claimed_at = :claimed_at WHERE id = :id"),
            {
                "claimed_at": datetime.now(timezone.utc)
                - timedelta(seconds=worker.STUCK_TIMEOUT_SECONDS + 5),
                "id": created["id"],
            },
        )
    assert worker.process_once() is True
    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "done"


# 7. Multiple workers claiming the same job ----------------------------------

def test_only_one_worker_claims_a_given_job(client, app_env):
    from app.repositories.job_repository import QueueRepository

    _main, worker = app_env
    created = client.post("/jobs", json={"image_path": SAMPLE}).json()

    results = []

    def try_claim():
        with worker.engine.begin() as conn:
            results.append(QueueRepository.claim_next_job(conn))

    threads = [threading.Thread(target=try_claim) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1
    assert claimed[0].id == created["id"]

    job = client.get(f"/jobs/{created['id']}").json()
    assert job["attempts"] == 1  # claimed exactly once despite 5 concurrent attempts


# 8. Retry + max-retry behavior -----------------------------------------------

def test_retry_uses_real_backoff_before_becoming_eligible_again(client, app_env):
    _main, worker = app_env
    created = client.post("/jobs", json={"image_path": "no_such_file.png"}).json()

    worker.process_once()
    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "pending"
    assert job["attempts"] == 1

    # Real (non-zero) backoff pushed run_at into the future -> not yet
    # eligible for reclaim, so nothing gets claimed on an immediate retry.
    assert worker.process_once() is False


def test_retry_fails_permanently_after_max_attempts(client, app_env, monkeypatch):
    _main, worker = app_env
    monkeypatch.setattr(worker, "BACKOFF_BASE_SECONDS", 0)  # zero backoff for all attempts below

    created = client.post("/jobs", json={"image_path": "no_such_file.png"}).json()

    for _ in range(worker.MAX_ATTEMPTS):
        worker.process_once()

    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "failed"
    assert job["attempts"] == worker.MAX_ATTEMPTS


# 9. Job stuck in processing (left alone below the stuck-timeout threshold) --

def test_job_processing_not_reclaimed_before_stuck_timeout(client, app_env):
    from app.repositories.job_repository import QueueRepository

    _main, worker = app_env
    created = client.post("/jobs", json={"image_path": SAMPLE}).json()

    with worker.engine.begin() as conn:
        QueueRepository.claim_next_job(conn)

    # claimed_at is "now" from the claim above -> well within the stuck
    # window, so an explicit reclaim pass must not touch it.
    with worker.engine.begin() as conn:
        QueueRepository.reclaim_stuck_jobs(
            conn,
            cutoff=datetime.now(timezone.utc) - timedelta(seconds=worker.STUCK_TIMEOUT_SECONDS),
        )

    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "processing"


# 10. Database concurrency/locking -------------------------------------------

def test_concurrent_job_creation_does_not_deadlock_or_corrupt(client, app_env):
    from app.db.session import SessionLocal
    from app.repositories.job_repository import JobRepository

    errors = []

    def create_one():
        try:
            session = SessionLocal()
            JobRepository(session).create(image_path=SAMPLE)
            session.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=create_one) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent writes raised: {errors}"

    resp = client.get("/jobs", params={"page_size": 100})
    assert resp.json()["total"] == 20


# 11. API unavailable ---------------------------------------------------------

def test_client_request_to_unreachable_api_raises_connection_error():
    """Documents expected client-side behavior (used by stress_test.py) when
    the API process isn't up: a connection error, not a silent hang/crash."""
    import requests

    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get("http://127.0.0.1:1/jobs", timeout=1)


# 12. Timeout/long-running job (no enforced timeout -- documented behavior) --

def test_long_running_job_completes_without_being_killed(client, app_env, monkeypatch):
    _main, worker = app_env
    created = client.post("/jobs", json={"image_path": SAMPLE}).json()

    real_compute_blur = worker.compute_blur

    def slow_compute_blur(path):
        time.sleep(0.2)  # stands in for a slow decode; worker enforces no timeout
        return real_compute_blur(path)

    monkeypatch.setattr(worker, "compute_blur", slow_compute_blur)
    assert worker.process_once() is True

    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "done"


# 13. Empty IMAGE_DIR ----------------------------------------------------------

def test_empty_image_dir_still_resolves_paths_without_crashing(app_env, tmp_path, monkeypatch):
    """Point IMAGE_DIR at a directory with zero files. Path resolution
    itself must not crash; the missing-file failure surfaces later, at
    worker processing time (same as case 2)."""
    import app.core.config as config
    import app.services.job_service as job_service

    empty_dir = tmp_path / "empty_images"
    empty_dir.mkdir()
    monkeypatch.setenv("IMAGE_DIR", str(empty_dir))
    config.get_settings.cache_clear()

    resolved = job_service.resolve_image_path("anything.png")
    assert resolved == str(empty_dir / "anything.png")

    config.get_settings.cache_clear()  # don't leak this override past the test


# 14. Path traversal / security validation ------------------------------------

def test_absolute_path_outside_image_dir_rejected(client):
    resp = client.post("/jobs", json={"image_path": "/etc/passwd"})
    assert resp.status_code == 400


def test_path_traversal_via_multiple_dotdot_rejected(client):
    resp = client.post("/jobs", json={"image_path": "subdir/../../../../etc/passwd"})
    assert resp.status_code == 400


def test_path_within_image_dir_subdirectory_is_allowed(client, app_env):
    """Sanity check the traversal guard isn't overly strict: a real
    subdirectory inside IMAGE_DIR should still be accepted."""
    from app.core.config import get_settings

    image_dir = get_settings().image_dir
    subdir = os.path.join(image_dir, "nested")
    os.makedirs(subdir, exist_ok=True)
    nested_file = os.path.join(subdir, "nested_copy.png")
    shutil.copy(os.path.join(image_dir, SAMPLE), nested_file)

    try:
        resp = client.post("/jobs", json={"image_path": "nested/nested_copy.png"})
        assert resp.status_code == 201
    finally:
        os.remove(nested_file)
        os.rmdir(subdir)
