from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bitcoin_s3_archive import cli
from bitcoin_s3_archive.config import Config


def test_defaults_map_into_config() -> None:
    args = cli.build_parser().parse_args([])
    config = cli.config_from_args(args)

    assert config.block_dir == Path("/var/lib/bitcoin/blocks")
    assert config.keep_latest_files == 2
    assert config.stop_bitcoin_on_error is True


def test_no_stop_on_error_flag() -> None:
    args = cli.build_parser().parse_args(["--no-stop-on-error"])

    assert cli.config_from_args(args).stop_bitcoin_on_error is False


def test_main_stops_bitcoin_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stopped: list[str] = []

    def failing_archive(config: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "archive", failing_archive)
    monkeypatch.setattr(
        cli,
        "stop_bitcoin",
        lambda config: stopped.append("stopped"),
    )

    exit_code = cli.main(["--state-dir", str(tmp_path)])

    assert exit_code == 1
    assert stopped == ["stopped"]


def test_main_leaves_bitcoin_running_when_opted_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_archive(config: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "archive", failing_archive)
    monkeypatch.setattr(
        cli,
        "stop_bitcoin",
        lambda config: pytest.fail("should not stop Bitcoin Core"),
    )

    assert cli.main(["--no-stop-on-error"]) == 1


def test_min_free_space_accepts_human_sizes() -> None:
    args = cli.build_parser().parse_args(["--min-free-space", "20G"])

    assert cli.config_from_args(args).min_free_space == 20 * 1024**3


def test_manual_pruning_keeps_the_node_running_after_a_failure(
    config: Config,
) -> None:
    pruning = replace(config, prune_after_archive=True)

    assert not cli.should_stop_bitcoin(pruning, failed=True)


def test_automatic_pruning_still_stops_the_node_after_a_failure(
    config: Config,
) -> None:
    assert cli.should_stop_bitcoin(config, failed=True)


def test_low_disk_stops_the_node_even_on_success(config: Config) -> None:
    guarded = replace(
        config,
        prune_after_archive=True,
        min_free_space=1024**5,
    )

    assert cli.should_stop_bitcoin(guarded, failed=False)


def test_ample_disk_does_not_stop_the_node(config: Config) -> None:
    guarded = replace(config, min_free_space=1)

    assert not cli.should_stop_bitcoin(guarded, failed=False)


def test_no_stop_on_error_overrides_everything(config: Config) -> None:
    never = replace(
        config,
        stop_bitcoin_on_error=False,
        min_free_space=1024**5,
    )

    assert not cli.should_stop_bitcoin(never, failed=True)
