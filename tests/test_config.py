from __future__ import annotations

from bitcoin_block_archive.config import Config


def test_remote_url_strips_trailing_slash(config: Config) -> None:
    assert config.remote_url("blk00000.dat") == ("s3://bucket/prefix/blk00000.dat")


def test_lock_path_lives_in_state_dir(config: Config) -> None:
    assert config.lock_path.parent == config.state_dir
    assert config.lock_path.name == "archive.lock"
