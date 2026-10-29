# Read the version from the VERSION file
VERSION=$(cat ../../VERSION)

# Check if the inventory file exists
INVENTORY_FILE="ansible/inventory/inventory.ini"
if [ ! -f "$INVENTORY_FILE" ]; then
  echo "Error: Inventory file $INVENTORY_FILE does not exist, please create it, in example from 'deploy/tools/ansible/inventory/inventory_example.ini'"
  exit 1
fi

echo "Send WATA version $VERSION to server"

# Installed beside what runs (nothing stops), then each container the release changes recreated when its
# guards allow; the rest waits for `watadeploy apply`. -e apply=none installs and says what would go,
# -e services=admin1,service_control1 applies those alone, -e force=true whatever holds them back,
# -e full=true stops and starts everything (for a change of Traefik, PostgreSQL or RabbitMQ).
ansible-playbook ansible/playbooks/deploy_app.yml -i "$INVENTORY_FILE" "$@"

echo "WATA version $VERSION is installed: watadeploy status on the server, or the admin's System page, says what runs it"
