# Design

## Architecture

```
app/api/routes/jobs.py   controller: HTTP request/response only, no logic
        |
app/services/job_service.py    business logic (resolve image path, map to schema)
app/services/blur_service.py   blur detection (Laplacian variance)
        |
app/repositories/job_repository.py   data access + the atomic queue-claim SQL
        |
app/db/ (session.py, models.py)      SQLAlchemy engine/session/Job model
```

```
POST /jobs  ->  controller  ->  JobService.create_job  ->  JobRepository.create
                                                     |
                              SQLite/Postgres "jobs" table = the queue
                                                     |
        python -m app.worker  (N processes)  <------+
              QueueRepository.claim_next_job (atomic UPDATE...RETURNING)
              -> BlurService.compute_blur
              -> QueueRepository.mark_done / mark_failed / mark_retry

GET /jobs, /jobs/{id}  ->  controller -> JobService -> JobRepository (read)
```

There is no separate broker (Redis/RabbitMQ/etc). The `jobs` table *is* the
queue: `status='pending'` rows are the backlog, and "dequeue" is an atomic
claim UPDATE (`QueueRepository.claim_next_job`, in
`app/repositories/job_repository.py`). This is the hinted approach in the
assignment and needs zero extra infrastructure.

`JobRepository` (session-based CRUD, used by the API) and `QueueRepository`
(raw-connection atomic ops, used by the worker) are split because they need
different transaction semantics: the API does ordinary ORM reads/writes,
while the worker's claim/mark operations must each be one atomic SQL
statement in one transaction — mixing them into a single ORM Session would
make that atomicity accidental instead of explicit.

## Exactly-once processing

The core guarantee lives in one SQL statement, `CLAIM_SQL` in
`app/repositories/job_repository.py`:

```sql
UPDATE jobs
SET status = 'processing', attempts = attempts + 1, claimed_at = :now
WHERE id = (
    SELECT id FROM jobs
    WHERE status = 'pending' AND (run_at IS NULL OR run_at <= :now)
    ORDER BY created_at LIMIT 1
)
AND status = 'pending'
RETURNING id, image_path, attempts
```

A worker never does a separate SELECT-then-UPDATE — that would leave a gap
where two workers could both read the same pending row before either writes
back. Instead the SELECT is a subquery *inside* the UPDATE's WHERE clause,
and the trailing `AND status = 'pending'` is a repeated guard so the write
only takes effect if the row is still pending at write time. All of this
runs inside one transaction (`engine.begin()`), so it's atomic: either
this worker fully claims the row and every other worker's UPDATE matches
zero rows (`RETURNING` gives no row back, so the worker just sees "nothing
to claim" and moves on), or it doesn't touch the row at all.

SQLite makes this safe across separate *processes*, not just threads: only
one write transaction can be active against the database file at a time —
concurrent writers block on the file lock and retry (`PRAGMA busy_timeout`,
set in `app/db/session.py`, controls how long they wait before erroring out) rather
than interleaving. So the claim UPDATE from worker A and the claim UPDATE
from worker B for the same row can never both partially apply; one commits
first and the other's `WHERE status = 'pending'` then matches nothing.
`PRAGMA journal_mode=WAL` is enabled so readers (the API's GET endpoints)
aren't blocked while a worker holds a write transaction.

If this were pointed at Postgres instead (`DATABASE_URL=postgresql://...`),
the same guarantee is expressed as `SELECT id FROM jobs WHERE status =
'pending' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED`, which is the
standard pattern for a DB-table-backed queue there; Postgres's MVCC/row
locks give the same "only one worker gets this row" property without
SQLite's whole-file serialization. The current code targets SQLite per the
assignment's hint and doesn't include the Postgres SQL variant.

`attempts` is incremented as part of the same claim UPDATE, so it always
reflects "how many times a worker has picked this job up," independent of
whether processing then succeeds or fails. For a job that succeeds on its
first pickup, `attempts == 1`, which is what the stress test asserts.

## Bonus features implemented

- **Retries with exponential backoff**: on failure, if `attempts <
  MAX_ATTEMPTS` (3), the job goes back to `pending` with `run_at = now +
  2^(attempts-1)` seconds and the error message recorded in `result`; once
  `attempts` reaches 3 it's marked `failed` instead. (`app/worker.py`)
- **Crash recovery**: before each claim attempt, a worker resets any job
  that's been sitting in `processing` for more than `STUCK_TIMEOUT_SECONDS`
  (60s) back to `pending`, so a worker that dies mid-job doesn't strand it
  forever. (`QueueRepository.reclaim_stuck_jobs` in
  `app/repositories/job_repository.py`, covered by
  `tests/test_worker.py::test_reclaim_stuck_job_after_timeout`)

  **Known limitation**: this treats "stuck in `processing` for
  `STUCK_TIMEOUT_SECONDS`" as a proxy for "the worker crashed" — there's no
  actual liveness check on the worker process. If a job's real processing
  time ever exceeded 60s (blur detection normally doesn't), a second worker
  could reclaim and reprocess a job the first worker is still legitimately
  working on, breaking the exactly-once guarantee for that job (`attempts`
  would end up `>1` even though nothing crashed). Mitigation would be a
  heartbeat column the original worker refreshes while processing, with
  `reclaim_stuck_jobs` checking that instead of a fixed wall-clock cutoff;
  not implemented here since it's low-risk for this workload's processing
  time, but worth flagging as a scaling/robustness follow-up.
- **Scheduled jobs**: `POST /jobs` accepts an optional `run_at` timestamp;
  the claim query only picks up rows where `run_at IS NULL OR run_at <=
  now()`.

## Keeping images out of the repo

`IMAGE_DIR` (env var / `.env`, `app/core/config.py`) is the one thing each
user configures locally. `POST /jobs`' `image_path` is resolved as
`IMAGE_DIR / image_path` (`app/services/job_service.py:resolve_image_path`),
which also rejects any path that escapes `IMAGE_DIR` (e.g. `../../etc/passwd`)
with a 400 — a minimal traversal guard, not a full sandbox. This means the
repo never needs to carry a user's actual image set; `sample_images/` is
`.gitignore`d and regenerated on demand by
`tools/generate_sample_images.py` for anyone who wants to run the tests or
stress test without supplying their own images.

## Assumptions

- `image_path` is a path readable by the worker process (same filesystem,
  or a shared volume in a multi-host deployment); no upload/storage layer
  is in scope. It's resolved relative to `IMAGE_DIR`, not an absolute path
  from the client — see "Keeping images out of the repo" above.
- Blur threshold is `variance(Laplacian) < 100.0` → blurry. This is a
  commonly-cited starting point for this metric on normal photographic
  images; it isn't recalibrated per-image-size or per-domain. The bundled
  `sample_images/` are synthetic (checkerboard/noise/stripe patterns, plus
  their Gaussian-blurred versions) precisely so the sharp/blurry split and
  exact variance are deterministic and don't depend on JPEG-compression or
  camera-specific artifacts.
- `python -m app.worker` polls with a 0.5s sleep when the queue is empty
  rather than using a push/notify mechanism — simplest correct choice for
  the stated scale (3 workers, 100 jobs), at the cost of up to 0.5s latency
  before an idle worker picks up a freshly-enqueued job.
- Single SQLite file (`jobs.db`) is assumed to live on local disk, not a
  network filesystem (NFS) — SQLite's locking is unreliable there.
- Pagination defaults: `page=1`, `page_size=20` (max 100) for `GET /jobs`.
- Built and tested on Python 3.10 (the environment available at
  implementation time) rather than 3.11+; nothing in the code is
  3.11-specific.
