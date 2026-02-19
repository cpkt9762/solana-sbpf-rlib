#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DIR="solana"
mkdir -p "$DIR"

if [ -z "${1:-}" ]; then
	echo "Usage: bash install-solana.sh <version>"
	exit 1
fi

version="$1"

_ostype="$(uname -s)"
_cputype="$(uname -m)"

case "$_ostype" in
Linux)
	_ostype="unknown-linux-gnu"
	;;
Darwin)
	if [[ "$_cputype" = "arm64" ]]; then
		_cputype="aarch64"
	fi
	_ostype="apple-darwin"
	;;
*)
	echo "Unsupported OS: $_ostype" >&2
	exit 1
	;;
esac

TARGET="${_cputype}-${_ostype}"
download_url="https://github.com/solana-labs/solana/releases/download/v${version}/solana-release-${TARGET}.tar.bz2"
archive="solana-release-${TARGET}.tar.bz2"

echo "Downloading ${archive} from ${download_url}"
wget "$download_url" -O "$archive"
tar -xvf "$archive"
rm -f "$archive"
mv solana-release "$DIR/solana-release-${version}"
