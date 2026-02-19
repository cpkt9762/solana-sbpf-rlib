#!/usr/bin/env bash

set -euo pipefail

usage() {
	cat <<'EOF'
Usage:
  build-rlibs-from-index.sh [options]

Required options:
  --solana-version <ver>                 Solana release policy version
  --compiler-solana-version <ver>        Primary compiler toolchain version
  --fallback-compiler-solana-version <v> Fallback compiler toolchain version
  --platform-tools-version <v>           cargo-build-sbf --tools-version value

Optional options:
  --scope <all|solana|anchor>            Crate scope (default: all)
  --versions-dir <path>                  Versions index directory
  --state-dir <path>                     State/log directory
  --include <regex>                      Only process crates matching regex
  --exclude <regex>                      Skip crates matching regex
  --max-crates <n>                       Process at most N crates
  --force                                Re-run crates even if marked success
  --cleanup-target                       Pass --cleanup-target to get-rlibs-from-crate.py
  --cleanup-solana                       Pass --cleanup-solana to get-rlibs-from-crate.py
  -h, --help                             Show this help

Notes:
  - Crate list comes from versions/{solana-rust-crates.txt,anchor-crates.txt}.
  - Per-crate versions file must exist at versions/<crate>.txt.
  - Missing crates in versions/missing-crates.txt are skipped automatically.
EOF
}

log() {
	printf '[*] %s\n' "$*"
}

warn() {
	printf '[!] %s\n' "$*" >&2
}

die() {
	printf '[x] %s\n' "$*" >&2
	exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FACTORY_DIR="$SCRIPT_DIR"
VERSIONS_DIR="$FACTORY_DIR/versions"
STATE_DIR="$FACTORY_DIR/run-state"

SOLANA_VERSION=""
COMPILER_SOLANA_VERSION=""
FALLBACK_COMPILER_SOLANA_VERSION=""
PLATFORM_TOOLS_VERSION=""
SCOPE="all"
INCLUDE_RE=""
EXCLUDE_RE=""
MAX_CRATES=0
FORCE=0
CLEANUP_TARGET=0
CLEANUP_SOLANA=0

while [ "$#" -gt 0 ]; do
	case "$1" in
	--solana-version)
		SOLANA_VERSION="$2"
		shift 2
		;;
	--compiler-solana-version)
		COMPILER_SOLANA_VERSION="$2"
		shift 2
		;;
	--fallback-compiler-solana-version)
		FALLBACK_COMPILER_SOLANA_VERSION="$2"
		shift 2
		;;
	--platform-tools-version)
		PLATFORM_TOOLS_VERSION="$2"
		shift 2
		;;
	--scope)
		SCOPE="$2"
		shift 2
		;;
	--versions-dir)
		VERSIONS_DIR="$2"
		shift 2
		;;
	--state-dir)
		STATE_DIR="$2"
		shift 2
		;;
	--include)
		INCLUDE_RE="$2"
		shift 2
		;;
	--exclude)
		EXCLUDE_RE="$2"
		shift 2
		;;
	--max-crates)
		MAX_CRATES="$2"
		shift 2
		;;
	--force)
		FORCE=1
		shift
		;;
	--cleanup-target)
		CLEANUP_TARGET=1
		shift
		;;
	--cleanup-solana)
		CLEANUP_SOLANA=1
		shift
		;;
	-h|--help)
		usage
		exit 0
		;;
	*)
		die "Unknown argument: $1"
		;;
	esac
done

[ -n "$SOLANA_VERSION" ] || die "--solana-version is required"
[ -n "$COMPILER_SOLANA_VERSION" ] || die "--compiler-solana-version is required"
[ -n "$FALLBACK_COMPILER_SOLANA_VERSION" ] || die "--fallback-compiler-solana-version is required"
[ -n "$PLATFORM_TOOLS_VERSION" ] || die "--platform-tools-version is required"

case "$SCOPE" in
all|solana|anchor) ;;
*) die "--scope must be all|solana|anchor" ;;
esac

