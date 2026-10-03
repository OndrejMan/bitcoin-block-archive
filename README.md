# bitcoin-block-archive

Archives completed Bitcoin Core `blk*.dat` files and their JSON metadata to S3-compatible storage. With manual pruning enabled, it can prune files after verifying the upload.

## Docker Compose

1. Copy `.env.example` to `.env` and set the S3 credentials and destination. See `.env.example` for optional settings.
2. Start Bitcoin Core and wait until `initialblockdownload` is `false`:

   ```bash
   docker compose up -d bitcoin-core
   docker compose exec bitcoin-core bitcoin-cli -datadir=/bitcoin getblockchaininfo
   ```

3. Start the archiver:

   ```bash
   docker compose --profile archive up -d archiver
   docker compose logs -f archiver
   ```

The default Compose configuration uses `prune=1` and prunes only after checking the archived files. To upload without pruning, remove `--prune-after-archive` from `ARCHIVER_ARGS` in `.env`.

For standalone use, install with `uv sync` and run `uv run bitcoin-block-archive --help`.
