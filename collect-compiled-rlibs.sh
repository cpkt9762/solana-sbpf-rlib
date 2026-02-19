#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FACTORY_DIR="$SCRIPT_DIR"
OUT_DIR="${1:-/tmp/r2ghidra-solana-rlibs-by-version}"
CACHE_SOLANA_DIR="${HOME}/.cache/solana"

mkdir -p "$OUT_DIR"
rm -rf "$OUT_DIR/toolchain-core" "$OUT_DIR/crate-builds" "$OUT_DIR/factory-rlibs"
mkdir -p "$OUT_DIR/toolchain-core" "$OUT_DIR/crate-builds" "$OUT_DIR/factory-rlibs"

echo "[*] Collecting rust core/std rlibs from $CACHE_SOLANA_DIR"
if [ -d "$CACHE_SOLANA_DIR" ]; then
	while IFS= read -r libdir; do
		case "$libdir" in
		*/rustlib/sbf-solana-solana/lib|*/rustlib/sbpf*-solana-solana/lib)
			solana_ver="$(echo "$libdir" | sed -E 's#^.*/\.cache/solana/(v[^/]+)/.*#\1#')"
			target="$(echo "$libdir" | sed -E 's#^.*/rustlib/([^/]+)/lib$#\1#')"
			dst="$OUT_DIR/toolchain-core/$solana_ver/$target"
			mkdir -p "$dst"
			find "$libdir" -maxdepth 1 -type f -name 'lib*.rlib' -exec cp -f {} "$dst/" \;
			;;
		esac
	done < <(find "$CACHE_SOLANA_DIR" -type d -path '*/platform-tools/rust/lib/rustlib/*/lib' | sort -V)
else
	echo "[!] Skip: $CACHE_SOLANA_DIR not found"
fi

echo "[*] Collecting compiled release/deps rlibs from factory crates/"
while IFS= read -r crate_dir; do
	base="$(basename "$crate_dir")"
	for target in sbf-solana-solana sbpfv3-solana-solana; do
		release_dir="$crate_dir/target/$target/release"
		[ -d "$release_dir" ] || continue
		dst_release="$OUT_DIR/crate-builds/$base/$target/release"
		dst_deps="$OUT_DIR/crate-builds/$base/$target/release-deps"
		mkdir -p "$dst_release" "$dst_deps"
		find "$release_dir" -maxdepth 1 -type f -name 'lib*.rlib' -exec cp -f {} "$dst_release/" \;
		if [ -d "$release_dir/deps" ]; then
			find "$release_dir/deps" -maxdepth 1 -type f -name '*.rlib' -exec cp -f {} "$dst_deps/" \;
		fi
	done
done < <(find "$FACTORY_DIR/crates" -maxdepth 1 -mindepth 1 -type d | sort)

echo "[*] Collecting exported factory rlibs from rlibs/"
if [ -d "$FACTORY_DIR/rlibs" ]; then
	find "$FACTORY_DIR/rlibs" -type f -name '*.rlib' | while IFS= read -r r; do
		rel="${r#$FACTORY_DIR/rlibs/}"
		dst="$OUT_DIR/factory-rlibs/$rel"
		mkdir -p "$(dirname "$dst")"
		cp -f "$r" "$dst"
	done
fi

find "$OUT_DIR" -type f -name '*.rlib' | sort > "$OUT_DIR/RLIB_PATHS.txt"

echo "[*] Done: $(wc -l < "$OUT_DIR/RLIB_PATHS.txt" | tr -d ' ') rlibs"
echo "[*] Output root: $OUT_DIR"
echo "[*] Path list: $OUT_DIR/RLIB_PATHS.txt"
