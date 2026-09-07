# Job Processing Service

FastAPI + SQLite(/Postgres via SQLAlchemy) job queue with parallel worker
processes, guaranteeing exactly-once processing. See `DESIGN.md` for how,
and for the repository/service/controller layering.

## How it works

```mermaid
flowchart LR
    Client(["Client"])
    API["FastAPI\napp/main.py"]
    Svc["JobService\n(business logic)"]
    Repo["JobRepository\n(ORM CRUD)"]
    Queue[("jobs table = QUEUE\npending -> processing -> done/failed")]
    QRepo["QueueRepository\n(atomic claim/mark,\none txn each)"]
    W1["worker #1"]
    W2["worker #2"]
    W3["worker #N"]
    Blur["BlurService\nOpenCV Laplacian variance"]
    Img[("IMAGE_DIR")]

    Client -- "POST /jobs\nGET /jobs/{id}" --> API
    API --> Svc --> Repo --> Queue
    Queue <--> QRepo
    QRepo -- "claim_next_job\n(UPDATE...RETURNING)" --> W1
    QRepo --> W2
    QRepo --> W3
    W1 --> Blur
    W2 --> Blur
    W3 --> Blur
    Blur -- reads --> Img
    Blur -- "mark_done/mark_failed/mark_retry" --> QRepo
```

Any number of `app.worker` processes can run against the same DB/queue at
once — the atomic claim UPDATE guarantees each job is picked up by exactly
one of them (see "Exactly-once processing" in `DESIGN.md`).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Configure where your images live — copy `.env.example` to `.env` and set
`IMAGE_DIR`:

```bash
cp .env.example .env
# edit .env, e.g.: IMAGE_DIR=/home/you/Pictures/test-images
```

Images are **not** committed to this repo (kept out via `.gitignore`) so it
doesn't get bloated with binary files — `IMAGE_DIR` is the one thing you
need to point at your own folder. `POST /jobs`' `image_path` is resolved
relative to `IMAGE_DIR` (e.g. `"cat.jpg"` -> `$IMAGE_DIR/cat.jpg`); a path
that escapes `IMAGE_DIR` (e.g. `"../../etc/passwd"`) is rejected with 400.

For a quick try, or to run the tests/stress test, generate the bundled
deterministic sample images locally (same output every run, nothing to
commit). This one-off script uses Pillow, which isn't in `requirements.txt`
(the app itself only needs OpenCV) — install it once if you need to run this:

```bash
pip install Pillow
python tools/generate_sample_images.py   # writes into ./sample_images
```

If you leave `IMAGE_DIR` unset it defaults to `./sample_images`.

## Run

Start the API (creates `jobs.db` on first run):

```bash
uvicorn app.main:app --reload
```

In separate terminals, start 3 worker processes against the same DB and
IMAGE_DIR:

```bash
python -m app.worker
python -m app.worker
python -m app.worker
```

(Or in the background: `python -m app.worker & python -m app.worker & python -m app.worker &`)

## Try it

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"image_path": "sharp_checkerboard.png"}'
# => {"id": "...", "status": "pending", ...}

curl http://127.0.0.1:8000/jobs/<id>

curl "http://127.0.0.1:8000/jobs?status=done"
```

## Tests

```bash
pytest
```

Tests point `IMAGE_DIR` at the repo's `sample_images/` themselves (see
`tests/conftest.py`) — generate that folder first if you haven't:
`python tools/generate_sample_images.py`.

## Stress test

With the API and 3 workers already running (as above, same `IMAGE_DIR`):

```bash
python stress_test.py
```

Submits 100 jobs, polls up to 60s for all to finish, and asserts total
count, deterministic `is_blurry`/`sharpness_score` per image, and
`attempts == 1` for every job. Prints `PASS`/`FAIL`.

## Project layout

```
app/
  api/routes/jobs.py     controller: HTTP <-> service, request/response only
  services/               business logic (job_service, blur_service)
  repositories/           data access + the atomic queue-claim SQL
  db/                      SQLAlchemy engine/session/models
  core/config.py           env-var settings (DATABASE_URL, IMAGE_DIR)
  main.py                  FastAPI app wiring
  worker.py                worker CLI (python -m app.worker)
```

## Config (env vars / `.env`)

- `DATABASE_URL` (default `sqlite:///./jobs.db`). Set to a Postgres URL
  (e.g. `postgresql://user:pass@host/db`) to run against Postgres instead
  — see the note in `DESIGN.md` about the claim query equivalent there
  (`SELECT ... FOR UPDATE SKIP LOCKED`, not implemented in code; SQLite's
  `UPDATE ... RETURNING` is used as shipped).
- `IMAGE_DIR` (default `./sample_images`). Base directory `image_path`
  values are resolved against.
