# Doc Ingest API

A small asynchronous document-ingestion API on Azure: you submit text, a background job splits it into overlapping chunks (the *chunk* step of a RAG ingestion pipeline), and the job state and results are stored in Azure Blob Storage.

The point of the project is less the chunking than the delivery around it: a container image, local development against an emulator, infrastructure as code, managed identity instead of secrets, and CI/CD.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs` | Submit `{"text": "...", "chunk_size": 500, "overlap": 50}`. Returns `202 Accepted` and a `Location` header. |
| `GET` | `/jobs/{id}` | Job status: `queued` → `running` → `succeeded` / `failed` |
| `GET` | `/jobs/{id}/result` | Chunks, once the job has succeeded (`409` before that) |
| `GET` | `/healthz` | Liveness: the process is up |
| `GET` | `/readyz` | Readiness: storage is reachable |

Interactive docs: `http://localhost:8000/docs`

## Run locally

**Option A: containers (API + Azurite emulator)** — needs Docker Desktop, OrbStack or Podman

```bash
docker compose up --build
```

**Option B: plain Python 3.12+** (macOS / Linux; on Windows use `.venv\Scripts\activate`)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest

# Storage emulator in a second terminal (needs Node.js):
npx azurite-blob --skipApiVersionCheck --location .azurite

cp .env.example .env
uvicorn app.main:app --env-file .env --reload
```

**Try it**

```bash
JOB=$(curl -s -X POST localhost:8000/jobs -H 'content-type: application/json' \
  -d '{"text": "Krk is the largest island in the Adriatic.", "chunk_size": 50, "overlap": 0}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["job_id"])')
curl -s localhost:8000/jobs/$JOB
curl -s localhost:8000/jobs/$JOB/result
```

> **Apple Silicon note:** an image built on an M-series Mac is `linux/arm64`, while Azure Container Apps runs `linux/amd64`. Pushing a Mac-built image fails at startup with `exec format error`. Images for Azure are therefore built in CI on amd64 runners (or locally with `docker buildx build --platform linux/amd64`).

## Design decisions

- **Stateless API.** Job status lives in Blob Storage, not in process memory, so any replica can answer any request and the app can scale out.
- **Liveness ≠ readiness.** `/healthz` never touches storage. If it did, a storage outage would make the platform restart perfectly healthy containers. `/readyz` checks storage and only takes the replica out of rotation.
- **Fail fast when storage is down.** The Azure Storage SDK's default retry policy backs off from 15 s, so a request hung for ~85 s before failing. With a short back-off and an explicit `503`, `/readyz` answers in 3 s and writes fail in ~16 s.
- **No secrets in Azure.** Locally the app uses the Azurite connection string; in Azure it uses `AZURE_STORAGE_ACCOUNT_URL` plus a managed identity (`DefaultAzureCredential`).
- **Input validation at the edge.** Job IDs are typed as UUIDs, so a path like `../something` can never reach a blob name.
- **Non-root, multi-stage image.** The runtime image contains only the virtualenv and the app code.

## Tests

```bash
pytest
```

Unit tests cover chunking edge cases (word boundaries, overlap, text with no spaces) and the API lifecycle against an in-memory store. The app was also tested end-to-end against Azurite, including a storage outage and recovery.

## Roadmap

- [x] API + tests
- [x] Dockerfile + compose with Azurite
- [ ] Bicep: Storage + Container Apps + managed identity (RBAC)
- [ ] GitHub Actions: test → build → push → deploy (OIDC, no stored credentials)
- [ ] Move background work to a queue-triggered worker (Service Bus / Storage Queue)
