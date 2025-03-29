# Read the version from the VERSION file
VERSION=$(cat ../../VERSION)

echo "Send WATA version $VERSION to server"

ansible-playbook deploy_app.yml -i inventory.ini

echo "WATA version $VERSION can be started on the server"
