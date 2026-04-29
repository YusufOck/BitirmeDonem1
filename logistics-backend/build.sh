#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/../logistics-frontend" && pwd)"

echo ">>> Building SBTU Logistics frontend..."
cd "$FRONTEND_DIR"
if [ -f package-lock.json ]; then
  npm ci || npm install
else
  npm install
fi
npm run build
cd "$BACKEND_DIR"

echo ">>> Copying dist/ to backend..."
rm -rf dist
cp -r "$FRONTEND_DIR/dist" "$BACKEND_DIR/dist"

echo ">>> Building Docker image..."
docker compose build

echo ">>> Done. Run: docker compose up -d"
