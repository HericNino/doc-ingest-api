import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

from azure.core.exceptions import AzureError
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from .chunking import chunk_text
from .config import Settings
from .store import BlobStore, JobStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("doc_ingest")
# At INFO level the Azure SDK logs every HTTP request with headers — too noisy.
logging.getLogger("azure").setLevel(logging.WARNING)

JobState = Literal["queued", "running", "succeeded", "failed"]


class JobRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1_000_000)
    chunk_size: int = Field(default=500, ge=50, le=8000)
    overlap: int = Field(default=50, ge=0)

    @model_validator(mode="after")
    def overlap_smaller_than_chunk(self) -> "JobRequest":
        if self.overlap >= self.chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        return self


class JobStatus(BaseModel):
    job_id: str
    status: JobState
    created_at: datetime
    updated_at: datetime
    chunk_count: int | None = None
    error: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _status_blob(job_id: str) -> str:
    return f"jobs/{job_id}.json"


def _input_blob(job_id: str) -> str:
    return f"inputs/{job_id}.json"


def _result_blob(job_id: str) -> str:
    return f"results/{job_id}.json"


async def _save_status(store: JobStore, job: JobStatus) -> None:
    job.updated_at = _now()
    await store.put_json(_status_blob(job.job_id), job.model_dump(mode="json"))


async def run_job(store: JobStore, job: JobStatus) -> None:
    """Background work. Reads its input from Blob, not from memory, so it can
    later move to a separate worker behind a queue (Service Bus / Storage Queue)."""
    try:
        job.status = "running"
        await _save_status(store, job)
        payload = await store.get_json(_input_blob(job.job_id))
        if payload is None:
            raise RuntimeError("input document not found")
        req = JobRequest(**payload)
        chunks = chunk_text(req.text, req.chunk_size, req.overlap)
        await store.put_json(
            _result_blob(job.job_id),
            {
                "job_id": job.job_id,
                "chunk_size": req.chunk_size,
                "overlap": req.overlap,
                "chunks": [{"index": i, "length": len(c), "text": c} for i, c in enumerate(chunks)],
            },
        )
        job.status = "succeeded"
        job.chunk_count = len(chunks)
        log.info("Job %s succeeded: %d chunks", job.job_id, len(chunks))
    except Exception as exc:  # noqa: BLE001 — a job must end up 'failed', never stuck
        log.exception("Job %s failed", job.job_id)
        job.status = "failed"
        job.error = str(exc)
    try:
        await _save_status(store, job)
    except Exception:  # noqa: BLE001 — storage itself is down; nothing more we can record
        log.exception("Could not save final status for job %s", job.job_id)


def create_app(store: JobStore | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if store is not None:
            app.state.store = store
        else:
            settings = Settings.from_env()
            blob_store = BlobStore.from_settings(settings)
            try:
                await blob_store.init(settings.create_container)
            except Exception:
                await blob_store.close()  # otherwise the HTTP session leaks
                raise
            app.state.store = blob_store
        yield
        await app.state.store.close()

    app = FastAPI(title="Doc Ingest API", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(AzureError)
    async def storage_unavailable(request: Request, exc: AzureError) -> JSONResponse:
        # A storage outage is a temporary condition (503), not a bug in our code (500).
        log.warning("Storage error on %s: %s", request.url.path, exc)
        return JSONResponse({"detail": "storage unavailable"}, status_code=503)

    def get_store(request: Request) -> JobStore:
        return request.app.state.store

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """Liveness: the process is alive. Deliberately does not touch storage —
        otherwise a storage outage would make the platform restart healthy containers."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    async def readyz(request: Request) -> dict[str, str]:
        """Readiness: should this replica receive traffic? Checks storage."""
        try:
            await get_store(request).ping()
        except Exception as exc:  # noqa: BLE001
            log.warning("Readiness check failed: %r", exc)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "storage unavailable") from exc
        return {"status": "ready"}

    @app.post("/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=JobStatus, tags=["jobs"])
    async def create_job(
        body: JobRequest, background: BackgroundTasks, request: Request, response: Response
    ) -> JobStatus:
        store = get_store(request)
        now = _now()
        job = JobStatus(job_id=str(uuid.uuid4()), status="queued", created_at=now, updated_at=now)

        await store.put_json(_input_blob(job.job_id), body.model_dump())
        await _save_status(store, job)
        background.add_task(run_job, store, job.model_copy())

        response.headers["Location"] = f"/jobs/{job.job_id}"
        return job

    @app.get("/jobs/{job_id}", response_model=JobStatus, tags=["jobs"])
    async def get_job(job_id: uuid.UUID, request: Request) -> Any:
        # Typed as UUID, so FastAPI rejects anything else — nobody can smuggle
        # "../something" into a blob name.
        data = await get_store(request).get_json(_status_blob(str(job_id)))
        if data is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        return data

    @app.get("/jobs/{job_id}/result", tags=["jobs"])
    async def get_result(job_id: uuid.UUID, request: Request) -> Any:
        store = get_store(request)
        job = await store.get_json(_status_blob(str(job_id)))
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        if job["status"] != "succeeded":
            raise HTTPException(status.HTTP_409_CONFLICT, f"job is '{job['status']}'")
        return await store.get_json(_result_blob(str(job_id)))

    return app


app = create_app()
