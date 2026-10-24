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

For a local archiver build, opt into the build override:

```bash
docker compose -f compose.yaml -f compose.build.yaml --profile archive build archiver
docker compose -f compose.yaml -f compose.build.yaml --profile archive up -d
```

The default Compose configuration keeps every block with a txindex, and the archiver only uploads. To prune after checking the archived files, set `BITCOIN_PRUNE=1`, `BITCOIN_TXINDEX=0` and add `--prune-after-archive` to `ARCHIVER_ARGS` in `.env`; Core refuses a txindex with any pruning.

With pruning enabled, the archiver releases eligible files after every 16
verified files (about 2 GiB of normal Core block files), and after the final
partial batch. Unarchived files remain protected even if a later upload fails.
Core may retain additional files near its active tip or during synchronization.

For standalone use, install with `uv sync` and run `uv run bitcoin-block-archive --help`.
