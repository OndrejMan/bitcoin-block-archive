# bitcoin-block-archive

Archives completed Bitcoin Core `blk*.dat` files and their JSON metadata to S3-compatible storage. With manual pruning enabled, it can prune files after verifying the upload.

## Deploy on a VM without a Git clone

Install Docker with Compose v2 and `curl`, then download and run the deployment
script:

```bash
curl -fsSL https://raw.githubusercontent.com/OndrejMan/bitcoin-block-archive/main/deploy.sh -o /tmp/bitcoin-block-archive-deploy.sh &&
bash /tmp/bitcoin-block-archive-deploy.sh
```

The default deployment directory is `~/bitcoin-block-archive`. On the first run
the script creates a private `.env` there and stops. Edit that file to set your
S3 credentials and destinations, and the data disk path/owner, then repeat the
same command. Deployment accepts a readable credentials file or a complete
pair of S3/AWS keys. Missing credentials stop deployment before pulling or
starting services; `S3_PROFILE` alone does not supply credentials.
Existing `.env` files are never overwritten. To use another
directory, pass it as the script's first argument.

After both repositories have successfully published their images, the script
downloads `compose.yaml`, validates it and pulls the images before replacing
the current Compose file. It starts Core, waits for its RPC health check and
recreates the archiver and Dumplings containers. Core may restart if its
configuration changed. Re-run the same download command for later updates;
there is no local image build or Git checkout to update.

The fixed Compose project name is `bitcoin-block-archive`, including in another
deployment directory, so the existing named volumes remain in use. This is
one deployment per Docker daemon. Dumplings has a persistent 2 GiB memory cap
with no swap; change `DUMPLINGS_MEMORY_LIMIT` in `.env` if needed.

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

For an existing credentials file on the VM, set these values in `.env`:

```dotenv
S3_CREDENTIALS_SOURCE=/home/ubuntu/.aws/credentials
S3_PROFILE=coinjoin
```

Compose mounts that file read-only at `/run/secrets/s3-credentials` in both the
archiver and Dumplings containers. Both select the requested profile without
copying or rewriting the file. The file takes precedence over environment keys.
Keep it private (mode `600`); its owner must match the container UID, such as
`BITCOIN_DATA_UID=1000` for a file owned by `ubuntu` with UID 1000. The deploy
script checks host readability, and each entrypoint checks readability again
as its container user. This does not verify that the selected profile exists
or that its keys have S3 access.

Alternatively leave `S3_CREDENTIALS_SOURCE` empty and supply both
`S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` (or both `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY`). File mode needs newly published images from both
repositories. Changing `.env` requires recreating the two service containers.

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
