# bitcoin-block-archive

Archives completed Bitcoin Core `blk*.dat` files and their JSON metadata to S3-compatible storage. With manual pruning enabled, it can prune files after verifying the upload.

## Docker Compose

1. Copy `.env.example` to `.env` and set the S3 credentials and destination. See `.env.example` for optional settings.
2. Pull the published images and start Bitcoin Core and the archiver together.
   Compose waits for Core's RPC
   health check; do not wait for initial block download to finish:

   ```bash
   docker compose --profile archive pull
   docker compose --profile archive up -d
   docker compose logs -f archiver
   ```

GitHub Actions validates and publishes the archiver image
`ghcr.io/ondrejman/bitcoin-block-archive` for Linux amd64/arm64 after successful
checks on `main`, `v*` tags or a manual run. Tags include `latest`, `sha-*` and
versions for version tags.

Dumplings is built and published independently in the
[Dumplings repository](https://github.com/OndrejMan/Dumplings). Its image,
`ghcr.io/ondrejman/dumplings:latest`, already contains the scheduler, S3 tools
and publisher. This repository only supplies its Compose configuration; it
does not build another Dumplings image or resolve a base-image digest.
`/app/COMMIT` records the Dumplings source commit in the published image.
Override `ARCHIVER_IMAGE` and `DUMPLINGS_IMAGE` in `.env` to select other images.
Both GHCR packages must be public or Docker must be logged in with read access.

After a Dumplings update is published, update the service on a VM with Core
already running:

```bash
docker compose --profile dumplings pull dumplings
docker compose --profile dumplings up -d --no-deps dumplings
```

No archive CI run or local build is required. If an existing `.env` sets
`DUMPLINGS_IMAGE=ghcr.io/ondrejman/bitcoin-block-archive-dumplings:latest`,
change it to `DUMPLINGS_IMAGE=ghcr.io/ondrejman/dumplings:latest`.

For a local archiver build, opt into the build override:

```bash
docker compose -f compose.yaml -f compose.build.yaml --profile archive build archiver
docker compose -f compose.yaml -f compose.build.yaml --profile archive up -d
```

For a local Dumplings build, build `dumplings:local` in the Dumplings repository
as described in its README, then set `DUMPLINGS_IMAGE=dumplings:local` in `.env`.
The archive's build override only builds the archiver.

The default Compose configuration keeps every block with a txindex, and the archiver only uploads. To prune after checking the archived files, set `BITCOIN_PRUNE=1`, `BITCOIN_TXINDEX=0` and add `--prune-after-archive` to `ARCHIVER_ARGS` in `.env`; Core refuses a txindex with any pruning.

With pruning enabled, the archiver releases eligible files after every 16
verified files (about 2 GiB of normal Core block files), and after the final
partial batch. Unarchived files remain protected even if a later upload fails.
Core may retain additional files near its active tip or during synchronization.

With the Dumplings service (`--profile dumplings`) on a pruned node, add
`--prune-ceiling-file /dumplings/Scanner/LastProcessedBlockHeight.txt` to
`ARCHIVER_ARGS`. Pruning then waits for Dumplings to scan each block, and stops
while that file is missing. Dumplings stores the Whirlpool tx0s it will need
itself, so it needs no txindex; start it before pruning begins, because it can
only scan blocks that are still on disk. Run that first scan with the default
txindex: every tx0 that its candidate rule misses lands in
`Scanner/Tx0CandidateMisses.txt`, which each S3 snapshot carries. Switch to
pruning only while that file is empty. Deploying the service on a running node
is described in [dumplings/README.md](dumplings/README.md).

For standalone use, install with `uv sync` and run `uv run bitcoin-block-archive --help`.
