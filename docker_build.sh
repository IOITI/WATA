#!/bin/bash
# The images of a release, built beside the ones that run: nothing is stopped for it. wata-base:<version> is
# what WATA's containers run, wata-backup:<version> the backups'. The release is the folder this script is in
# (install_package.sh runs the new release's, CI the checkout's). An image is built only when the server has
# none of that version with this content (its label wata.content): installing a release again builds nothing,
# and a container recreated on a tag finds the image its release was checked with.
set -euo pipefail

cd "$(dirname "$0")"
VERSION="$(cat VERSION)"

# What an image is made of, as a digest: the files of its build, compiled ones left out.
content() {
    find "$@" -type f ! -path '*/__pycache__/*' ! -name '.DS_Store' -print0 | LC_ALL=C sort -z \
        | xargs -0 sha256sum | sha256sum | cut -c1-16
}

# Build "$image" with the command given unless it is there with this content already.
build() {
    local image="$1" made="$2"
    shift 2
    if [ "$(docker image inspect --format '{{ index .Config.Labels "wata.content" }}' "$image" 2>/dev/null || true)" = "$made" ]; then
        echo "$image is built already, with this content"
        return
    fi
    "$@"
}

base_image() {
    docker build -t "wata-base:$VERSION" --label "wata.content=$BASE_CONTENT" --platform=linux/amd64 .
}

# The backups' image from its two files alone: nothing else of the release's folder, its .env link
# included, reaches the build (COPYFILE_DISABLE: a Mac's tar would add its ._ files; Linux's has none).
backup_image() {
    COPYFILE_DISABLE=1 tar -c -C deploy pg_backup.Dockerfile pg_backup.sh \
        | docker build -t "wata-backup:$VERSION" --label "wata.content=$BACKUP_CONTENT" --platform=linux/amd64 \
            -f pg_backup.Dockerfile -
}

BASE_CONTENT="$(content Dockerfile requirements.txt VERSION src)"
build "wata-base:$VERSION" "$BASE_CONTENT" base_image
BACKUP_CONTENT="$(content deploy/pg_backup.Dockerfile deploy/pg_backup.sh)"
build "wata-backup:$VERSION" "$BACKUP_CONTENT" backup_image
