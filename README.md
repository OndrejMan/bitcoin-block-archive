# bitcoin-block-archive

Archives completed Bitcoin Core `blk*.dat` files and their JSON metadata to S3-compatible storage. With manual pruning enabled, it can prune files after verifying the upload.

## Use in mainnet-sync

The published image `ghcr.io/ondrejman/bitcoin-block-archive` contains Bitcoin
Core and this archiver. coinjoin-pipeline's manually started `mainnet-sync` runs
it on MetaCentrum PBS nodes through Apptainer: `bitcoind` for the node, one
archive pass after Dumplings has scanned the frozen height, and a `--prune-only`
pass with `--prune-ceiling-file` and `--prune-retain-from-file` after BlockSci
has indexed it. Every pass is a single run; there is no scheduler or long-lived
service. The block files land incrementally under the S3 prefix given by
`--destination` (or `S3_DESTINATION`), one object and its sidecar per completed file.
The destination has no default: an archiver started without one stops with an error
instead of uploading into someone else's bucket.

GitHub Actions validates and publishes the image for Linux amd64/arm64 after
successful checks on `main`, `v*` tags or a manual run. Tags include `latest`,
`sha-*` and versions for version tags.

## Pruning

To prune after checking the archived files, pass `--prune-after-archive` to an
archive pass or run a separate `--prune-only` pass; Core needs `prune=1`
(manual pruning) and refuses a txindex with any pruning. Unarchived files remain
protected even if a later upload fails. Core may retain additional files near
its active tip or during synchronization.

`--prune-ceiling-file` names a file holding a height, such as Dumplings'
`Scanner/LastProcessedBlockHeight.txt`. Pruning never goes above it and stops
while the file is missing, so every block is scanned before it is deleted.

`--min-free-space` (default 20G) stops Bitcoin Core when free space in the block
directory falls below it, checked every 30 seconds even during uploads.

For standalone use, install with `uv sync` and run `uv run bitcoin-block-archive --help`.
