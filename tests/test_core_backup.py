from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

from bitcoin_block_archive import core_backup as backup
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.s3 import S5cmdClient
from bitcoin_block_archive.state import file_signature, write_marker


@pytest.fixture
def settings(tmp_path: Path) -> backup.Settings:
    work = tmp_path / "staging"
    work.mkdir()
    return backup.Settings(work, "s3://test/core", 1024**2, 1024, 2016)


@pytest.fixture
def node(config: Config) -> Config:
    root = config.bitcoin_datadir
    for directory in (root / "blocks/index", root / "chainstate"):
        directory.mkdir(parents=True)
        (directory / "CURRENT").write_bytes(b"MANIFEST-000001\n")
        (directory / "MANIFEST-000001").write_bytes(b"fake-db\n")
    (root / ".lock").touch()
    (root / "blocks/blk00042.dat").write_bytes(b"retained block")
    (root / "blocks/rev00042.dat").write_bytes(b"undo data")
    (root / "blocks/xor.dat").write_bytes(bytes(8))
    return replace(config, block_dir=root / "blocks")


def chain_info() -> dict[str, object]:
    return {
        "chain": "main",
        "pruned": True,
        "automatic_pruning": False,
        "blocks": 970000,
        "headers": 970000,
        "pruneheight": 969500,
        "initialblockdownload": False,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"chain": "regtest"},
        {"pruned": False},
        {"pruned": 1},
        {"automatic_pruning": True},
        {"automatic_pruning": None},
        {"initialblockdownload": True},
        {"headers": 970001},
        {"pruneheight": 0},
        {"pruneheight": 100},
        {"pruneheight": 970001},
        {"blocks": True},
    ],
)
def test_pruning_flag_alone_does_not_qualify(
    settings: backup.Settings, change: dict[str, object]
) -> None:
    with pytest.raises(ArchiveError):
        backup.require_pruned({**chain_info(), **change}, settings)


def test_pruned_synced_node_qualifies(settings: backup.Settings) -> None:
    backup.require_pruned(chain_info(), settings)


@pytest.mark.parametrize("block_limit,total_limit", [(10, 1024**2), (1024, 10)])
def test_size_caps_refuse_copy_before_any_tar_is_written(
    node: Config, settings: backup.Settings, block_limit: int, total_limit: int
) -> None:
    settings = replace(settings, max_blocks=block_limit, max_size=total_limit)
    with pytest.raises(ArchiveError, match="size limits"):
        backup.inventory(node.bitcoin_datadir, settings)
    assert not (settings.work / "core.tar").exists()


def test_inventory_rejects_symlink(node: Config, settings: backup.Settings) -> None:
    (node.bitcoin_datadir / "chainstate/secret").symlink_to("/etc/passwd")
    with pytest.raises(ArchiveError, match="links"):
        backup.inventory(node.bitcoin_datadir, settings)


def test_live_core_lock_prevents_snapshot(node: Config) -> None:
    script = (
        "import fcntl,sys; "
        "f=open(sys.argv[1], 'r+b'); fcntl.lockf(f,fcntl.LOCK_EX); "
        "print('locked',flush=True); sys.stdin.read()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(node.bitcoin_datadir / ".lock")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        with (
            pytest.raises(ArchiveError, match="still running"),
            backup.stopped_core(node.bitcoin_datadir),
        ):
            pytest.fail("Live database must never be copied")
    finally:
        process.communicate(timeout=2)


class MemoryClient(S5cmdClient):
    def __init__(self, objects: dict[str, bytes], config: Config) -> None:
        super().__init__(config)
        self.objects = objects
        self.uploads: list[str] = []

    def upload(self, source: Path, destination: str) -> None:
        self.objects[destination] = source.read_bytes()
        self.uploads.append(destination)


class MemoryStorage(backup.Storage):
    client: MemoryClient

    def __init__(self, config: Config) -> None:
        self.objects: dict[str, bytes] = {}
        self.client = MemoryClient(self.objects, config)
        self.bad_head = False

    def command(self, *arguments: str) -> str:
        if arguments[0] == "cat":
            return self.objects[arguments[1]].decode()
        if arguments[:2] == ("--json", "head"):
            url = arguments[2]
            return json.dumps(
                {"key": url, "size": len(self.objects[url]) + int(self.bad_head)}
            )
        assert arguments[0] == "cp"
        Path(arguments[2]).write_bytes(self.objects[arguments[1]])
        return ""


