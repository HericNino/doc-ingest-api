import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """All settings come from environment variables (12-factor app).

    Local (Azurite):  AZURE_STORAGE_CONNECTION_STRING
    Azure:            AZURE_STORAGE_ACCOUNT_URL + managed identity (no secrets)
    """

    connection_string: str | None
    account_url: str | None
    container: str
    create_container: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            connection_string=os.getenv("AZURE_STORAGE_CONNECTION_STRING") or None,
            account_url=os.getenv("AZURE_STORAGE_ACCOUNT_URL") or None,
            container=os.getenv("STORAGE_CONTAINER", "ingest"),
            create_container=os.getenv("CREATE_CONTAINER", "false").lower() == "true",
        )
