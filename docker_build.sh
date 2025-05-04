cd /app/wata/$(cat VERSION)
docker build -t wata-base . --platform=linux/amd64