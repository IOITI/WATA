#!/usr/bin/env bash
# age and rclone for python -m lab backup check --offsite, into .venv-lab/bin: the versions the
# server's backup image takes (deploy/pg_backup.Dockerfile), each download checked against its sha256
# as the release lists it (rclone's SHA256SUMS, the digests GitHub gives age's assets). Run once,
# after lab/setup.sh; then make the key whose public line goes in the server's .env
# (BACKUP_AGE_RECIPIENT), and the remote that reads the bucket:
#
#   lab/backup_tools.sh
#   mkdir -p ~/.config/wata && .venv-lab/bin/age-keygen -o ~/.config/wata/backup-age.key
#   .venv-lab/bin/rclone config
#
# Keep a copy of the key's file where this Mac's own backups go: without it, no copy opens.
set -euo pipefail
cd "$(dirname "$0")/.."
AGE_VERSION=v1.3.2
RCLONE_VERSION=v1.75.2
[ "$(uname -s)" = Darwin ] || { echo "For the Mac: the server's image has its own (deploy/pg_backup.Dockerfile)." >&2; exit 1; }
case "$(uname -m)" in
  arm64)
    AGE_ARCH=darwin-arm64; AGE_SHA256=e2020b073c44f692685a24d6abc378817eb81ffaaf49fd0531ef8565f767f2f5
    RCLONE_ARCH=osx-arm64; RCLONE_SHA256=610ab7d48615135dfc60b763858b11a426a0d3d1160c1bb76fb0ae6011b02edd ;;
  x86_64)
    AGE_ARCH=darwin-amd64; AGE_SHA256=1d1e4bc66e1427edad7739ae7616157de0e79db8b6d2a1497d7d9925fb06a539
    RCLONE_ARCH=osx-amd64; RCLONE_SHA256=574e1f31cf1c464e5fb0325cb66fcbdeaf1efa1802bd9b17f13c256fd74b6561 ;;
  *) echo "Unknown processor: $(uname -m)" >&2; exit 1 ;;
esac
[ -d .venv-lab/bin ] || { echo "Run lab/setup.sh first." >&2; exit 1; }
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

curl -fsSL -o "$work/age.tar.gz" "https://github.com/FiloSottile/age/releases/download/$AGE_VERSION/age-$AGE_VERSION-$AGE_ARCH.tar.gz"
echo "$AGE_SHA256  $work/age.tar.gz" | shasum -a 256 -c -
tar -xzf "$work/age.tar.gz" -C "$work"
install -m 755 "$work/age/age" "$work/age/age-keygen" .venv-lab/bin/

curl -fsSL -o "$work/rclone.zip" "https://downloads.rclone.org/$RCLONE_VERSION/rclone-$RCLONE_VERSION-$RCLONE_ARCH.zip"
echo "$RCLONE_SHA256  $work/rclone.zip" | shasum -a 256 -c -
unzip -q "$work/rclone.zip" "rclone-$RCLONE_VERSION-$RCLONE_ARCH/rclone" -d "$work"
install -m 755 "$work/rclone-$RCLONE_VERSION-$RCLONE_ARCH/rclone" .venv-lab/bin/

echo "Ready: .venv-lab/bin/age, age-keygen and rclone ($AGE_VERSION, $RCLONE_VERSION)"
