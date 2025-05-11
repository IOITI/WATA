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
mkdir -p _tmp_package/wata/$VERSION
cp -r ./src _tmp_package/wata/$VERSION/
cp -r ./etc _tmp_package/wata/
cp ./requirements.txt _tmp_package/wata/$VERSION/
cp ./Dockerfile _tmp_package/wata/$VERSION/
cp ./VERSION _tmp_package/wata/
cp ./VERSION _tmp_package/wata/$VERSION/
cp ./docker_build.sh _tmp_package/wata/$VERSION/
mkdir -p _tmp_package/wata/$VERSION/deploy
cp -r ./deploy/docker-compose.yml _tmp_package/wata/$VERSION/deploy/
cp -r ./deploy/docker-compose.override.yml _tmp_package/wata/$VERSION/deploy/
cp -r ./deploy/install_package.sh _tmp_package/wata/$VERSION/deploy/
cp -r ./deploy/update_rabbit_password.sh _tmp_package/wata/$VERSION/deploy/

# Go to the temporary directory and create the zip
cd _tmp_package
zip -r ../$PACKAGE_NAME ./*
cd ..

# Clean up
rm -rf _tmp_package

echo "Package $PACKAGE_NAME created with version $VERSION"