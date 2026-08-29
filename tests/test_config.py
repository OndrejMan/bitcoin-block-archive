from __future__ import annotations

from dataclasses import replace

import pytest

from bitcoin_block_archive.config import Config


def test_remote_url_strips_trailing_slash(config: Config) -> None:
    assert config.remote_url("blk00000.dat") == ("s3://bucket/prefix/blk00000.dat")


def test_lock_path_lives_in_state_dir(config: Config) -> None:
    assert config.lock_path.parent == config.state_dir
    assert config.lock_path.name == "archive.lock"


def test_direct_config_rejects_invalid_limits(config: Config) -> None:
    with pytest.raises(ValueError, match="keep_latest_files"):
        replace(config, keep_latest_files=-1)
    with pytest.raises(ValueError, match="min_free_space"):
        replace(config, min_free_space=-1)
    with pytest.raises(ValueError, match="rpc_timeout"):
        replace(config, rpc_timeout=0)
    with pytest.raises(ValueError, match="upload_timeout"):
        replace(config, upload_timeout=-1)
    with pytest.raises(ValueError, match="verify_timeout"):
        replace(config, verify_timeout=0)
