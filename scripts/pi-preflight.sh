#!/usr/bin/env sh
set -eu

echo "AssignmentTracker Pi preflight"
echo "Architecture: $(uname -m)"
echo "Docker: $(docker version --format '{{.Server.Version}}')"

case "$(uname -m)" in
  aarch64|arm64|armv7l|armv6l) ;;
  *) echo "Warning: this is not a Raspberry Pi ARM architecture." >&2 ;;
esac

docker compose config --quiet
echo "Compose configuration: OK"

echo "Building for the current Pi architecture..."
docker compose build --pull

echo "Starting AssignmentTracker..."
docker compose up -d

i=0
until [ "$i" -ge 30 ]; do
  if docker compose exec -T assignmenttracker python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/status", timeout=3)' >/dev/null 2>&1; then
    echo "Container health check: OK"
    exit 0
  fi
  i=$((i + 1))
  sleep 2
done

echo "Container did not become ready. Recent logs:" >&2
docker compose logs --tail=100 assignmenttracker >&2
exit 1
