"""The parser cursor is a file boundary, not just a chain height."""

from dataclasses import replace

import pytest

from bitcoin_block_archive import bitcoin, cli, prune
from bitcoin_block_archive.errors import ArchiveError, UnsafePruningError
from tests.conftest import FakeClient, fake_header, write_block_file
from bitcoin_block_archive.blockfile import block_hash


def test_rpc_timeout_does_not_request_core_shutdown(config, monkeypatch):
    def timeout(*args, **kwargs):
        raise ArchiveError("RPC timed out")

    monkeypatch.setattr(bitcoin, "_rpc_json", timeout)
    with pytest.raises(ArchiveError) as raised:
        bitcoin.require_safe_pruning(config, archiver_prunes=False)
    assert not isinstance(raised.value, UnsafePruningError)
    monkeypatch.setattr(
        cli,
        "archive",
        lambda *args, **kwargs: bitcoin.require_safe_pruning(
            config, archiver_prunes=False
        ),
    )
    monkeypatch.setattr(
        cli, "stop_bitcoin", lambda *_: pytest.fail("timeout must not stop Core")
    )
    assert not cli.run_pass(config)


def test_retains_cursor_and_newer_files_with_out_of_order_heights(config, monkeypatch):
    config = replace(config, prune_retain_from_file=1)
    known = {}
    # Newer file contains an older block: retaining only file 1's height is unsafe.
    for number, height in enumerate([20, 200, 100]):
        header = fake_header(number + 1)
        write_block_file(config.block_dir / f"blk{number:05d}.dat", [header])
        known[block_hash(header)] = height
    monkeypatch.setattr(prune, "require_safe_pruning", lambda *args, **kwargs: None)
    monkeypatch.setattr(prune, "_verify_archived_local_files", lambda *_: None)
    monkeypatch.setattr(prune, "safe_prune_height", lambda *args, **kwargs: 1000)
    monkeypatch.setattr(prune, "block_height", lambda _, block: known[block])
    calls = []
    monkeypatch.setattr(
        prune, "prune_blockchain", lambda _, height: calls.append(height)
    )
    prune.prune_archived_blocks(config, FakeClient())

    assert calls == [99]


@pytest.mark.parametrize("missing", [True, False])
def test_unknown_resume_boundary_never_prunes(config, monkeypatch, missing):
    config = replace(config, prune_retain_from_file=3)
    if not missing:
        (config.block_dir / "blk00003.dat").write_bytes(bytes(128))
    monkeypatch.setattr(prune, "require_safe_pruning", lambda *args, **kwargs: None)
    monkeypatch.setattr(prune, "_verify_archived_local_files", lambda *_: None)
    monkeypatch.setattr(prune, "safe_prune_height", lambda *args, **kwargs: 1000)
    monkeypatch.setattr(
        prune,
        "prune_blockchain",
        lambda *_: pytest.fail("unknown cursor must not prune"),
    )
    prune.prune_archived_blocks(config, FakeClient())
    with pytest.raises(ArchiveError):
        prune.prune_archived_blocks(config, FakeClient(), strict=True)


def test_prune_only_does_not_run_archive(config, monkeypatch):
    monkeypatch.setattr(
        cli,
        "archive",
        lambda *args, **kwargs: pytest.fail("prune-only must never upload"),
    )
    calls = []
    monkeypatch.setattr(
        prune, "prune_archived_blocks", lambda config, **kwargs: calls.append(config)
    )
    assert (
        cli.main(
            [
                "--prune-only",
                "--state-dir",
                str(config.state_dir),
                "--prune-ceiling-file",
                "/tmp/ceiling",
                "--prune-retain-from-file",
                "7",
            ]
        )
        == 0
    )
    assert calls[0].prune_retain_from_file == 7


def test_prune_only_requires_both_consumer_limits():
    with pytest.raises(SystemExit) as raised:
        cli.main(["--prune-only"])
    assert raised.value.code == 2
