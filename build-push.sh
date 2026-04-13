#!/usr/bin/env bash
# Build & push multi-arch images (linux/amd64 + linux/arm64) to Docker Hub.
# Usage: ./build-push.sh [hub|agent|all]
set -e

BUILDER="ikabot-multi"
PLATFORMS="linux/amd64,linux/arm64"
HUB_IMAGE="blackoneal/ikabot-web-hub:latest"
AGENT_IMAGE="blackoneal/ikabot-web-agent:latest"

TARGET="${1:-all}"

if [[ "$TARGET" == "hub" || "$TARGET" == "all" ]]; then
  echo "==> Building hub ($PLATFORMS)..."
  docker buildx build --builder "$BUILDER" --platform "$PLATFORMS" --push -t "$HUB_IMAGE" ./hub_v2
fi

if [[ "$TARGET" == "agent" || "$TARGET" == "all" ]]; then
  echo "==> Building agent ($PLATFORMS)..."
  docker buildx build --builder "$BUILDER" --platform "$PLATFORMS" --push -t "$AGENT_IMAGE" ./agent_v2
fi

echo "Done."
