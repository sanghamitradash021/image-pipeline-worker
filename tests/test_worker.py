SHARP = "sharp_checkerboard.png"
BLURRY = "blurry_checkerboard.png"
MISSING = "no_such_file.png"  # valid relative path under IMAGE_DIR, file doesn't exist


def test_worker_processes_job_end_to_end(client, app_env):
    _main, worker = app_env

    created = client.post("/jobs", json={"image_path": SHARP}).json()

    did_work = worker.process_once()
    assert did_work is True

    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "done"
    assert job["attempts"] == 1
    assert job["result"]["is_blurry"] is False
    assert job["result"]["sharpness_score"] > 100.0


def test_worker_detects_blurry_image(client, app_env):
    _main, worker = app_env

    created = client.post("/jobs", json={"image_path": BLURRY}).json()
    worker.process_once()

    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "done"
    assert job["attempts"] == 1
    assert job["result"]["is_blurry"] is True


def test_worker_marks_missing_file_failed_after_retries(client, app_env, monkeypatch):
    _main, worker = app_env
    monkeypatch.setattr(worker, "BACKOFF_BASE_SECONDS", 0)  # don't slow the test down

    created = client.post("/jobs", json={"image_path": MISSING}).json()

    # MAX_ATTEMPTS retries, then it should land on 'failed'.
    for _ in range(worker.MAX_ATTEMPTS):
        worker.process_once()

    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "failed"
    assert job["attempts"] == worker.MAX_ATTEMPTS
    assert "error" in job["result"]


def test_no_double_processing_when_queue_empty(client, app_env):
    _main, worker = app_env
    assert worker.process_once() is False


def test_reclaim_stuck_job_after_timeout(client, app_env):
    """A job left in 'processing' past STUCK_TIMEOUT_SECONDS (simulating a
    worker that crashed mid-job) must be reclaimed and finished by the next
    call to process_once(), which runs reclaim_stuck_jobs before claiming."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    _main, worker = app_env
    created = client.post("/jobs", json={"image_path": SHARP}).json()

    stuck_since = datetime.now(timezone.utc) - timedelta(seconds=worker.STUCK_TIMEOUT_SECONDS + 5)
    with worker.engine.begin() as conn:
        conn.execute(
            text("UPDATE jobs SET status = 'processing', claimed_at = :claimed_at WHERE id = :id"),
            {"claimed_at": stuck_since, "id": created["id"]},
        )

    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "processing"  # stuck, not yet reclaimed

    did_work = worker.process_once()
    assert did_work is True

    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "done"
