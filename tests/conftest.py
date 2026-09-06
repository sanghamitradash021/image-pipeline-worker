import importlib
import os

import pytest
from fastapi.testclient import TestClient

SAMPLE_IMAGE_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_images")


@pytest.fixture
def app_env(tmp_path):
    """Point the app at a fresh, isolated SQLite file (and the repo's
    sample_images/ as IMAGE_DIR) for this test, and reload every app
    module that cached settings/engine at import time."""
    db_path = tmp_path / "test_jobs.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["IMAGE_DIR"] = SAMPLE_IMAGE_DIR

    import app.core.config as config
    import app.db.session as session
    import app.db.models as models
    import app.repositories.job_repository as job_repository
    import app.services.blur_service as blur_service
    import app.services.job_service as job_service
    import app.api.routes.jobs as jobs_routes
    import app.worker as worker
    import app.main as main

    config.get_settings.cache_clear()
    for module in (
        config, session, models, job_repository, blur_service,
        job_service, jobs_routes, worker, main,
    ):
        importlib.reload(module)

    session.init_db()

    yield main, worker

    del os.environ["DATABASE_URL"]
    del os.environ["IMAGE_DIR"]


@pytest.fixture
def client(app_env):
    main, _worker = app_env
    return TestClient(main.app)
