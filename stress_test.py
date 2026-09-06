"""Standalone stress test.

Assumes the API and 3 (or more) `python -m app.worker` processes are
already running against the same DB and the same IMAGE_DIR (see
README.md for exact commands).

Submits 100 jobs (cycling through IMAGE_DIR's *.png files), polls until
all are done/failed (60s timeout), and asserts:
  - total jobs == 100
  - each result matches the known-correct, deterministic value for its
    input image (computed locally with the same code path the worker uses)
  - attempts == 1 for every job (each job processed exactly once)

Usage:
    export IMAGE_DIR=./sample_images   # must match the API/workers' IMAGE_DIR
    python -m app.worker &   # x3
    uvicorn app.main:app &
    python stress_test.py
"""

import glob
import os
import sys
import time

import requests

API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000")
NUM_JOBS = 100
POLL_INTERVAL_SECONDS = 1
TIMEOUT_SECONDS = 60


def load_expected_results():
    """Compute ground truth locally with the same code path the worker
    uses, so this script has no hardcoded numbers to drift out of sync.

    Must run with the same IMAGE_DIR env var as the API/workers, so the
    filenames submitted here resolve to the same files on both sides.
    """
    from app.core.config import get_settings
    from app.services.blur_service import compute_blur

    image_dir = get_settings().image_dir
    extensions = ("*.png", "*.jpg", "*.jpeg", "*.bmp")
    paths = sorted(
        p for ext in extensions for p in glob.glob(os.path.join(image_dir, ext))
    )
    if not paths:
        print(f"FAIL: no images ({', '.join(extensions)}) found in IMAGE_DIR={image_dir}")
        sys.exit(1)

    filenames = [os.path.basename(p) for p in paths]
    expected = {name: compute_blur(path) for name, path in zip(filenames, paths)}
    return filenames, expected


def submit_jobs(filenames):
    job_ids = []
    for i in range(NUM_JOBS):
        filename = filenames[i % len(filenames)]
        resp = requests.post(f"{API_URL}/jobs", json={"image_path": filename})
        resp.raise_for_status()
        body = resp.json()
        job_ids.append((body["id"], filename))
    return job_ids


def poll_until_terminal(job_ids):
    remaining = {job_id for job_id, _ in job_ids}
    finished = {}
    deadline = time.time() + TIMEOUT_SECONDS

    while remaining and time.time() < deadline:
        for job_id in list(remaining):
            resp = requests.get(f"{API_URL}/jobs/{job_id}")
            resp.raise_for_status()
            body = resp.json()
            if body["status"] in ("done", "failed"):
                finished[job_id] = body
                remaining.discard(job_id)
        if remaining:
            time.sleep(POLL_INTERVAL_SECONDS)

    return finished, remaining


def print_report(job_ids, finished, remaining, elapsed_seconds):
    total = len(job_ids)
    done = sum(1 for b in finished.values() if b["status"] == "done")
    failed = sum(1 for b in finished.values() if b["status"] == "failed")
    blurry = sum(1 for b in finished.values() if b["status"] == "done" and (b.get("result") or {}).get("is_blurry"))
    sharp = sum(1 for b in finished.values() if b["status"] == "done" and (b.get("result") or {}).get("is_blurry") is False)
    attempts_list = [b["attempts"] for b in finished.values()]
    retried = sum(1 for a in attempts_list if a > 1)
    throughput = total / elapsed_seconds if elapsed_seconds > 0 else 0.0

    print()
    print("=" * 60)
    print("STRESS TEST REPORT")
    print("=" * 60)
    print(f"{'Total jobs submitted':<30}{total}")
    print(f"{'Finished (done + failed)':<30}{len(finished)}")
    print(f"{'Still pending/processing':<30}{len(remaining)}")
    print(f"{'  done':<30}{done}")
    print(f"{'  failed':<30}{failed}")
    print(f"{'  sharp (is_blurry=false)':<30}{sharp}")
    print(f"{'  blurry (is_blurry=true)':<30}{blurry}")
    print(f"{'Jobs needing a retry (>1)':<30}{retried}")
    print(f"{'Elapsed time':<30}{elapsed_seconds:.2f}s")
    print(f"{'Throughput':<30}{throughput:.2f} jobs/sec")
    print("=" * 60)


def main():
    filenames, expected = load_expected_results()
    print(f"Submitting {NUM_JOBS} jobs against {API_URL} using {len(filenames)} sample images...")

    start = time.time()
    job_ids = submit_jobs(filenames)
    finished, remaining = poll_until_terminal(job_ids)
    elapsed = time.time() - start

    print_report(job_ids, finished, remaining, elapsed)

    failures = []

    if len(job_ids) != NUM_JOBS:
        failures.append(f"expected {NUM_JOBS} submitted jobs, got {len(job_ids)}")

    if remaining:
        failures.append(f"{len(remaining)} jobs did not finish within {TIMEOUT_SECONDS}s")

    for job_id, filename in job_ids:
        body = finished.get(job_id)
        if body is None:
            continue  # already reported above as a timeout

        if body["status"] != "done":
            failures.append(f"job {job_id} ({filename}) status={body['status']}, expected done")
            continue

        want = expected[filename]
        got = body["result"] or {}
        if got.get("is_blurry") != want["is_blurry"]:
            failures.append(
                f"job {job_id} ({filename}) is_blurry={got.get('is_blurry')}, "
                f"expected {want['is_blurry']}"
            )
        if got.get("sharpness_score") != want["sharpness_score"]:
            failures.append(
                f"job {job_id} ({filename}) sharpness_score={got.get('sharpness_score')}, "
                f"expected {want['sharpness_score']}"
            )
        if body["attempts"] != 1:
            failures.append(
                f"job {job_id} ({filename}) attempts={body['attempts']}, expected 1"
            )

    print()
    if failures:
        print(f"FAIL: {len(failures)} problem(s) found")
        for f in failures[:50]:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print(f"PASS: all {NUM_JOBS} jobs processed exactly once with correct, deterministic results")
        sys.exit(0)


if __name__ == "__main__":
    main()