[ -d "$VERSIONS_DIR" ] || die "versions dir not found: $VERSIONS_DIR"
[ -f "$VERSIONS_DIR/solana-rust-crates.txt" ] || die "missing $VERSIONS_DIR/solana-rust-crates.txt"
[ -f "$VERSIONS_DIR/anchor-crates.txt" ] || die "missing $VERSIONS_DIR/anchor-crates.txt"

mkdir -p "$STATE_DIR/logs" "$STATE_DIR/success" "$STATE_DIR/failed"
RUN_TS="$(date +%Y%m%d-%H%M%S)"
RUN_SUMMARY="$STATE_DIR/run-$RUN_TS.summary"
: > "$RUN_SUMMARY"

tmp_crates="$(mktemp "${TMPDIR:-/tmp}/build-rlibs-crates.XXXXXX")"
trap 'rm -f "$tmp_crates"' EXIT

if [ "$SCOPE" = "solana" ] || [ "$SCOPE" = "all" ]; then
	cat "$VERSIONS_DIR/solana-rust-crates.txt" >> "$tmp_crates"
fi
if [ "$SCOPE" = "anchor" ] || [ "$SCOPE" = "all" ]; then
	cat "$VERSIONS_DIR/anchor-crates.txt" >> "$tmp_crates"
fi

if [ -f "$VERSIONS_DIR/missing-crates.txt" ]; then
	grep -v -F -x -f "$VERSIONS_DIR/missing-crates.txt" "$tmp_crates" > "${tmp_crates}.filtered" || true
	mv "${tmp_crates}.filtered" "$tmp_crates"
fi

sort -u -o "$tmp_crates" "$tmp_crates"

if [ -n "$INCLUDE_RE" ]; then
	grep -E "$INCLUDE_RE" "$tmp_crates" > "${tmp_crates}.filtered" || true
	mv "${tmp_crates}.filtered" "$tmp_crates"
fi

if [ -n "$EXCLUDE_RE" ]; then
	grep -v -E "$EXCLUDE_RE" "$tmp_crates" > "${tmp_crates}.filtered" || true
	mv "${tmp_crates}.filtered" "$tmp_crates"
fi

total="$(wc -l < "$tmp_crates" | tr -d ' ')"
[ "$total" -gt 0 ] || die "no crates selected"

if [ "$MAX_CRATES" -gt 0 ] 2>/dev/null; then
	head -n "$MAX_CRATES" "$tmp_crates" > "${tmp_crates}.limited"
	mv "${tmp_crates}.limited" "$tmp_crates"
	total="$(wc -l < "$tmp_crates" | tr -d ' ')"
fi

log "Selected ${total} crates (scope=${SCOPE})"
echo "selected_crates=${total}" >> "$RUN_SUMMARY"
echo "scope=${SCOPE}" >> "$RUN_SUMMARY"
echo "solana_version=${SOLANA_VERSION}" >> "$RUN_SUMMARY"
echo "compiler_solana_version=${COMPILER_SOLANA_VERSION}" >> "$RUN_SUMMARY"
echo "fallback_compiler_solana_version=${FALLBACK_COMPILER_SOLANA_VERSION}" >> "$RUN_SUMMARY"
echo "platform_tools_version=${PLATFORM_TOOLS_VERSION}" >> "$RUN_SUMMARY"

ok=0
fail=0
skip=0
partial=0
no_rlib=0
idx=0

