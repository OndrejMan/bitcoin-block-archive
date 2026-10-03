from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from bitcoin_block_archive import bitcoin, cli
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError

ENVIRONMENT_DEFAULTS = (
    "S3_ENDPOINT_URL",
    "S3_PROFILE",
    "S3_DESTINATION",
    "BITCOIN_DATADIR",
    "BITCOIN_RPC_HOST",
    "BITCOIN_RPC_PORT",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT_DEFAULTS:
        monkeypatch.delenv(name, raising=False)


def parse(argv: list[str]) -> Config:
    args = cli.Arguments()
    cli.build_parser().parse_args(argv, namespace=args)
    return cli.config_from_args(args)


def test_defaults_map_into_config() -> None:
    args = cli.Arguments()
    cli.build_parser().parse_args([], namespace=args)
    config = cli.config_from_args(args)

    assert config.block_dir == Path("/var/lib/bitcoin/blocks")
    assert config.keep_latest_files == 2
    assert config.stop_bitcoin_on_error is True


def test_no_stop_on_error_flag() -> None:
    args = cli.Arguments()
    cli.build_parser().parse_args(["--no-stop-on-error"], namespace=args)

    assert cli.config_from_args(args).stop_bitcoin_on_error is False


def test_environment_supplies_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.example.invalid")
    monkeypatch.setenv("S3_PROFILE", "archive")
    monkeypatch.setenv("S3_DESTINATION", "s3://bucket/blocks")
    monkeypatch.setenv("BITCOIN_DATADIR", "/bitcoin")
    monkeypatch.setenv("BITCOIN_RPC_HOST", "bitcoin-core")
    monkeypatch.setenv("BITCOIN_RPC_PORT", "8332")

    config = parse([])

    assert config.s3_endpoint == "https://s3.example.invalid"
    assert config.s3_profile == "archive"
    assert config.s3_destination == "s3://bucket/blocks"
    assert config.bitcoin_datadir == Path("/bitcoin")
    assert config.bitcoin_rpc_host == "bitcoin-core"
    assert config.bitcoin_rpc_port == 8332


def test_flags_override_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.example.invalid")
    monkeypatch.setenv("BITCOIN_RPC_PORT", "8332")

    config = parse(["--endpoint", "https://other.invalid", "--rpc-port", "18443"])

    assert config.s3_endpoint == "https://other.invalid"
    assert config.bitcoin_rpc_port == 18443


def test_empty_environment_keeps_builtin_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Compose passes `${VAR:-}` through as an empty string.
    for name in ENVIRONMENT_DEFAULTS:
        monkeypatch.setenv(name, "")

    config = parse([])

    assert config.s3_endpoint == "https://s3.cl4.du.cesnet.cz"
    assert config.s3_destination == "s3://xman-coinjoin/bitcoin-mainnet/blocks"
    assert config.bitcoin_datadir == Path("/var/lib/bitcoin")
    assert config.bitcoin_rpc_host is None
    assert config.bitcoin_rpc_port is None


def test_invalid_rpc_port_from_environment_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BITCOIN_RPC_PORT", "0")

    with pytest.raises(SystemExit):
        parse([])


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

    monkeypatch.setattr(cli, "manual_pruning_enabled", lambda _: False)
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
    args = cli.Arguments()
    cli.build_parser().parse_args(["--min-free-space", "20G"], namespace=args)

    assert cli.config_from_args(args).min_free_space == 20 * 1024**3


def test_manual_pruning_keeps_the_node_running_after_a_failure(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pruning = replace(config, prune_after_archive=True)
    monkeypatch.setattr(cli, "manual_pruning_enabled", lambda _: True)

    assert not cli.should_stop_bitcoin(pruning, failed=True)


def test_automatic_pruning_still_stops_the_node_after_a_failure(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "manual_pruning_enabled", lambda _: False)
    assert cli.should_stop_bitcoin(config, failed=True)


def test_archive_flag_is_not_proof_that_core_uses_manual_pruning(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "manual_pruning_enabled", lambda _: False)
    assert cli.should_stop_bitcoin(
        replace(config, prune_after_archive=True), failed=True
    )


def test_upload_only_failure_on_manual_node_does_not_stop_it(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "manual_pruning_enabled", lambda _: True)
    assert not cli.should_stop_bitcoin(config, failed=True)


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


@pytest.mark.parametrize(
    "arguments",
    [
        ["--keep-latest-files", "-1"],
        ["--min-free-space", "nonsense"],
        ["--rpc-timeout", "0"],
        ["--upload-timeout", "-1"],
        ["--verify-timeout", "invalid"],
    ],
)
def test_invalid_arguments_have_no_node_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    def unexpected(_: Config) -> None:
        pytest.fail("Invalid arguments must not invoke the archive or node")

    monkeypatch.setattr(cli, "archive", unexpected)
    monkeypatch.setattr(cli, "manual_pruning_enabled", unexpected)
    monkeypatch.setattr(cli, "stop_bitcoin", unexpected)
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_timeouts_map_into_config() -> None:
    args = cli.Arguments()
    cli.build_parser().parse_args(
        ["--rpc-timeout", "5", "--upload-timeout", "600", "--verify-timeout", "10"],
        namespace=args,
    )
    config = cli.config_from_args(args)
    assert (config.rpc_timeout, config.upload_timeout, config.verify_timeout) == (
        5,
        600,
        10,
    )


def test_node_control_failure_returns_unsuccessful_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_stop(_: Config) -> None:
        raise ArchiveError("RPC unavailable")

    monkeypatch.setattr(cli, "archive", lambda _: None)
    monkeypatch.setattr(cli, "should_stop_bitcoin", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "stop_bitcoin", failed_stop)
    assert cli.main([]) == 1


def test_failed_low_disk_stop_does_not_report_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def failed_rpc(
        command: list[str], *, check: bool = True, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        assert command[-1] == "stop"
        return subprocess.CompletedProcess(command, 1, "", "RPC unavailable")

    monkeypatch.setattr(cli, "archive", lambda _: None)
    monkeypatch.setattr(cli, "free_bytes", lambda _: 0)
    monkeypatch.setattr(bitcoin, "run", failed_rpc)
    assert cli.main(["--block-dir", str(tmp_path), "--min-free-space", "1"]) == 1
