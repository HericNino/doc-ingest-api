"""Storage for job state and results.

The API is deliberately stateless: job status lives in Blob Storage, not in
process memory, so Container Apps can run several replicas that all see the
same state.
"""

import asyncio
import json
import logging
from typing import Any, Protocol

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings

# Note: the async client needs the async retry policy from .aio — the sync one breaks at runtime.
from azure.storage.blob.aio import BlobServiceClient, ExponentialRetry

from .config import Settings

log = logging.getLogger(__name__)

# The default storage retry policy starts with a 15 s back-off (15 s, 18 s, 24 s...),
# so with storage unreachable every request hangs for ~85 s. Too long for an API
# someone is waiting on: use a short back-off and fail fast with a 503.
CLIENT_OPTIONS: dict[str, Any] = {
    "retry_policy": ExponentialRetry(
        initial_backoff=1, increment_base=2, retry_total=3, random_jitter_range=1
    ),
    "connection_timeout": 5,
    "read_timeout": 30,
}
PING_TIMEOUT_S = 3


class JobStore(Protocol):
    async def put_json(self, name: str, data: dict[str, Any]) -> None: ...
    async def get_json(self, name: str) -> dict[str, Any] | None: ...
    async def ping(self) -> None: ...
    async def close(self) -> None: ...


class InMemoryStore:
    """Test double with the same interface as BlobStore, no network."""

    def __init__(self) -> None:
        self.blobs: dict[str, str] = {}

    async def put_json(self, name: str, data: dict[str, Any]) -> None:
        self.blobs[name] = json.dumps(data, default=str)

    async def get_json(self, name: str) -> dict[str, Any] | None:
        raw = self.blobs.get(name)
        return json.loads(raw) if raw is not None else None

    async def ping(self) -> None:
        return None

    async def close(self) -> None:
        return None


class BlobStore:
    def __init__(self, service: BlobServiceClient, container: str, credential: Any = None) -> None:
        self._service = service
        self._credential = credential
        self._container = service.get_container_client(container)

    @classmethod
    def from_settings(cls, settings: Settings) -> "BlobStore":
        if settings.connection_string:
            # Local: Azurite emulator via connection string.
            service = BlobServiceClient.from_connection_string(
                settings.connection_string, **CLIENT_OPTIONS
            )
            return cls(service, settings.container)

        if settings.account_url:
            # Azure: managed identity, no secrets. DefaultAzureCredential tries
            # env vars, managed identity, Azure CLI login, etc. in order.
            from azure.identity.aio import DefaultAzureCredential

            credential = DefaultAzureCredential()
            service = BlobServiceClient(settings.account_url, credential=credential, **CLIENT_OPTIONS)
            return cls(service, settings.container, credential)

        raise RuntimeError(
            "Set AZURE_STORAGE_CONNECTION_STRING (local) or AZURE_STORAGE_ACCOUNT_URL (Azure)."
        )

    async def init(self, create_container: bool) -> None:
        if not create_container:
            return
        try:
            await self._container.create_container()
            log.info("Created container %s", self._container.container_name)
        except ResourceExistsError:
            pass

    async def put_json(self, name: str, data: dict[str, Any]) -> None:
        blob = self._container.get_blob_client(name)
        await blob.upload_blob(
            json.dumps(data, default=str),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )

    async def get_json(self, name: str) -> dict[str, Any] | None:
        blob = self._container.get_blob_client(name)
        try:
            stream = await blob.download_blob()
            return json.loads(await stream.readall())
        except ResourceNotFoundError:
            return None

    async def ping(self) -> None:
        # Readiness has to answer quickly; the platform has its own probe timeout anyway.
        await asyncio.wait_for(self._container.get_container_properties(), PING_TIMEOUT_S)

    async def close(self) -> None:
        await self._service.close()
        if self._credential is not None:
            await self._credential.close()