def make_snapshot(node: Config, settings: backup.Settings) -> None:
    backup.write_json(
        settings.work / "preflight.json",
        {
            "checked_at": time.time(),
            "observed_chain_info": chain_info(),
            "core_version": 290100,
        },
    )
    backup.create(node, settings)


def test_snapshot_roundtrip_preserves_core_files(
    node: Config, settings: backup.Settings, tmp_path: Path
) -> None:
    make_snapshot(node, settings)
    storage = MemoryStorage(node)
    backup.upload(node, settings, storage)
    assert storage.client.uploads[-1] == f"{settings.destination}/latest.json"
    destination = tmp_path / "restored"
    destination.mkdir()
    backup.restore(replace(node, bitcoin_datadir=destination), settings, storage)
    expected = {
        str(p.relative_to(node.bitcoin_datadir)): p.read_bytes()
        for p in backup.inventory(node.bitcoin_datadir, settings)
    }
    actual = {
        str(p.relative_to(destination)): p.read_bytes()
        for p in destination.rglob("*")
        if p.is_file() and p.name != ".lock"
    }
    assert actual == expected


def test_failed_remote_verification_keeps_latest(
    node: Config, settings: backup.Settings
) -> None:
    make_snapshot(node, settings)
    storage = MemoryStorage(node)
    latest = f"{settings.destination}/latest.json"
    storage.objects[latest] = b"previous generation"
    storage.bad_head = True
    with pytest.raises(ArchiveError, match="remote size"):
        backup.upload(node, settings, storage)
    assert storage.objects[latest] == b"previous generation"


def test_restore_checksum_failure_leaves_destination_empty(
    node: Config, settings: backup.Settings, tmp_path: Path
) -> None:
    make_snapshot(node, settings)
    storage = MemoryStorage(node)
    backup.upload(node, settings, storage)
    url = next(key for key in storage.objects if key.endswith("core.tar"))
    original = storage.objects[url]
    storage.objects[url] = b"x" + original[1:]
    destination = tmp_path / "restore"
    destination.mkdir()
    with pytest.raises(ArchiveError, match="checksum"):
        backup.restore(replace(node, bitcoin_datadir=destination), settings, storage)
    assert [path.name for path in destination.iterdir()] == [".lock"]


def test_restore_preserves_existing_data(
    node: Config, settings: backup.Settings
) -> None:
    with pytest.raises(ArchiveError, match="empty"):
        backup.restore(node, settings, MemoryStorage(node))
    assert (
        node.bitcoin_datadir / "blocks/blk00042.dat"
    ).read_bytes() == b"retained block"


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("chainstate/link", tarfile.SYMTYPE),
        ("wallets/wallet.dat", tarfile.REGTYPE),
    ],
)
def test_restore_rejects_unsafe_tar_entries(
    settings: backup.Settings, tmp_path: Path, name: str, kind: bytes
) -> None:
    archive = settings.work / "bad.tar"
    with tarfile.open(archive, "w") as tar:
        entry = tarfile.TarInfo(name)
        entry.type = kind
        tar.addfile(entry, io.BytesIO())
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ArchiveError, match="Unsafe"):
        backup.unpack(archive, target, settings)


