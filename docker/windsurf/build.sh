#!/bin/bash
# Build the Windsurf headless Docker image
# Usage: ./docker/windsurf/build.sh [image_name]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="${1:-windsurf}"

echo "Building Windsurf Docker image: $IMAGE_NAME"
echo "Context: $SCRIPT_DIR"
docker build "$SCRIPT_DIR" -t "$IMAGE_NAME"
echo "Done! Image '$IMAGE_NAME' is ready."
