# bitcoin-block-archive

Archives completed Bitcoin Core `blk*.dat` files to S3-compatible storage
(CESNET S3 by default) using [`s5cmd`](https://github.com/peak/s5cmd), so a
pruned node can keep serving while the full block history stays available for
BlockSci parsing.

Each block file is uploaded together with a `.sha256` sidecar. A JSON marker
in the state directory records what has already been archived and every pass
publishes `archive-manifest.json` in the S3 destination: a deterministic,
checksummed inventory for BlockSci restore jobs. With
`--prune-after-archive` the tool also drives pruning itself, so blocks are
deleted only once they are known to be in S3.

## Install

```bash
uv sync                      # or: pip install -e .
```

## Usage

```bash
bitcoin-block-archive \
  --block-dir /var/lib/bitcoin/blocks \
  --state-dir /var/lib/bitcoin/.s3-archive \
  --destination s3://xman-coinjoin/bitcoin-mainnet/blocks \
  --profile coinjoin

python -m bitcoin_block_archive --help   # equivalent, without installing a script
./script.py --help                    # compatibility shim, runs from the source tree
```

## Docker

The image contains the Python package, `s5cmd`, and `bitcoin-cli`. For the
complete deployment use Compose: it starts a persistent mainnet Bitcoin Core
container and the `archiver` service performs one archive pass through the
private Docker network. Both share a persistent Core datadir; the archiver
mounts it read-only and uses Core's cookie authentication. The entrypoint
creates a temporary `s5cmd` credentials profile from environment variables, so
no AWS file is mounted into either container.

```bash
cp .env.example .env
# Edit .env and insert the two S3 secrets.
docker compose up -d bitcoin-core
docker compose logs -f bitcoin-core
```

The first IBD is a full mainnet synchronization and can take a long time and
substantial disk space. Once `initialblockdownload` is `false`, run one pass:

```bash
docker compose exec bitcoin-core bitcoin-cli -datadir=/bitcoin getblockchaininfo
docker compose --profile archive run --rm archiver
```

Run the latter command periodically from cron/systemd. Compose starts Core
with `prune=1` (manual pruning); the default archiver command is upload-only.
Add `--prune-after-archive` to the `archiver.command` only after confirming
the archived S3 objects and manifest.

The standalone image can still target an already-running node by mounting its
datadir and passing `--bitcoin-cli` as needed.
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are accepted as aliases for
the two `S3_*` secret variables; `S3_PROFILE` defaults to `coinjoin`.

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--keep-latest-files N` | Hold back the N newest files; the newest may still be written by Bitcoin Core (default 2). |
| `--prune-after-archive` | Prune the node up to the last safely archived height (needs `prune=1`). |
| `--min-free-space 20G` | Stop Bitcoin Core if the block directory runs this low. |
| `--no-stop-on-error` | Never stop Bitcoin Core, whatever happens. |
| `--endpoint` / `--profile` / `--credentials` | s5cmd connection settings. |
| `-v` | Debug logging, including every command that is run. |

Intended to run periodically (cron/systemd timer). Concurrent runs are safe:
the second one takes an `flock` on `<state-dir>/archive.lock`, finds it held,
and exits without doing anything.

## Pruning: race vs. handshake

Under automatic pruning (`prune=N` in bitcoin.conf) the node deletes files on
its own schedule while the archiver uploads on another, and nothing
synchronises the two. The only protection is reactive: stop the node once a
failure is noticed — by which time the file may already be gone.

Setting **`prune=1`** switches Bitcoin Core to *manual pruning mode*: automatic
pruning is off entirely, and blocks are removed only when the
`pruneblockchain <height>` RPC is called. Running the archiver with
`--prune-after-archive` then makes the two steps a handshake:

```
upload blk + .sha256  →  marker  →  compute safe height  →  pruneblockchain
```

The safe height is `min(height of the first block in each unarchived file) - 1`.
Bitcoin Core deletes a blk/rev pair only once the *highest* block it holds is
at or below the requested height, and a file's first block is a lower bound on
its highest one — so this is safe even though blocks are written in arrival
order rather than by height, as happens during IBD or after a reorg. It costs
one `getblockheader` call per unarchived file (two by default).

**During an S3 outage** the upload fails, no marker is written, and
`pruneblockchain` is never called. Nothing is deleted, and the node keeps
running: a failed pass costs disk space, not blocks. The node is then stopped
only when `--min-free-space` is actually breached, which is the setup worth
running:

```bash
bitcoin-block-archive --prune-after-archive --min-free-space 20G
```

Notes:

- Bitcoin Core always keeps the last 288 blocks, whatever height is requested.
- Switching `prune=550` → `prune=1` needs a node restart, and does not bring
  back blocks that were already pruned away.
- Only `blk*.dat` is archived. The `rev*.dat` undo files are derived data and
  can be rebuilt from the block files with `-reindex`.
- The per-file deletion rule above is load-bearing; it is worth confirming on
  regtest against the Bitcoin Core version you actually run.

## Layout

| Module | Responsibility |
| --- | --- |
| `config.py` | `Config` dataclass, defaults, remote URL construction |
| `cli.py` | Argument parsing, wiring, top-level error handling |
| `archive.py` | Block selection and the per-file archival sequence |
| `s3.py` | `Uploader` protocol and the `s5cmd` implementation |
| `prune.py` | Safe prune height and the `pruneblockchain` handshake |
| `blockfile.py` | Minimal `blk*.dat` reader (first block hash) |
| `disk.py` | Free-space watchdog and human-readable sizes |
| `state.py` | Atomic per-block JSON markers |
| `hashing.py` | Chunked SHA256 and `sha256sum`-compatible lines |
| `locking.py` | Single-instance `flock` guard |
| `bitcoin.py` | Node RPCs via `bitcoin-cli`: heights, pruning, stop |
| `process.py` | Subprocess wrapper shared by all external commands |

## Design log

`docs/design-log.md` records why the pruning handshake looks the way it does,
what has and has not been verified against a real node, and the open decisions
(archive manifest, bucket name, where the S3 restore path belongs).

## Development

```bash
uv run pytest
uv run ruff check .
uv run mypy
```
