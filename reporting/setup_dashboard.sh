#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check for required dependencies
if ! command_exists npm; then
    echo "Error: npm is not installed. Please install Node.js and npm first."
    exit 1
fi

if ! command_exists git; then
    echo "Error: git is not installed. Please install git first."
    exit 1
fi

# Define the dashboard directory
DASHBOARD_DIR="trading-dashboard"

# Check if directory already exists
if [ -d "$DASHBOARD_DIR" ]; then
    echo "Error: Directory '$DASHBOARD_DIR' already exists."
    echo "Please remove it first or choose a different location."
    exit 1
fi

# Create the dashboard directory
echo "Creating dashboard directory: $DASHBOARD_DIR"
mkdir "$DASHBOARD_DIR"

# Navigate to the dashboard directory
cd "$DASHBOARD_DIR"

# Create a new Observable Framework project
echo "Creating new Observable Framework project..."
npx @observablehq/framework@latest create

# Copy your custom index.md
echo "Copying custom index.md..."
cp "$SCRIPT_DIR/original/index.md" "$SCRIPT_DIR/$DASHBOARD_DIR/hello-framework/src/index.md"
rm "$SCRIPT_DIR/$DASHBOARD_DIR/hello-framework/src/example-dashboard.md"
rm "$SCRIPT_DIR/$DASHBOARD_DIR/hello-framework/src/example-report.md"


# Make the sync script executable
chmod +x "$SCRIPT_DIR/sync_reporting_data.sh"

echo "Setup complete! Your dashboard is ready in: $DASHBOARD_DIR/hello-framework"
echo "To start the development server, run:"
echo "$SCRIPT_DIR/start_report_server.sh"
