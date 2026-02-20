#!/usr/bin/env python3
"""
Batch-download and build Solana/Anchor crate rlibs using get-rlibs-from-crate.py.

Default behavior is tuned for Solana 1.18.16:
- scope: solana
- mode: latest version per crate
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


CRATES_API = "https://crates.io/api/v1"
AGAVE_CARGO_URL = "https://raw.githubusercontent.com/anza-xyz/agave/master/Cargo.toml"
ANCHOR_CARGO_URL = "https://raw.githubusercontent.com/coral-xyz/anchor/master/Cargo.toml"
DEFAULT_TIMEOUT = 45
DEFAULT_RETRIES = 6

# Static whitelist of crates known to appear in on-chain SBF program builds.
# Used by scope=solana (default).  For dynamic discovery use scope=solana-all.
# Third-party Rust libs (borsh, serde, etc.) are captured via deps/ extraction
# in get-rlibs-from-crate.py --extract-deps, so they don't need to be listed here.
SBF_PROGRAM_CRATES: frozenset[str] = frozenset({
    # --- CORE: solana-program and its transitive micro-crates (2.x era) ---
    "solana-program",
    "solana-account",
    "solana-account-info",
    "solana-address",
    "solana-atomic-u64",
    "solana-big-mod-exp",
    "solana-bincode",
    "solana-blake3-hasher",
    "solana-borsh",
    "solana-clock",
    "solana-cpi",
    "solana-decode-error",
    "solana-define-syscall",
    "solana-epoch-rewards",
    "solana-epoch-schedule",
    "solana-fee-calculator",
    "solana-frozen-abi",
    "solana-frozen-abi-macro",
    "solana-hash",
    "solana-instruction",
    "solana-instruction-error",
    "solana-instructions-sysvar",
    "solana-keccak-hasher",
    "solana-last-restart-slot",
    "solana-msg",
    "solana-native-token",
    "solana-nonce",
    "solana-precompile-error",
    "solana-program-entrypoint",
    "solana-program-error",
    "solana-program-memory",
    "solana-program-option",
    "solana-program-pack",
    "solana-pubkey",
    "solana-rent",
    "solana-sanitize",
    "solana-sdk-ids",
    "solana-secp256k1-recover",
    "solana-serde-varint",
    "solana-serialize-utils",
    "solana-sha256-hasher",
    "solana-short-vec",
    "solana-slot-hashes",
    "solana-slot-history",
    "solana-stable-layout",
    "solana-sysvar",
    "solana-sysvar-id",
    # --- COMMON: frequently imported by programs ---
    "solana-address-lookup-table-interface",
    "solana-compute-budget-interface",
    "solana-config-interface",
    "solana-feature-gate-interface",
    "solana-loader-v2-interface",
    "solana-loader-v3-interface",
    "solana-loader-v4-interface",
    "solana-stake-interface",
    "solana-system-interface",
    "solana-vote-interface",
    "solana-security-txt",
    # --- CRYPTO: syscall wrappers ---
    "solana-bn254",
    "solana-curve25519",
    "solana-poseidon",
    "solana-ed25519-program",
    "solana-secp256k1-program",
    "solana-secp256r1-program",
    "solana-zk-sdk",
    "solana-zk-token-sdk",
    # --- SPL: programs and utility libs ---
    "spl-token",
    "spl-token-2022",
    "spl-associated-token-account",
    "spl-memo",
    "spl-pod",
    "spl-type-length-value",
    "spl-discriminator",
    "spl-tlv-account-resolution",
    "spl-transfer-hook-interface",
    "spl-token-metadata-interface",
    "spl-token-interface",
    "spl-token-2022-interface",
    "spl-associated-token-account-interface",
    "spl-program-error",
    "spl-elgamal-registry-interface",
    "spl-memo-interface",
    "spl-token-confidential-transfer-ciphertext-arithmetic",
    "spl-token-confidential-transfer-proof-extraction",
    "spl-token-confidential-transfer-proof-generation",
    "spl-token-group-interface",
    # --- THIRD-PARTY LIBS (proven to build standalone with cargo-build-sbf) ---
    "arrayref",
    "bincode",
    "borsh",
    "bytemuck",
    "num-traits",
    "thiserror",
})


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(msg: str) -> None:
    print(f"[*] {msg}")


def warn(msg: str) -> None:
    print(f"[!] {msg}", file=sys.stderr)


def die(msg: str, code: int = 1) -> None:
    print(f"[x] {msg}", file=sys.stderr)
    raise SystemExit(code)


def http_get(url: str, timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> bytes:
    headers = {
        "User-Agent": "solana-rlib-batch-builder/1.0",
        "Accept": "application/json,text/plain,*/*",
    }
    err: Exception | None = None
    for i in range(1, retries + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - controlled URLs
                status = getattr(resp, "status", 200)
                if 200 <= status < 300:
                    return resp.read()
                err = RuntimeError(f"HTTP {status} for {url}")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, RuntimeError) as e:
            err = e
            time.sleep(min(i, 5))
    assert err is not None
    raise err


def http_get_json(url: str) -> Any:
    data = http_get(url)
    return json.loads(data.decode("utf-8"))


def parse_workspace_dependency_names(cargo_toml: str, prefix: str) -> list[str]:
    in_dep = False
    out: list[str] = []
    for raw in cargo_toml.splitlines():
        line = raw.strip()
        if line == "[workspace.dependencies]":
            in_dep = True
            continue
        if in_dep and line.startswith("["):
            break
        if not in_dep:
            continue
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name.startswith(prefix):
            out.append(name)
    return sorted(set(out))


def fetch_solana_crate_list() -> list[str]:
    cargo = http_get(AGAVE_CARGO_URL).decode("utf-8", errors="ignore")
    crates = parse_workspace_dependency_names(cargo, "solana-")
    if not crates:
        raise RuntimeError("empty solana crate list from agave Cargo.toml")
    return crates


def fetch_anchor_crate_list() -> list[str]:
    # Seed list covers official anchor workspace crates and historical names.
    seed = {
        "anchor-lang",
        "anchor-spl",
        "anchor-client",
        "anchor-cli",
        "anchor-idl",
        "anchor-lang-idl",
        "anchor-lang-idl-spec",
        "anchor-attribute-access-control",
        "anchor-attribute-account",
        "anchor-attribute-constant",
        "anchor-attribute-error",
        "anchor-attribute-event",
        "anchor-attribute-program",
        "anchor-derive-accounts",
        "anchor-derive-serde",
        "anchor-derive-space",
        "anchor-syn",
        "avm",
    }

    # Include dynamically discovered anchor-* deps from latest anchor-lang/anchor-spl.
    for root in ("anchor-lang", "anchor-spl"):
        versions = fetch_non_yanked_versions(root)
        if not versions:
            continue
        latest = versions[0]
        dep_url = f"{CRATES_API}/crates/{urllib.parse.quote(root)}/{urllib.parse.quote(latest)}/dependencies"
        deps = http_get_json(dep_url)
        for dep in deps.get("dependencies", []):
            crate_id = dep.get("crate_id", "")
            if crate_id.startswith("anchor-"):
                seed.add(crate_id)
    return sorted(seed)


def read_lines(path: pathlib.Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = raw.strip()
        if not s:
            continue
        out.append(s)
    return out


def fetch_non_yanked_versions(crate: str) -> list[str]:
    url = f"{CRATES_API}/crates/{urllib.parse.quote(crate)}/versions"
    data = http_get_json(url)
    versions = []
    for item in data.get("versions", []):
        if not item.get("yanked", False):
            num = item.get("num", "").strip()
            if num:
                versions.append(num)
    return versions


def _prerelease_key(pre: str | None) -> tuple[int, tuple[Any, ...]]:
    if not pre:
        return (1, ())
    toks: list[Any] = []
    for part in pre.split("."):
        if part.isdigit():
            toks.append((0, int(part)))
        else:
            toks.append((1, part))
    return (0, tuple(toks))


def version_sort_key(ver: str) -> tuple[tuple[int, ...], tuple[int, tuple[Any, ...]]]:
    s = ver.strip().lstrip("v")
    if "-" in s:
        core, pre = s.split("-", 1)
    else:
        core, pre = s, None
    nums: list[int] = []
    for p in core.split("."):
        if p.isdigit():
            nums.append(int(p))
        else:
            m = re.match(r"^(\d+)", p)
            nums.append(int(m.group(1)) if m else 0)
    while len(nums) < 4:
        nums.append(0)
    return (tuple(nums), _prerelease_key(pre))


def resolve_crates(scope: str, versions_dir: pathlib.Path) -> list[str]:
    crates: set[str] = set()
    if scope == "solana":
        # Static whitelist — no network fetch, stable and controllable.
        crates.update(SBF_PROGRAM_CRATES)
        log(f"Using static whitelist: {len(SBF_PROGRAM_CRATES)} crates")
    elif scope in ("solana-all", "all"):
        # Dynamic discovery from GitHub — may find new crates but is less stable.
        try:
            solana_crates = fetch_solana_crate_list()
            (versions_dir / "solana-rust-crates.txt").write_text("\n".join(solana_crates) + "\n", encoding="utf-8")
        except Exception as e:
            warn(f"failed to fetch solana crate list online, using local index: {e}")
            solana_crates = read_lines(versions_dir / "solana-rust-crates.txt")
        crates.update(solana_crates)
    if scope in ("anchor", "all"):
        try:
            anchor_crates = fetch_anchor_crate_list()
            (versions_dir / "anchor-crates.txt").write_text("\n".join(anchor_crates) + "\n", encoding="utf-8")
        except Exception as e:
            warn(f"failed to fetch anchor crate list online, using local index: {e}")
            anchor_crates = read_lines(versions_dir / "anchor-crates.txt")
        crates.update(anchor_crates)
    missing = set(read_lines(versions_dir / "missing-crates.txt"))
    crates -= missing
    out = sorted(crates)
    if not out:
        raise RuntimeError("no crates resolved")
    return out


def resolve_versions_for_crate(crate: str, versions_dir: pathlib.Path, latest_only: bool) -> list[str]:
    versions: list[str]
    try:
        versions = fetch_non_yanked_versions(crate)
        if versions:
            (versions_dir / f"{crate}.txt").write_text("\n".join(sorted(set(versions), key=lambda s: s)) + "\n", encoding="utf-8")
    except Exception as e:
        warn(f"{crate}: online versions fetch failed, using local index ({e})")
        versions = read_lines(versions_dir / f"{crate}.txt")

    if not versions:
        return []

    if latest_only:
        latest = max(versions, key=version_sort_key)
        return [latest]
    return versions


def load_state(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"meta": {"created_at": now_iso()}, "crates": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"meta": {"created_at": now_iso()}, "crates": {}}


def save_state(path: pathlib.Path, state: dict[str, Any]) -> None:
    state.setdefault("meta", {})
    state["meta"]["updated_at"] = now_iso()
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def classify_result(log_text: str, rc: int) -> tuple[str, int, int]:
    """
    Return (status, built, total)
    status in: ok|partial|no_rlib|failed
    """
    done_match = re.findall(r"Done:\s+(\d+)/(\d+)\s+versions produced rlibs", log_text)
    if rc == 0:
        if done_match:
            built, total = map(int, done_match[-1])
            if built == 0 and "Rlib for" in log_text and "not found" in log_text:
                return ("no_rlib", built, total)
            return ("ok", built, total)
        return ("ok", 0, 0)

    if done_match:
        built, total = map(int, done_match[-1])
        if built > 0:
            return ("partial", built, total)
    if "Rlib for" in log_text and "not found" in log_text:
        return ("no_rlib", 0, 0)
    return ("failed", 0, 0)


def run_crate_build(
    factory_dir: pathlib.Path,
    crate: str,
    versions: list[str],
    args: argparse.Namespace,
    log_file: pathlib.Path,
) -> tuple[int, str]:
    cmd = [
        sys.executable,
        str(factory_dir / "get-rlibs-from-crate.py"),
        "--solana-version",
        args.solana_version,
        "--compiler-solana-version",
        args.compiler_solana_version,
        "--fallback-compiler-solana-version",
        args.fallback_compiler_solana_version,
        "--platform-tools-version",
        args.platform_tools_version,
        "--sbf-arch", args.sbf_arch,
        "--crate",
        crate,
    ]

    tmp_versions_file: pathlib.Path | None = None
    if args.latest_only:
        cmd.extend(["--version", versions[0]])
    else:
        # Use a temp versions file so get-rlibs-from-crate.py handles retries/version loop.
        fd, tmp = tempfile.mkstemp(prefix=f"{crate}-", suffix=".versions.txt")
        os.close(fd)
        tmp_versions_file = pathlib.Path(tmp)
        tmp_versions_file.write_text("\n".join(versions) + "\n", encoding="utf-8")
        cmd.extend(["--versions-file", str(tmp_versions_file)])

    if args.cleanup_target:
        cmd.append("--cleanup-target")
    if args.cleanup_solana:
        cmd.append("--cleanup-solana")
    cmd.append("--extract-deps")

    if args.dry_run:
        if tmp_versions_file and tmp_versions_file.exists():
            tmp_versions_file.unlink()
        return (0, "DRY-RUN: " + " ".join(cmd))

    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w", encoding="utf-8") as lf:
        lf.write("$ " + " ".join(cmd) + "\n")
        lf.flush()

        proc = subprocess.Popen(
            cmd,
            cwd=str(factory_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        captured: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            captured.append(line)
            lf.write(line)
            lf.flush()
            if args.stream:
                # Stream child output to the main terminal in real time.
                sys.stdout.write(line)
                sys.stdout.flush()
        proc.stdout.close()
        rc = proc.wait()

    if tmp_versions_file and tmp_versions_file.exists():
        tmp_versions_file.unlink()

    text = "".join(captured) if not args.stream else log_file.read_text(encoding="utf-8", errors="ignore")
    return (rc, text)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Solana/Anchor crate rlibs (default: Solana 1.18.16, all versions per crate)."
    )
    parser.add_argument("--solana-version", default="1.18.16")
    parser.add_argument("--compiler-solana-version", default="1.18.16")
    parser.add_argument("--fallback-compiler-solana-version", default="1.18.16")
    parser.add_argument("--platform-tools-version", default="v1.48")
    parser.add_argument("--sbf-arch", choices=("sbfv1", "sbfv3", "both", "auto"), default="auto")
    parser.add_argument("--scope", choices=("solana", "solana-all", "anchor", "all"), default="solana")
    parser.add_argument(
        "--latest-only",
        action="store_true",
        default=False,
        help="Build only latest non-yanked version per crate (default: build all versions)",
    )
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="Build all non-yanked versions per crate (default behavior)",
    )
    parser.add_argument("--include", help="Only process crates matching regex")
    parser.add_argument("--exclude", help="Skip crates matching regex")
    parser.add_argument("--max-crates", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="Re-run crates already marked success/partial/no_rlib")
    parser.add_argument("--cleanup-target", action="store_true")
    parser.add_argument("--cleanup-solana", action="store_true")
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Do not mirror child build output to main terminal (still written to per-crate logs)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--factory-dir", default=str(pathlib.Path(__file__).resolve().parent))
    parser.add_argument("--versions-dir", default="")
    parser.add_argument("--state-dir", default="")
    args = parser.parse_args()

    if args.all_versions:
        args.latest_only = False
    args.stream = not args.no_stream

    factory_dir = pathlib.Path(args.factory_dir).resolve()
    versions_dir = pathlib.Path(args.versions_dir).resolve() if args.versions_dir else (factory_dir / "versions")
    state_dir = pathlib.Path(args.state_dir).resolve() if args.state_dir else (factory_dir / "run-state")
    logs_dir = state_dir / "logs-latest"
    state_file = state_dir / "latest-build-state.json"
    summary_file = state_dir / f"run-latest-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')}.summary"

    versions_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    crates = resolve_crates(args.scope, versions_dir)
    if args.include:
        pat = re.compile(args.include)
        crates = [c for c in crates if pat.search(c)]
    if args.exclude:
        pat = re.compile(args.exclude)
        crates = [c for c in crates if not pat.search(c)]
    if args.max_crates > 0:
        crates = crates[: args.max_crates]

    if not crates:
        die("no crates selected after filters")

    state = load_state(state_file)
    state.setdefault("crates", {})
    state.setdefault("meta", {})
    state["meta"]["config"] = {
        "solana_version": args.solana_version,
        "compiler_solana_version": args.compiler_solana_version,
        "fallback_compiler_solana_version": args.fallback_compiler_solana_version,
        "platform_tools_version": args.platform_tools_version,
        "scope": args.scope,
        "latest_only": bool(args.latest_only),
    }

    ok = partial = no_rlib = fail = skip = 0
    summary_lines = [
        f"selected_crates={len(crates)}",
        f"scope={args.scope}",
        f"latest_only={str(args.latest_only).lower()}",
        f"solana_version={args.solana_version}",
        f"compiler_solana_version={args.compiler_solana_version}",
        f"fallback_compiler_solana_version={args.fallback_compiler_solana_version}",
        f"platform_tools_version={args.platform_tools_version}",
    ]

    log(f"Selected {len(crates)} crates (scope={args.scope}, latest_only={args.latest_only})")

    for i, crate in enumerate(crates, start=1):
        existing = state["crates"].get(crate, {})
        prev_status = existing.get("status")
        if not args.force and prev_status in {"ok", "partial", "no_rlib", "failed"}:
            log(f"[{i}/{len(crates)}] skip {crate}: already {prev_status}")
            skip += 1
            continue

        versions = resolve_versions_for_crate(crate, versions_dir, args.latest_only)
        if not versions:
            warn(f"[{i}/{len(crates)}] fail {crate}: no versions found")
            fail += 1
            state["crates"][crate] = {
                "status": "failed",
                "error": "no versions found",
                "last_run": now_iso(),
            }
            summary_lines.append(f"fail={crate} reason=no_versions")
            save_state(state_file, state)
            continue

        log(f"[{i}/{len(crates)}] build {crate} ({'latest' if args.latest_only else f'{len(versions)} versions'})")
        log_file = logs_dir / f"{crate}.log"
        rc, log_text = run_crate_build(factory_dir, crate, versions, args, log_file)
        status, built, total = classify_result(log_text, rc)

        if status == "ok":
            ok += 1
            summary_lines.append(f"ok={crate}")
        elif status == "partial":
            partial += 1
            summary_lines.append(f"partial={crate} built={built} total={total}")
        elif status == "no_rlib":
            no_rlib += 1
            summary_lines.append(f"no_rlib={crate}")
        else:
            fail += 1
            summary_lines.append(f"fail={crate}")

        state["crates"][crate] = {
            "status": status,
            "built": built,
            "total": total,
            "last_run": now_iso(),
            "log": str(log_file),
            "versions_requested": versions if len(versions) <= 8 else [versions[0], "...", versions[-1]],
        }
        save_state(state_file, state)

        if status == "failed":
            warn(f"[{i}/{len(crates)}] failed {crate} (log: {log_file})")
        elif status == "partial":
            warn(f"[{i}/{len(crates)}] partial {crate}: {built}/{total} (log: {log_file})")
        elif status == "no_rlib":
            warn(f"[{i}/{len(crates)}] no_rlib {crate} (log: {log_file})")

    summary_lines.extend(
        [
            f"ok={ok}",
            f"partial={partial}",
            f"no_rlib={no_rlib}",
            f"fail={fail}",
            f"skip={skip}",
        ]
    )
    summary_file.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    log(f"Done: ok={ok} partial={partial} no_rlib={no_rlib} fail={fail} skip={skip}")
    log(f"State: {state_file}")
    log(f"Summary: {summary_file}")

    return 1 if fail > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
