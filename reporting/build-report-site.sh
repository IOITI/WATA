#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Define the dashboard directory
DASHBOARD_DIR="trading-dashboard/hello-framework"

cd $SCRIPT_DIR/$DASHBOARD_DIR

# Start the build process
npm run build