#!/bin/bash

# Read the version from the VERSION file
VERSION=$(cat VERSION)

# Define the package name with the version
PACKAGE_NAME="wata_app_v$VERSION.zip"

# Check if the package already exists
if [ -f "$PACKAGE_NAME" ]; then
    # Prompt the user for confirmation to rebuild the package
    echo "Package $PACKAGE_NAME already exists."
    read -p "Do you want to rebuild it? (y/n): " REBUILD_CONFIRMATION

    # If the user does not confirm, exit the script
    if [[ "$REBUILD_CONFIRMATION" != "y" ]]; then
        echo "Aborting the build process."
        exit 0
    else
        # Remove the old package if the user agrees to rebuild
        rm "$PACKAGE_NAME"
        echo "Old package $PACKAGE_NAME has been removed."
    fi
fi
# Build the new package with the versioned name
zip -r $PACKAGE_NAME ./src ./etc/rabbitmq/01-conf-custom.conf ./etc/config_example.json ./requirements.txt ./Dockerfile ./VERSION ./docker_build.sh ./deploy/docker-compose.yml ./deploy/install_package.sh ./deploy/update_rabbit_password.sh ./deploy/docker-compose.override.yml

echo "Package $PACKAGE_NAME created with version $VERSION"