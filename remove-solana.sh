#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DIR="solana"

if [ -z "${1:-}" ]; then
	echo "Usage: bash remove-solana.sh <version>"
	exit 1
fi

version="$1"

# Keep historical toolchains by default. Uncomment to free disk.
# rm -rf "$DIR/solana-release-$version"
