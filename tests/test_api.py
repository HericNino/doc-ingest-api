import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import InMemoryStore


@pytest.fixture
def client():
    # TestClient runs background tasks right after the response,
    # so the job is already finished when we ask for its status.
    with TestClient(create_app(store=InMemoryStore())) as c:
        yield c


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz(client):
    assert client.get("/readyz").status_code == 200


def test_job_lifecycle(client):
    text = " ".join(["Krk"] * 400)
    r = client.post("/jobs", json={"text": text, "chunk_size": 200, "overlap": 20})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    assert r.headers["Location"] == f"/jobs/{job_id}"

    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "succeeded"
    assert status["chunk_count"] > 1

    result = client.get(f"/jobs/{job_id}/result").json()
    assert len(result["chunks"]) == status["chunk_count"]
    assert result["chunks"][0]["index"] == 0


def test_unknown_job_is_404(client):
    assert client.get(f"/jobs/{uuid.uuid4()}").status_code == 404


def test_non_uuid_job_id_rejected(client):
    assert client.get("/jobs/..%2Fsecret").status_code in (404, 422)
    assert client.get("/jobs/not-a-uuid").status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"text": ""},
        {"text": "abc", "chunk_size": 10},
        {"text": "abc", "chunk_size": 100, "overlap": 100},
    ],
)
def test_validation(client, body):
    assert client.post("/jobs", json=body).status_code == 422


def test_result_before_success_is_409():
    store = InMemoryStore()
    with TestClient(create_app(store=store)) as c:
        job_id = str(uuid.uuid4())
        store.blobs[f"jobs/{job_id}.json"] = (
            '{"job_id": "%s", "status": "running", '
            '"created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}' % job_id
        )
        assert c.get(f"/jobs/{job_id}/result").status_code == 409
