#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

~/go/bin/onsave &
ONSAVE_PID=$!

cleanup() {
    kill "$ONSAVE_PID" 2>/dev/null
}
trap cleanup EXIT

sleep 2
echo "" >> "$REPO_ROOT/data.yaml"
sleep 3
truncate -s -1 "$REPO_ROOT/data.yaml"
sleep 2
