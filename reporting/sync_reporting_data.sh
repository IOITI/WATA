#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Define the inventory file path
INVENTORY_FILE="$SCRIPT_DIR/../deploy/tools/ansible/inventory/inventory.ini"

# Define the paths that were previously prompted
reporting_framework_path="$SCRIPT_DIR/trading-dashboard/hello-framework"
temp_local_duckdb_path="$SCRIPT_DIR/trading-dashboard/temp_local_duckdb"

# Check if hello-framework directory exists
if [ ! -d "$reporting_framework_path" ]; then
    echo "Error: hello-framework directory not found at $reporting_framework_path"
    echo "Please run the setup-dashboard.sh script first to set up the reporting framework."
    exit 1
fi

# Check if temp_local_duckdb directory exists, create it if not
if [ ! -d "$temp_local_duckdb_path" ]; then
    echo "Creating temp_local_duckdb directory at $temp_local_duckdb_path"
    mkdir -p "$temp_local_duckdb_path"
fi

# Check if inventory file exists
if [ ! -f "$INVENTORY_FILE" ]; then
    echo "Error: Inventory file not found at $INVENTORY_FILE"
    echo "Please make sure you have copied inventory_example.ini to inventory.ini and configured it with your server details."
    exit 1
fi

# Run the Ansible playbook with the inventory file and pass the variables
ansible-playbook -i "$INVENTORY_FILE" \
    -e "temp_local_duckdb_path=$temp_local_duckdb_path" \
    -e "reporting_framework_path=$reporting_framework_path" \
    "$SCRIPT_DIR/../deploy/tools/ansible/playbooks/sync_reporting_data.yml"

cd $temp_local_duckdb_path

duckdb $temp_local_duckdb_path/trading_data.duckdb "EXPORT DATABASE 'duckdb_export' (FORMAT PARQUET);"
duckdb $temp_local_duckdb_path/trading_data.duckdb "COPY (WITH grouped_data AS ( SELECT action, instrument_name, SUM(position_profit_loss) AS total_profit_loss FROM turbo_data_position GROUP BY action, instrument_name ) SELECT action AS name, json_group_array(json_object( 'name', instrument_name, 'value', total_profit_loss )) AS children FROM grouped_data GROUP BY action) TO 'duckdb_export/treemap_data.json' (FORMAT JSON, ARRAY true);"

cp -R duckdb_export/treemap_data.json $reporting_framework_path/src/
cp -R duckdb_export/turbo_data_order.parquet $reporting_framework_path/src/
cp -R duckdb_export/turbo_data_position.parquet $reporting_framework_path/src/
cp -R duckdb_export/trade_performance.parquet $reporting_framework_path/src/