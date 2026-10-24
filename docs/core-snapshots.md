# Small, restorable snapshots of a pruned Bitcoin Core node

The block archive stores blockchain history, while Dumplings publishes its
scanner outputs separately. Neither contains Bitcoin Core's ready-to-use
UTXO database and block index. Reconstructing those from raw blocks requires
a full reindex. This feature adds a separate S3 snapshot of the pruned Core
state so another VM can restore it and catch up from that state.

The backup is explicitly invoked on the host. It does not install a scheduler,
enable pruning, or change the existing block archive format. Its containers
are isolated behind the `backup` Compose profile.

## Eligibility, backup and restore

After deploying the updated archiver image and Compose file, run
`bash core-backup.sh` in the deployment directory. A daily host cron job can
invoke the same script. The deploy script downloads it alongside Compose.
It skips the backup before stopping any service unless:

- `.env` sets `BITCOIN_PRUNE=1`, `BITCOIN_TXINDEX=0`, and Core reports manual
  pruning, completed synchronization and a positive `pruneheight` within
  `CORE_BACKUP_MAX_LAG` (default 2016 blocks) of its tip;
- optional Core indexes are disabled and there is a single chainstate;
- no verified archived file remains eligible for the archiver's pruning
  (respecting its pending files, Dumplings' ceiling and Core's retained tail);
- retained block/undo data fits `CORE_BACKUP_MAX_BLOCK_SIZE` (default `2G`)
  and all snapshot files fit `CORE_BACKUP_MAX_SIZE` (default `25G`, GiB).

The script never enables or performs pruning. Let the regular archiver prune
after verified archival and Dumplings processing first. `prune=1` alone is
insufficient: a node still holding old history or exceeding either size limit
does not qualify. Eligibility and sizes are checked again before copying.

The script stops the running archiver and Dumplings, checks eligibility again,
then gracefully stops Core. A forced/OOM shutdown is rejected. It copies
`chainstate/` and all retained `blocks/` files (index, blocks, undo and XOR key)
while holding Core's filesystem lock. Core and the previously running consumers
restart before upload, also on copy failures. Wallets, credentials, optional
indexes and node configuration are outside this snapshot.

Snapshots go to `CORE_BACKUP_DESTINATION`, by default
`s3://xman-coinjoin-new/bitcoin-mainnet/core-snapshots`. Each generation contains
`core.tar` and `manifest.json` with its size, SHA-256 and Core version.
`latest.json` switches only after the tar's remote size and manifest are
verified; older generations remain available. Restore verifies the downloaded
tar's checksum. The observed tip in the manifest is from before shutdown, not
an exact claim about the offline snapshot's final height.

The `core-backup-staging` volume needs space for a tar up to the size limit
(and room for the previous local tar while making a replacement). No Docker
socket is mounted into containers. Only the host script controls Compose.
Keep Core stopped during copying; do not run concurrent deployment commands.

To restore on a new VM, prepare the deployment with
`bash /tmp/bitcoin-block-archive-deploy.sh ~/bitcoin-block-archive --prepare-only`
**without starting the services**, configure its S3 access and set `BITCOIN_PRUNE=1`,
`BITCOIN_TXINDEX=0`. Use the same Core image version as the snapshot. With an
empty destination datadir and all services stopped, run:

```bash
bash core-backup.sh restore
docker compose up -d --wait bitcoin-core
```

Restore refuses existing data, validates tar paths and size limits, and restores
the databases and retained blocks together. Core then catches up without a full
reindex. Dumplings' `Scanner/` must be restored separately from its own S3
snapshot (including `Tx0Candidates.txt`) before starting Dumplings and the
pruning archiver. `--prepare-only` leaves services untouched; use it instead of
the normal deploy command when preparing a restore.

## Validation and remaining runtime checks

Focused local tests cover pruning and size guards, refusal of live database
copies, backup/restore file preservation, checksum and tar path validation,
failed upload publication, service recovery on errors, Compose profile
isolation, and deployment without starting services. Static checks include
Ruff, mypy, shell syntax and rendering the Compose configuration without
contacting a Docker daemon.

No mainnet backup, image build, real Core restart/restore or live S3 operation
has been run as part of this implementation. A published archiver image
containing this change is required before using the host script. The first
real backup and a restore into a separate empty datadir must still be verified
with that image before treating the workflow as operationally validated.
