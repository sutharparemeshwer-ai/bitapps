#!/usr/bin/env bash
# Golden Navratna Kuber — Start Script
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "============================================================"
echo " Starting Golden Navratna Kuber 15-Minute Draw Server..."
echo "============================================================"

# Kill any existing server on port 8000
fuser -k 8000/tcp 2>/dev/null || true

python3 server.py
