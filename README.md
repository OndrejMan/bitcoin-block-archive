# bitcoin-block-archive

Archives completed Bitcoin Core `blk*.dat` files and their JSON metadata to S3-compatible storage. With manual pruning enabled, it can prune files after verifying the upload.

## Docker Compose

1. Copy `.env.example` to `.env` and set the S3 credentials and destination. See `.env.example` for optional settings.
2. Start Bitcoin Core and the archiver together. Compose waits for Core's RPC
   health check; do not wait for initial block download to finish:

   ```bash
   docker compose --profile archive up -d
   docker compose logs -f archiver
   ```

The default Compose configuration uses `prune=1` and prunes only after checking the archived files. To upload without pruning, remove `--prune-after-archive` from `ARCHIVER_ARGS` in `.env`.

With pruning enabled, the archiver releases eligible files after every 16
verified files (about 2 GiB of normal Core block files), and after the final
partial batch. Unarchived files remain protected even if a later upload fails.
Core may retain additional files near its active tip or during synchronization.

For standalone use, install with `uv sync` and run `uv run bitcoin-block-archive --help`.
