#!/usr/bin/env bash
# Initialize a new, empty block device for Bitcoin Core data.
#
# Usage:
#   sudo ./scripts/init-bitcoin-data-volume.sh --yes
#   sudo ./scripts/init-bitcoin-data-volume.sh --device /dev/sdb --mountpoint /mnt/bitcoin-data --owner ubuntu --yes
#
# This is intentionally destructive, but refuses disks that already have
# partitions or are mounted.  It does not move the Docker volume; configure
# Compose to bind-mount the resulting mountpoint before starting Core.

set -Eeuo pipefail

device=/dev/sdb
mountpoint=/mnt/bitcoin-data
owner=ubuntu
confirmed=false

usage() {
    cat <<'EOF'
Usage: init-bitcoin-data-volume.sh [OPTIONS]

Create one ext4 partition covering a new, empty disk, mount it, and add an
UUID-based /etc/fstab entry with nofail.

Options:
  --device PATH       Block device to initialize (default: /dev/sdb)
  --mountpoint PATH   Directory where it will be mounted
                       (default: /mnt/bitcoin-data)
  --owner USER        Owner of the mountpoint (default: ubuntu)
  --yes               Confirm the destructive partitioning and formatting
  -h, --help          Show this help
EOF
}

while (($#)); do
    case "$1" in
        --device)
            device=${2:?"--device requires a path"}
            shift 2
            ;;
        --mountpoint)
            mountpoint=${2:?"--mountpoint requires a path"}
            shift 2
            ;;
        --owner)
            owner=${2:?"--owner requires a user"}
            shift 2
            ;;
        --yes)
            confirmed=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if ((EUID != 0)); then
    printf 'Run this script as root, for example: sudo %q --yes\n' "$0" >&2
    exit 1
fi

device=$(readlink -f -- "$device")
if [[ ! -b "$device" ]]; then
    printf 'Not a block device: %s\n' "$device" >&2
    exit 1
fi
if ! id "$owner" >/dev/null 2>&1; then
    printf 'User does not exist: %s\n' "$owner" >&2
    exit 1
fi
owner_uid=$(id -u "$owner")
owner_group=$(id -gn "$owner")
owner_gid=$(id -g "$owner")

# Never permit targeting the device that backs the root filesystem.
root_source=$(findmnt -n -o SOURCE /)
root_parent=$(lsblk -n -o PKNAME "$root_source" 2>/dev/null || true)
if [[ -n "$root_parent" && "$device" == "/dev/$root_parent" ]]; then
    printf 'Refusing to initialize the root disk: %s\n' "$device" >&2
    exit 1
fi

if findmnt -rn -S "$device" >/dev/null; then
    printf 'Refusing mounted device: %s\n' "$device" >&2
    exit 1
fi
if ! device_layout=$(lsblk -nr -o NAME,TYPE "$device"); then
    printf 'Cannot inspect device layout: %s\n' "$device" >&2
    exit 1
fi
if [[ -n $(awk '$2 != "disk" { print $1 }' <<<"$device_layout") ]]; then
    printf 'Refusing %s: it already has partitions or mapped child devices.\n' "$device" >&2
    exit 1
fi
if ! signatures=$(wipefs --no-act --noheadings --output TYPE "$device"); then
    printf 'Cannot inspect filesystem signatures: %s\n' "$device" >&2
    exit 1
fi
if [[ -n "$signatures" ]]; then
    printf 'Refusing %s: it already contains a recognizable filesystem signature.\n' "$device" >&2
    exit 1
fi
if mountpoint -q "$mountpoint"; then
    printf 'Refusing already-mounted directory: %s\n' "$mountpoint" >&2
    exit 1
fi
if awk -v target="$mountpoint" '$0 !~ /^[[:space:]]*#/ && $2 == target { found = 1 } END { exit !found }' /etc/fstab; then
    printf 'Refusing to add fstab entry: %s already has an entry.\n' "$mountpoint" >&2
    exit 1
fi
if [[ "$confirmed" != true ]]; then
    printf 'Refusing to modify %s without --yes.\n' "$device" >&2
    exit 2
fi

printf 'Creating an ext4 filesystem on %s and mounting it at %s.\n' "$device" "$mountpoint"
parted -s -- "$device" mklabel gpt mkpart primary ext4 0% 100%
partprobe "$device"
udevadm settle

if [[ "$device" =~ [0-9]$ ]]; then
    partition="${device}p1"
else
    partition="${device}1"
fi
if [[ ! -b "$partition" ]]; then
    printf 'Expected partition was not created: %s\n' "$partition" >&2
    exit 1
fi

mkfs.ext4 -F -L bitcoin-data "$partition"
mkdir -p -- "$mountpoint"
mount "$partition" "$mountpoint"
chown "$owner:$owner_group" "$mountpoint"

uuid=$(blkid -s UUID -o value "$partition")
fstab_line="UUID=$uuid $mountpoint ext4 defaults,nofail 0 2"
printf '%s\n' "$fstab_line" >> /etc/fstab

printf '\nReady.  Mounted %s at %s\n' "$partition" "$mountpoint"
printf 'fstab: %s\n' "$fstab_line"
printf 'For the Compose bind mount, set BITCOIN_DATA_UID=%s and BITCOIN_DATA_GID=%s.\n' \
    "$owner_uid" "$owner_gid"
