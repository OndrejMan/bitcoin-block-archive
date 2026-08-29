"""Runtime configuration and its defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BLOCK_DIR = Path("/var/lib/bitcoin/blocks")
DEFAULT_STATE_DIR = Path("/var/lib/bitcoin/.s3-archive")
DEFAULT_BITCOIN_DATADIR = Path("/var/lib/bitcoin")
DEFAULT_BITCOIN_CLI = "bitcoin-cli"

DEFAULT_S3_ENDPOINT = "https://s3.cl4.du.cesnet.cz"
DEFAULT_S3_PROFILE = "coinjoin"
DEFAULT_S3_DESTINATION = os.environ.get(
    "S3_DESTINATION", "s3://xman-coinjoin/bitcoin-mainnet/blocks"
)

DEFAULT_KEEP_LATEST_FILES = 2
DEFAULT_MIN_FREE_SPACE = 0  # bytes; 0 disables the watchdog
DEFAULT_RPC_TIMEOUT = 60
DEFAULT_UPLOAD_TIMEOUT = 3600
DEFAULT_VERIFY_TIMEOUT = 60

LOCK_FILE_NAME = "archive.lock"


def default_credentials() -> Path:
    return Path.home() / ".aws" / "credentials"


@dataclass(frozen=True)
class Config:
    block_dir: Path
    state_dir: Path

    s3_endpoint: str
    s3_profile: str
    s3_credentials: Path
    s3_destination: str

    keep_latest_files: int
    stop_bitcoin_on_error: bool

    prune_after_archive: bool
    min_free_space: int

    bitcoin_cli: str
    bitcoin_datadir: Path

    rpc_timeout: int = DEFAULT_RPC_TIMEOUT
    upload_timeout: int = DEFAULT_UPLOAD_TIMEOUT
    verify_timeout: int = DEFAULT_VERIFY_TIMEOUT

    def __post_init__(self) -> None:
        if self.keep_latest_files < 0:
            raise ValueError("keep_latest_files must not be negative")
        if self.min_free_space < 0:
            raise ValueError("min_free_space must not be negative")
        for name, value in (
            ("rpc_timeout", self.rpc_timeout),
            ("upload_timeout", self.upload_timeout),
            ("verify_timeout", self.verify_timeout),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")

    @property
    def lock_path(self) -> Path:
        return self.state_dir / LOCK_FILE_NAME

    def remote_url(self, name: str) -> str:
        """Object URL of `name` under the configured destination prefix."""
        return f"{self.s3_destination.rstrip('/')}/{name}"
