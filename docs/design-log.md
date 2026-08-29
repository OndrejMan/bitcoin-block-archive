# Design log

Working notes for `bitcoin-core-s3-archive`. Written 2026-08-28; update in
place rather than starting a new file.

## Why this exists

A pruned Bitcoin Core node keeps the disk small but deletes `blk*.dat` files
that BlockSci still needs to parse the mainnet chain. This tool uploads those
files to S3 (CESNET) before they can be pruned, so the node stays small and the
full block history stays parseable.

## What was built

Started from a single 402-line `script.py`. Split into a `src/` layout package
following the conventions of the rest of the workspace (setuptools + uv,
pytest/ruff/mypy, console script). `script.py` remains as a shim so the old
invocation still works. See the README module table for the split.

Behaviour changes made during the split:

- `ArchiveError` instead of bare `RuntimeError`, with exception chaining.
- `S5cmdClient` hidden behind an `Uploader` protocol, so tests need no
  subprocess patching.
- `find_archivable_blocks` with `--keep-latest-files 0` now returns every
  file; the original `[:-0]` returned an empty list.
- Marker temp file is `blkNNNNN.dat.json.tmp`; `.with_suffix()` used to
  replace `.json`.

## The pruning race, and the handshake that replaces it

Under automatic pruning (`prune=N`) Core deletes on its own schedule while the
archiver uploads on another. Nothing synchronises them, and stopping the node
after a failure is noticed is reactive — by then the file may be gone.

`prune=1` switches Core to manual pruning: nothing is deleted unless
`pruneblockchain <height>` is called. `--prune-after-archive` makes the
archiver the only thing that calls it.

Safe height: `min(height of the first block in each unarchived file) - 1`.
Core deletes a blk/rev pair only once the *highest* block it holds is at or
below the requested height, and a file's first block is a lower bound on its
highest one — so this holds even though blocks are written in arrival order
rather than by height (IBD, reorgs). Costs one `getblockheader` per unarchived
file, two by default.

During an S3 outage: no marker, so no prune call, so nothing is deleted and the
node keeps running. Disk pressure becomes the only real risk, which is what
`--min-free-space` guards. Stopping the node is no longer the reaction to every
failure — only to a breached space threshold, or to a failure while automatic
pruning is still in charge.

## Verified BlockSci facts

Read in the `blocksci` subrepo, `tools/parser/`:

- `parser_configuration.cpp:81` — `pathForBlockFile()` resolves to
  `coinDirectory/blocks/blkNNNNN.dat`. The parser reads **only** `blk*.dat`:
  no LevelDB `blocks/index/`, no `rev*.dat`. Archiving block files alone is
  therefore sufficient, and undo data can be rebuilt with `-reindex`.
- `chain_index.cpp:43` — `maxBlockFileNum()` walks consecutive file numbers and
  **stops at the first gap**.
- `chain_index.cpp:104` — a cold parse starts at `fileNum = 0`; an incremental
  update resumes at `newestBlock.nFile` and never re-reads older files.

Consequences:

1. A cold parse against a pruned datadir finds no `blk00000.dat`, parses zero
   blocks, and **does not fail** — silent truncation, not an error.
2. Any restore must download a *contiguous* run starting at `blk00000.dat`. One
   missing file in the middle silently cuts the index short.
3. Incremental update only needs blocks from `newestBlock.nFile` onward, which
   is the flow that is compatible with a pruned node.

## Not verified

- **Core's per-file prune predicate.** The safe-height formula assumes Core
  deletes a blk/rev pair once its highest block is at or below the requested
  height. The `- 1` leaves margin for either `<` or `<=`, but the rule itself
  was not read out of the Core source. Confirm on regtest before trusting
  `--prune-after-archive` on mainnet.
- **Nothing has run against reality.** All verification is against stubs: 51
  unit tests, plus an end-to-end run with a fake `bitcoin-cli` and `s5cmd`
  covering the normal pass, an S3 outage under manual pruning (no RPC at all,
  node left running) and under automatic pruning (`stop` issued). No real
  bitcoind, no real s5cmd, no CESNET.