while IFS= read -r crate; do
	[ -n "$crate" ] || continue
	idx=$((idx + 1))
	versions_file="$VERSIONS_DIR/$crate.txt"
	[ -f "$versions_file" ] || {
		warn "[$idx/$total] skip $crate: missing versions file"
		skip=$((skip + 1))
		echo "skip_missing_versions=$crate" >> "$RUN_SUMMARY"
		continue
	}

	if [ "$FORCE" -eq 0 ] && [ -f "$STATE_DIR/success/$crate.ok" ]; then
		log "[$idx/$total] skip $crate: already successful"
		skip=$((skip + 1))
		continue
	fi

	log "[$idx/$total] build $crate"
	log_file="$STATE_DIR/logs/$crate.log"

	cmd=(
		python3 "$FACTORY_DIR/get-rlibs-from-crate.py"
		--solana-version "$SOLANA_VERSION"
		--compiler-solana-version "$COMPILER_SOLANA_VERSION"
		--fallback-compiler-solana-version "$FALLBACK_COMPILER_SOLANA_VERSION"
		--platform-tools-version "$PLATFORM_TOOLS_VERSION"
		--crate "$crate"
		--versions-file "$versions_file"
	)
	if [ "$CLEANUP_TARGET" -eq 1 ]; then
		cmd+=(--cleanup-target)
	fi
	if [ "$CLEANUP_SOLANA" -eq 1 ]; then
		cmd+=(--cleanup-solana)
	fi

	if "${cmd[@]}" > "$log_file" 2>&1; then
		ok=$((ok + 1))
		rm -f "$STATE_DIR/failed/$crate.fail"
		rm -f "$STATE_DIR/failed/$crate.partial"
		printf 'ok\n' > "$STATE_DIR/success/$crate.ok"
		echo "ok=$crate" >> "$RUN_SUMMARY"
	else
		done_line="$(grep -E 'Done: [0-9]+/[0-9]+ versions produced rlibs' "$log_file" | tail -n 1 || true)"
		done_ok="$(printf '%s\n' "$done_line" | sed -E 's/.*Done: ([0-9]+)\/([0-9]+).*/\1/')"
		done_total="$(printf '%s\n' "$done_line" | sed -E 's/.*Done: ([0-9]+)\/([0-9]+).*/\2/')"
		if [ -n "$done_ok" ] && [ -n "$done_total" ] && [ "$done_ok" -gt 0 ] 2>/dev/null; then
			partial=$((partial + 1))
			rm -f "$STATE_DIR/failed/$crate.fail"
			rm -f "$STATE_DIR/failed/$crate.norlib"
			printf 'partial %s/%s\n' "$done_ok" "$done_total" > "$STATE_DIR/failed/$crate.partial"
			printf 'partial %s/%s\n' "$done_ok" "$done_total" > "$STATE_DIR/success/$crate.ok"
			echo "partial=$crate built=$done_ok total=$done_total log=$log_file" >> "$RUN_SUMMARY"
			warn "[$idx/$total] partial $crate: $done_ok/$done_total versions (see $log_file)"
		elif grep -Eq 'Rlib for .+ not found' "$log_file"; then
			no_rlib=$((no_rlib + 1))
			rm -f "$STATE_DIR/failed/$crate.fail" "$STATE_DIR/failed/$crate.partial"
			printf 'no_rlib\n' > "$STATE_DIR/failed/$crate.norlib"
			printf 'no_rlib\n' > "$STATE_DIR/success/$crate.ok"
			echo "no_rlib=$crate log=$log_file" >> "$RUN_SUMMARY"
			warn "[$idx/$total] no_rlib $crate (likely proc-macro/host-only, see $log_file)"
		else
			fail=$((fail + 1))
			rm -f "$STATE_DIR/success/$crate.ok"
			printf 'fail\n' > "$STATE_DIR/failed/$crate.fail"
			echo "fail=$crate log=$log_file" >> "$RUN_SUMMARY"
			warn "[$idx/$total] failed $crate (see $log_file)"
		fi
	fi
done < "$tmp_crates"

log "Done: ok=${ok} partial=${partial} no_rlib=${no_rlib} fail=${fail} skip=${skip}"
log "Summary: $RUN_SUMMARY"
echo "ok=${ok}" >> "$RUN_SUMMARY"
echo "partial=${partial}" >> "$RUN_SUMMARY"
echo "no_rlib=${no_rlib}" >> "$RUN_SUMMARY"
echo "fail=${fail}" >> "$RUN_SUMMARY"
echo "skip=${skip}" >> "$RUN_SUMMARY"

if [ "$fail" -gt 0 ]; then
	exit 1
fi
