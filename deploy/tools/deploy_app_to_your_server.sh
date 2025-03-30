# Read the version from the VERSION file
VERSION=$(cat ../../VERSION)

# Check if the inventory file exists
INVENTORY_FILE="ansible/inventory/inventory.ini"
if [ ! -f "$INVENTORY_FILE" ]; then
  echo "Error: Inventory file $INVENTORY_FILE does not exist, please create it, in example from 'deploy/tools/ansible/inventory/inventory_example.ini'"
  exit 1
fi

echo "Send WATA version $VERSION to server"

ansible-playbook ansible/playbooks/deploy_app.yml -i "$INVENTORY_FILE"

echo "WATA version $VERSION can be started on the server"