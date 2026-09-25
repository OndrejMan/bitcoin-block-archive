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
deleted only after uploads succeed and the remote objects' sizes and checksum
sidecars have been checked again. The destination must be protected against
independent deletion or replacement; see the safety limits below.

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

By default Compose stores Core's datadir in Docker's named `bitcoin-data`
volume, as before. To store it on an initialized host disk instead, set this
in `.env` before the first `docker compose up`:

```dotenv
BITCOIN_DATA_SOURCE=/mnt/bitcoin-data
# Replace 1000 with the output of `id -u ubuntu` / `id -g ubuntu`.
BITCOIN_DATA_UID=1000
BITCOIN_DATA_GID=1000
```

The same source is mounted read-only into `archiver`. The path must already
exist and be mounted; use
[`scripts/init-bitcoin-data-volume.sh`](scripts/init-bitcoin-data-volume.sh)
for a new, empty `/dev/sdb` volume. Do not change `BITCOIN_DATA_SOURCE` for a
node that already has data unless you deliberately intend to start from the
data at the new location. The UID and GID ensure that Core's container user
owns the bind-mounted data as the specified host user, so that user can inspect
the files without `sudo`.

Compose sets `-blocksxor=0` before the first node start. The archiver and
BlockSci need unobfuscated block files. A pre-existing nonzero `blocks/xor.dat`
is rejected before uploading anything. Adding `-blocksxor=0` to an existing
obfuscated datadir does **not** convert it; Core rejects that combination.
Preserve that datadir and initialize a separate one with XOR disabled.

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
`S3_PROFILE` defaults to `coinjoin`.

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--keep-latest-files N` | Hold back the N newest files; the newest may still be written by Bitcoin Core (default 2). |
| `--prune-after-archive` | Prune the node up to the last safely archived height (needs `prune=1`). |
| `--min-free-space 20G` | Stop Bitcoin Core if the block directory runs this low. |
| `--no-stop-on-error` | Never stop Bitcoin Core, whatever happens. |
| `--endpoint` / `--profile` / `--credentials` | s5cmd connection settings. |
| `--rpc-timeout SECONDS` | Time limit for each Bitcoin RPC command (default 60). |
| `--upload-timeout SECONDS` | Time limit for each S3 upload, including block files (default 3600). |
| `--verify-timeout SECONDS` | Time limit for each S3 HEAD or checksum-sidecar read (default 60). |
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
upload blk + .sha256 → marker → manifest → remote checks → pruneblockchain
```

The safe height is `min(height of the first block in each unarchived file) - 1`.
Bitcoin Core deletes a blk/rev pair only once the *highest* block it holds is
at or below the requested height, and a file's first block is a lower bound on
its highest one — so this is safe even though blocks are written in arrival
order rather than by height, as happens during IBD or after a reorg. It costs
one `getblockheader` call per unarchived file (two by default).

This is a **file-deletion limit**, not proof that every lower block height
has been archived. The manifest's `archived_max_height` is computed separately:
it checks all headers in unarchived files, including out-of-order blocks, and
caps the result by the node's validated tip. Linked consecutive headers share
one height lookup; out-of-order runs need additional RPC calls. Unknown
coverage is `null`, and an empty archive is not contiguous from genesis.
Files created, removed or modified during this check cause the pass to fail
and be retried.

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
- The per-file deletion rule is present in Bitcoin Core 29.1's
  [`FindFilesToPruneManual`](https://github.com/bitcoin/bitcoin/blob/v29.1/src/node/blockstorage.cpp).
  Runtime pruning still needs validation against the deployed node.

## Safety limits and existing state

`--prune-after-archive` requires the connected node to report manual pruning
over RPC (`pruned=true`, `automatic_pruning=false`). The flag alone is not
evidence of Core's configuration. Error handling also checks the actual node
mode; when that cannot be verified, it conservatively requests a stop unless
`--no-stop-on-error` was supplied.

Markers are bound to the S3 endpoint and object destination, and checked
against local file size and modification/change timestamps. A file changing
during hashing, metadata lookup or upload does not get a completed marker.
Keep the newest files excluded; `--keep-latest-files 0` is intended for a
quiescent source, not a node that is actively appending blocks.

Each external command has a time limit; exceeding it fails the pass and uses
the same error policy as other archival failures. These are per-command
limits, not a deadline for the whole pass. Adjust the upload limit for slow
connections. Invalid CLI limits are rejected before archival or node control.
An unsuccessful safety check or node stop request makes the command exit
with a nonzero status, including after a successful upload.

Markers require a matching endpoint and complete, correctly typed local
timestamps. Markers with malformed block references are rejected at load time.

Before pruning, the archiver performs a remote HEAD and reads the checksum
sidecar for each archived file still on disk. This detects missing objects,
size mismatches and missing/changed sidecars; it does not download and rehash
every remote block. Protect the archive from lifecycle expiry and independent
writers; a deletion after verification cannot be prevented by a local lock.
The node's RPC and `--block-dir` must refer to the same block store.

`--min-free-space` is checked at the end of a pass, not continuously. Schedule
passes and disk monitoring accordingly. Compose remains upload-only by default;
manual pruning requires explicitly enabling `--prune-after-archive`.

## Layout

| Module | Responsibility |
| --- | --- |
| `config.py` | `Config` dataclass, defaults, remote URL construction |
| `cli.py` | Argument parsing, wiring, top-level error handling |
| `archive.py` | Block selection and the per-file archival sequence |
| `s3.py` | `Uploader` protocol and the `s5cmd` implementation |
| `prune.py` | Safe prune height and the `pruneblockchain` handshake |
| `coverage.py` | Conservative archived height for manifest consumers |
| `blockfile.py` | Minimal `blk*.dat` header reader |
| `models.py` | Immutable archive records and typed JSON schemas |
| `jsonutil.py` | Validation of external JSON at input boundaries |
| `manifest.py` | Portable archive inventory and publication |
| `disk.py` | Free-space watchdog and human-readable sizes |
| `state.py` | Atomic per-block JSON markers |
| `hashing.py` | Chunked SHA256 and `sha256sum`-compatible lines |
| `locking.py` | Single-instance `flock` guard |
| `bitcoin.py` | Node RPCs via `bitcoin-cli`: heights, pruning, stop |
| `process.py` | Subprocess wrapper shared by all external commands |

## Development

```bash
uv sync --all-groups --all-extras --locked
uv run python -m pytest
uv run ruff check .
uv run mypy
```

Strict mypy checks both source and tests. Production modules also reject
`Any` expressions, explicit `Any`, and types degraded by missing imports.
