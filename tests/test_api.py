SAMPLE = "sharp_checkerboard.png"  # resolved against IMAGE_DIR (see conftest.py)


def test_create_job(client):
    resp = client.post("/jobs", json={"image_path": SAMPLE})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["attempts"] == 0
    assert body["result"] is None
    assert "id" in body and body["id"]


def test_get_job(client):
    created = client.post("/jobs", json={"image_path": SAMPLE}).json()
    resp = client.get(f"/jobs/{created['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["status"] == "pending"
    assert "created_at" in body and "updated_at" in body


def test_get_job_not_found(client):
    resp = client.get("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_create_job_rejects_path_escaping_image_dir(client):
    resp = client.post("/jobs", json={"image_path": "../../etc/passwd"})
    assert resp.status_code == 400


def test_list_jobs_with_status_filter(client):
    client.post("/jobs", json={"image_path": SAMPLE})
    client.post("/jobs", json={"image_path": SAMPLE})

    resp = client.get("/jobs", params={"status": "pending"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(item["status"] == "pending" for item in body["items"])

    resp = client.get("/jobs", params={"status": "done"})
    assert resp.json()["total"] == 0
