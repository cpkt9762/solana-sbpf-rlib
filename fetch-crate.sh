#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DIR="crates"
mkdir -p "$DIR"

if [ -z "${1:-}" ] || [ -z "${2:-}" ]; then
	echo "Usage: bash fetch-crate.sh <package> <version>"
	exit 1
fi

crate="$1"
version="$2"
tmp_dir="${crate}-${version}"

curl -Ls "https://crates.io/api/v1/crates/${crate}/${version}/download" | tar -zxf -
mv "$tmp_dir" "$DIR/$tmp_dir"
