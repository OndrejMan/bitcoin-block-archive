# Dumplings service

Runs [Dumplings](https://github.com/OndrejMan/Dumplings) next to the archived
Bitcoin Core node and publishes its `Scanner/` folder to S3. Each pass waits
until Core (and its txindex, if enabled) is synced, publishes the hashes of the
last 2016 blocks, runs `dumplings sync` (which resumes from the last
checkpoint), and uploads a new snapshot when the height moved. The service is
built in the Dumplings repository; see its
[entrypoint](https://github.com/OndrejMan/Dumplings/blob/master/docker-entrypoint.sh).

S3 layout under `DUMPLINGS_S3_DESTINATION`:

- `latest.json`: `last_processed_height`, `prefix` of the current snapshot,
  `previous` snapshot, `dumplings_commit`, `uploaded_at`.
- `<height>/`: a `Scanner/` snapshot. The current one and the one before it are
  kept; older ones are deleted.
- `block-hashes.json`: canonical hashes of the last 2016 heights from Core.

## Deploying on the archive VM

These steps add Dumplings to a VM that already runs `bitcoin-core` and the
archiver from this repository, on a node that has **not been pruned**.
Dumplings starts at height 336 861 and can only scan blocks that are still on
disk.

1. Make sure the commit you deploy is pushed, then update the checkout and keep
   a copy of `.env`. Stop if `git status` shows local changes:

   ```bash
   cd ~/bitcoin-block-archive && git status --short && cp .env .env.bak-$(date +%F) && git pull --ff-only
   ```

2. Check the node. `getblockchaininfo` must show `"initialblockdownload": false`
   and `blocks` equal to `headers`. If it shows `"pruned": true`, check that
   `pruneheight` is below 336 861. A txindex is optional, but the first scan
   should run with one (see the main README):

   ```bash
   docker compose exec bitcoin-core bitcoin-cli -datadir=/bitcoin getblockchaininfo
   ```

   ```bash
   docker compose exec bitcoin-core bitcoin-cli -datadir=/bitcoin getindexinfo
   ```

   Check free memory and disk. `Scanner/` grows to several GB in the
   `dumplings-data` volume, which lives on the Docker disk:

   ```bash
   free -h && df -h / /var/lib/docker
   ```

3. Check `.env`. `ARCHIVER_ARGS` must not contain `--prune-after-archive`
   while the first scan runs, or the archiver may delete blocks Dumplings has
   not scanned yet:

   ```bash
   grep -E '^(BITCOIN_PRUNE|BITCOIN_TXINDEX|ARCHIVER_ARGS|DUMPLINGS_)' .env
   ```

   Set the following in `.env`, adding missing keys rather than duplicating them:

   ```dotenv
   DUMPLINGS_IMAGE=ghcr.io/ondrejman/dumplings:latest
   DUMPLINGS_S3_DESTINATION=s3://xman-coinjoin-new/dumplings
   DUMPLINGS_INTERVAL=86400
   DUMPLINGS_DATA_SOURCE=dumplings-data
   DUMPLINGS_MEMORY_LIMIT=2g
   ```

   For a host credentials file, also set
   `S3_CREDENTIALS_SOURCE=/home/ubuntu/.aws/credentials` and `S3_PROFILE=coinjoin`.
   Compose mounts it read-only into both services. A mode-600 file must be
   owned by the container's UID (`BITCOIN_DATA_UID`, typically 1000 on this VM).
   Environment key pairs are also supported when no file is selected; see
   the main README.

   The image already contains the CLI, scheduler and S3 publisher. If an
   existing `.env` points to `bitcoin-block-archive-dumplings`, replace that
   value with the image above. A `sha-*` tag can also select an older release.

4. Pull the published image and check its embedded Dumplings source commit:

   ```bash
   docker compose --profile dumplings pull dumplings
   docker compose --profile dumplings run --rm --no-deps --entrypoint cat dumplings /app/COMMIT
   ```

   Publish the service with CI in `OndrejMan/Dumplings`. Its GHCR package
   must be public, or the VM's Docker must be logged in with read access.
   No build or CI run in the archive repository is needed for a Dumplings update.

5. Start only Dumplings. `--no-deps` leaves the running Core and archiver
   alone. Compose applies `DUMPLINGS_MEMORY_LIMIT` (2 GiB by default) with no
   swap, including after every container recreation:

   ```bash
   docker compose --profile dumplings up -d --no-deps dumplings
   ```

6. Follow the log. The first pass prints `published hashes of heights …` and
   `About N blocks will be processed`:

   ```bash
   docker compose --profile dumplings logs -f dumplings
   ```

The first snapshot reaches S3 only after the whole first scan, which takes many
hours to days on HDD-backed storage. Until then, check the progress with:

```bash
docker compose --profile dumplings exec dumplings cat /data/Scanner/LastProcessedBlockHeight.txt
```

```bash
docker stats --no-stream
```

If the log shows `Killed` followed by `sync failed`, inspect the container's
OOM status. If it hit the memory cap, raise `DUMPLINGS_MEMORY_LIMIT` in `.env`,
for example to `3g`, and repeat step 5.

### Cautions

- Do not run `docker compose --profile archive up -d` as part of this
  deployment. If `compose.yaml` changed the `bitcoin-core` command, Compose
  recreates and restarts Core with the new flags.
- Dumplings shares Core's network namespace. Whenever `bitcoin-core` is
  recreated, recreate Dumplings too:

  ```bash
  docker compose --profile dumplings up -d --no-deps --force-recreate dumplings
  ```

- Switch the node to pruning only after the first scan, while
  `Scanner/Tx0CandidateMisses.txt` stays empty, and add `--prune-ceiling-file`
  as described in the main README.

### Upgrading Dumplings

Publish the updated service from the Dumplings repository, then repeat
steps 4 and 5. With `DUMPLINGS_IMAGE=ghcr.io/ondrejman/dumplings:latest`, pulling
fetches the newly published image. There is no second build in the archive.
The scan resumes from `Scanner/Checkpoint.txt` in the `dumplings-data` volume.
Compose applies the memory cap to the new container.

### Rollback

```bash
docker compose --profile dumplings stop dumplings
```

Core and the archiver keep running. `Scanner/` stays in its volume, and the
next start resumes from it.

## Local tests

All run on a development machine with Docker; none needs the VM or real S3.
Each builds Dumplings from a local checkout. `test_service.sh` uses that
checkout's Dockerfile and entrypoint to build the complete service image.

- `test_service.sh <dumplings-checkout>`: regtest Core with a txindex and
  MinIO; three passes check publishing, `latest.json`, snapshot rotation and
  `block-hashes.json`.
- `smoke_scan.sh <dumplings-checkout> [blocks]`: kills a scan and checks that
  the resumed one reaches the tip with a consistent checkpoint.
- `bench_resume.sh <dumplings-checkout> <scanner-dir> [label]`: peak memory of
  a resume over an existing `Scanner/`, for example one written by
  `gen_scanner.py`.
