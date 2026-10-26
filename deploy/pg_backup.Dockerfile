# The image of pg_backup1 (deploy/pg_backup.sh): PostgreSQL's own tools, the version of the database's
# image, and age and rclone, which send each dump off the server encrypted. Built by docker_build.sh as
# wata-backup, from nothing but this file. Each download is checked against its sha256, as the release
# lists it (rclone's SHA256SUMS, the digests GitHub gives age's assets), read on 2026-10-26.
FROM postgres:16-alpine

ARG AGE_VERSION=v1.3.2
ARG AGE_SHA256=cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10
ARG RCLONE_VERSION=v1.75.2
ARG RCLONE_SHA256=349ac8fba6ff65d6247043f1750cdcb518ec5d500ef91463a10d37c0ccdf3702

# ca-certificates: rclone checks the bucket's certificate. age-keygen comes with age for CI's throwaway
# key: the operator's own key is made on the Mac (lab/backup_tools.sh), and never sent here.
RUN set -eu; \
    apk add --no-cache ca-certificates; \
    cd /tmp; \
    wget -q -O age.tar.gz "https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-linux-amd64.tar.gz"; \
    echo "${AGE_SHA256}  age.tar.gz" | sha256sum -c -; \
    tar -xzf age.tar.gz; \
    install -m 755 age/age age/age-keygen /usr/local/bin/; \
    wget -q -O rclone.zip "https://downloads.rclone.org/${RCLONE_VERSION}/rclone-${RCLONE_VERSION}-linux-amd64.zip"; \
    echo "${RCLONE_SHA256}  rclone.zip" | sha256sum -c -; \
    unzip -q rclone.zip "rclone-${RCLONE_VERSION}-linux-amd64/rclone"; \
    install -m 755 "rclone-${RCLONE_VERSION}-linux-amd64/rclone" /usr/local/bin/rclone; \
    rm -rf /tmp/*; \
    age --version; \
    rclone version