## Interaction with coinjoin-pipeline

The two do not collide. The pipeline's S3 is for *run artifacts*
(`s3://coinjoin-thesis/runs`); this archiver's is for *block data*. Same
endpoint, same `s5cmd`, same `--profile coinjoin`, same `~/.aws/credentials`.

But the pipeline reads blocks from a local path, never from S3:
`external_bitcoin_datadir` is bind-mounted read-only as `/mnt/data` and passed
to `blocksci_parser ... --disk`. Combined with the BlockSci findings above,
a pruned node breaks the mainnet parse stage silently.

**Decision: the restore direction belongs in `coinjoin-pipeline`, not here.**
It has to execute on a PBS compute node inside a generated bash script, in an
environment the pipeline builds and where s5cmd, credentials and
`sha256sum -c` are already established idiom. Putting it here would mean
getting this package onto MetaCentrum and onto the compute node's PYTHONPATH —
a new moving part where s5cmd suffices. The pipeline also has a MinIO harness
(`tests/test-kubernetes-s3-minio.sh`) that can exercise a restore for real.

Division of ownership: this project owns *production* of the archive (naming,
what gets uploaded, when pruning is allowed); the pipeline owns *consumption*
(where blocks come from for the parse stage, and how they are verified).

Where it would go in the pipeline — the extension point already exists, so the
template itself needs no change:

| File | Change |
| --- | --- |
| `pipeline/client/pbs.py:721` | third source branch in `render_blocksci_parse_s3_pbs` (and the update variant): download, verify sidecars, check contiguity, set `BITCOIN_DATADIR="$RUN_WORK/bitcoin_data"` |
| `pipeline/client/wrapper.py` | `--blocksci-bitcoin-blocks-uri` flag + validation, as an alternative to `--blocksci-external-bitcoin-datadir` |
| `src/coinjoin_pipeline/configuration.py` | matching YAML field on `BlockSciConfiguration` |
| `command_metadata.json` (×2) | regenerate via `scripts/generate-command-metadata.py`; guarded by `tests/test-command-builder-contract.sh` |
| `examples/`, tests | new example YAML, render unit tests |

Keep `source_kind: "external-bitcoin"` in the cache manifest, otherwise
`blocksci_update_s3_template.sh` rejects the cache for a later incremental
update. Mainnet `blocks/` is well past 700 GB, so the `scratch: 2tb` in
`examples/metacentrum-mainnet-parse.yaml` is a requirement, not headroom.

## Open decisions

- **Archive manifest (recommended, not built).** An object listing archived
  files, their checksums and the height range. Without it the restore step has
  to infer contiguity by probing `s5cmd ls`; with it the producer states what
  it knows. It would also carry the archived max height, which today is
  hand-set as `max_block:` in the parse YAML and merely hoped to be covered.
  Cost: a format coupling between the two repos, where today they share only
  a naming convention.
- **Bucket.** The default here is `s3://xman-coinjoin/bitcoin-mainnet/blocks`,
  inherited from the original script. That name appears nowhere else in the
  workspace; every pipeline example uses `coinjoin-thesis`. Pick one.

## Next steps, in order

1. Confirm the `pruneblockchain` per-file rule on regtest (blocks safe mainnet
   use of `--prune-after-archive`).
2. Smoke run against a real bitcoind and real CESNET S3 (nothing has yet).
3. Build the restore path in `coinjoin-pipeline` (blocks parsing a pruned node
   at all).
4. Settle the bucket name.
5. systemd timer / cron unit for deployment.

Rationale for the order: verify the producer does what we think before
building a consumer on top of it.

## Repo state

Everything in `BitcoinCoreWithS3/` is untracked — nothing committed.
`coinjoin-pipeline` was read but never modified; its tree is clean.
