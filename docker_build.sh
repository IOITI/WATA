# Define base directory and version file path
APP_BASE_DIR="/app/wata"
VERSION_FILE="$APP_BASE_DIR/VERSION"

# Check if VERSION file exists
if [ ! -f "$VERSION_FILE" ]; then
    echo "Error: Version file not found at $VERSION_FILE" >&2
    exit 1
fi

# Get the version from the VERSION file
VERSION=$(cat "$VERSION_FILE")
VERSION_DIR="$APP_BASE_DIR/$VERSION"

cd $VERSION_DIR
docker build -t wata-base . --platform=linux/amd64