def test_prunable_archived_file_prevents_backup(
    node: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORE_BACKUP_ARCHIVER_ARGS", "--prune-after-archive")
    monkeypatch.setattr(backup, "safe_prune_height", lambda _: 969900)
    old = node.block_dir / "blk00041.dat"
    old.write_bytes(b"old block")
    node.state_dir.mkdir()
    write_marker(
        node,
        old,
        "a" * 64,
        old.stat().st_size,
        height_ranges=((969000, 969100),),
        source_signature=file_signature(old),
    )
    with pytest.raises(ArchiveError, match="await pruning"):
        backup.require_pruning_complete(node, chain_info())


def test_missing_consumer_ceiling_prevents_backup(
    node: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "LastProcessedBlockHeight.txt"
    monkeypatch.setenv(
        "CORE_BACKUP_ARCHIVER_ARGS",
        f"--prune-after-archive --prune-ceiling-file {missing}",
    )
    monkeypatch.setattr(backup, "safe_prune_height", lambda _: 969900)
    with pytest.raises(ArchiveError, match="ceiling"):
        backup.require_pruning_complete(node, chain_info())


@pytest.mark.parametrize("indexed", [False, True])
def test_preflight_checks_runtime_and_records_version(
    node: Config,
    settings: backup.Settings,
    monkeypatch: pytest.MonkeyPatch,
    indexed: bool,
) -> None:
    responses = {
        "getblockchaininfo": chain_info(),
        "getindexinfo": {"txindex": {"synced": True}} if indexed else {},
        "getnetworkinfo": {"version": 290100},
    }
    monkeypatch.setattr(backup, "rpc", lambda _config, method: responses[method])
    monkeypatch.setattr(backup, "require_pruning_complete", lambda _config, _info: None)
    if indexed:
        with pytest.raises(ArchiveError, match="optional Core indexes"):
            backup.check(node, settings)
    else:
        receipt = backup.check(node, settings)
        assert receipt["core_version"] == 290100
        assert receipt["observed_chain_info"] == chain_info()


def test_core_snapshot_modes_require_pruned_configuration(
    node: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORE_BACKUP_PRUNE", "0")
    monkeypatch.setenv("CORE_BACKUP_TXINDEX", "1")
    assert backup.main(node, "check") == 3
    assert backup.main(node, "restore") == 1


def test_snapshot_services_are_not_enabled_by_the_archive_profile() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is unavailable")
    version = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        timeout=3,
    )
    if version.returncode:
        pytest.skip("Docker Compose CLI is unavailable")
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(repo / "compose.yaml"),
            "--profile",
            "archive",
            "--profile",
            "backup",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    )
    services = json.loads(result.stdout)["services"]
    for name in ("core-backup", "core-restore"):
        service = services[name]
        assert service["profiles"] == ["backup"]
        assert service["restart"] == "no"
        assert service["environment"]["BITCOIN_DATADIR"] == "/bitcoin"
        volume = next(v for v in service["volumes"] if v["target"] == "/bitcoin")
        assert bool(volume.get("read_only")) is (name == "core-backup")


@pytest.mark.parametrize(
    "failure", ["check", "prepare", "create", "unclean", "upload", "none"]
)
def test_host_lifecycle_uses_only_clean_offline_copy_and_resumes_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    repo = Path(__file__).resolve().parents[1]
    script = tmp_path / "core-backup.sh"
    shutil.copyfile(repo / "core-backup.sh", script)
    docker = tmp_path / "docker"
    log = tmp_path / "calls.jsonl"
    docker.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, sys
args = sys.argv[1:]
with open(os.environ["BACKUP_TEST_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
failure = os.environ["BACKUP_TEST_FAILURE"]
if "--core-backup" in args:
    action = args[-1]
    if action == failure:
        sys.exit(3 if action == "check" else 1)
elif "--services" in args:
    print("bitcoin-core\narchiver\ndumplings")
elif "ps" in args:
    print("test-core-id")
elif "inspect" in args:
    print("137 false false" if failure == "unclean" else "0 false false")
"""
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("BACKUP_TEST_LOG", str(log))
    monkeypatch.setenv("BACKUP_TEST_FAILURE", failure)
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=4
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    if failure == "check":
        assert result.returncode == 0
        assert not any("stop" in call for call in calls)
        return
    assert any("start" in call and "dumplings" in call for call in calls)
    if failure != "prepare":
        core_start = next(
            call for call in calls if "start" in call and call[-1] == "bitcoin-core"
        )
        assert "--wait" in core_start
    if failure == "unclean":
        assert not any("create" in call for call in calls)
    if failure in {"none", "upload"}:
        upload = next(i for i, call in enumerate(calls) if "upload" in call)
        assert any(
            "start" in call and call[-1] == "bitcoin-core" for call in calls[:upload]
        )
    assert (result.returncode == 0) is (failure == "none")
