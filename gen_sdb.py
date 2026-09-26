#!/usr/bin/env python3
"""
Generate SDB signature databases from compiled rlibs.

Pipeline:
  1. Per-rlib  : ar x rlib → r2 per .o → individual SDB in sdb/by-crate/<group>/<crate>/
  2. Group merge: collect per-rlib SDBs → merged-<group>.sdb in sdb/

Dedup on merge: (namespace, realname) pairs — exact duplicates are dropped.
  namespace encodes crate + version + sbpf + sol + pt
  realname  is the demangled Rust symbol (_ZN... demangled)

Parallelism:
  --rlib-workers : concurrent r2 jobs (default: CPU count, up to 32)
  --group-workers: concurrent groups   (default: 2)
"""

import argparse
import collections
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

ROOT_DIR = pathlib.Path(__file__).resolve().parent
RLIBS_DIR = ROOT_DIR / "rlibs"
SDB_DIR = ROOT_DIR / "sdb"
BY_CRATE_DIR = SDB_DIR / "by-crate"

RLIB_RE = re.compile(
    r"^lib(.+?)-((?:[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:-[a-zA-Z][^-]*)?))"
    r"-(sbpfv[0-9]+)-(s[0-9]+_[0-9]+)-(pt[0-9]+_[0-9]+)\.rlib$"
)
HASH_ONLY_RE = re.compile(r"^lib.+-[0-9a-f]{16}-(sbpfv[0-9]+)")


def parse_rlib(path: pathlib.Path):
    m = RLIB_RE.match(path.name)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)


def sanitize(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", s)


def make_namespace(crate: str, version: str, sbpf: str, sol: str, pt: str) -> str:
    return f"{sanitize(crate)}__{sanitize(version)}__{sbpf}__{sol}__{pt}"


def _r2_gen_for_obj(
    obj_path: pathlib.Path, ns: str, cpu: str, out_sdb: str, timeout: int
) -> int:
    r2_cmds = f"aaa; zs+ {ns}; zg; zos {out_sdb}"
    cmd = [
        "r2",
        "-e",
        "asm.arch=sbpf",
        "-e",
        f"asm.cpu={cpu}",
        "-qc",
        r2_cmds,
        str(obj_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    m = re.search(r"generated zignatures:\s*(\d+)", result.stdout + result.stderr)
    return int(m.group(1)) if m else 0


def gen_sdb_for_rlib(
    rlib_path: pathlib.Path, out_dir: pathlib.Path, timeout: int = 180
) -> dict:
    """
    Extract .o from rlib archive, run r2 per .o to generate signatures.
    r2 reads .o ELF directly → picks up .symtab (real Rust mangled names).
    Passing the .a directly to r2 loses internal symbols (gives fcn.XXXXXXXX).
    Writes one SDB per rlib: <out_dir>/<namespace>.sdb
    Returns: {rlib, ns, sigs, error, sdb_path, cached}
    """
    parsed = parse_rlib(rlib_path)
    if not parsed:
        return {
            "rlib": rlib_path,
            "ns": None,
            "sigs": 0,
            "error": f"unparseable: {rlib_path.name}",
            "sdb_path": None,
            "cached": False,
        }

    crate, version, sbpf, sol, pt = parsed
    ns = make_namespace(crate, version, sbpf, sol, pt)
    cpu = sbpf

    out_dir.mkdir(parents=True, exist_ok=True)
    sdb_path = out_dir / f"{ns}.sdb"

    if sdb_path.exists() and sdb_path.stat().st_size > 0:
        result = subprocess.run(
            ["r2", "-qc", f"zo {sdb_path}; zl", "/dev/null"],
            capture_output=True,
            text=True,
        )
        m = re.search(r"(\d+)", result.stdout + result.stderr)
        cached_sigs = int(m.group(1)) if m else 0
        if cached_sigs > 0:
            return {
                "rlib": rlib_path,
                "ns": ns,
                "sigs": cached_sigs,
                "error": None,
                "sdb_path": sdb_path,
                "cached": True,
            }

    tmpdir = tempfile.mkdtemp(prefix="gen_sdb_")
    try:
        ar = subprocess.run(
            ["ar", "x", str(rlib_path)], cwd=tmpdir, capture_output=True, text=True
        )
        if ar.returncode != 0:
            return {
                "rlib": rlib_path,
                "ns": ns,
                "sigs": 0,
                "error": f"ar x failed: {ar.stderr[:120]}",
                "sdb_path": None,
                "cached": False,
            }

        obj_files = sorted(
            p for p in pathlib.Path(tmpdir).iterdir() if p.suffix == ".o"
        )
        if not obj_files:
            return {
                "rlib": rlib_path,
                "ns": ns,
                "sigs": 0,
                "error": "no .o members",
                "sdb_path": None,
                "cached": False,
            }

        tmp_sdb = sdb_path.with_suffix(".sdb.tmp")
        if tmp_sdb.exists():
            tmp_sdb.unlink()

        total = 0
        for obj in obj_files:
            try:
                total += _r2_gen_for_obj(obj, ns, cpu, str(tmp_sdb), timeout)
            except subprocess.TimeoutExpired:
                return {
                    "rlib": rlib_path,
                    "ns": ns,
                    "sigs": total,
                    "error": f"timeout: {obj.name}",
                    "sdb_path": None,
                    "cached": False,
                }

        if total > 0:
            tmp_sdb.rename(sdb_path)
        else:
            if tmp_sdb.exists():
                tmp_sdb.unlink()
            sdb_path = None

        return {
            "rlib": rlib_path,
            "ns": ns,
            "sigs": total,
            "error": None,
            "sdb_path": sdb_path,
            "cached": False,
        }
    except Exception as exc:
        return {
            "rlib": rlib_path,
            "ns": ns,
            "sigs": 0,
            "error": str(exc),
            "sdb_path": None,
            "cached": False,
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _parse_sdb_signatures(sdb_path: pathlib.Path) -> list:
    result = subprocess.run(
        ["r2", "-qc", f"zo {sdb_path}; z", "/dev/null"], capture_output=True, text=True
    )
    sigs = []
    current = None
    for line in result.stdout.splitlines():
        hdr = re.match(r"^\(([^)]+)\) (.+):$", line)
        if hdr:
            if current and current.get("realname") and current.get("name"):
                sigs.append(current)
            current = {"ns": hdr.group(1), "name": hdr.group(2)}
            continue
        if current is None:
            continue
        kv = re.match(r"^\s+(\w+):\s+(.+)$", line)
        if kv:
            current[kv.group(1)] = kv.group(2).strip()
    if current and current.get("realname") and current.get("name"):
        sigs.append(current)
    return sigs


def _bytes_with_mask(raw_bytes: str, mask: str) -> str:
    """Merge bytes+mask into r2 dot-notation: mask byte 00 → '..'  in bytes."""
    result = []
    for i in range(0, min(len(raw_bytes), len(mask)), 2):
        b = raw_bytes[i : i + 2]
        m = mask[i : i + 2]
        result.append(".." if m == "00" else b)
    return "".join(result)


def _write_sigs_to_sdb(new_sigs: list, out_sdb: pathlib.Path) -> None:
    """Append new_sigs to out_sdb (creates if absent) via r2 za commands."""
    script_lines = []
    if out_sdb.exists():
        script_lines.append(f"zo {out_sdb}")
    for sig in new_sigs:
        rawname = sig.get("rawname", "").strip()
        realname = sig.get("realname", "").strip()
        b = sig.get("bytes", "").strip()
        m = sig.get("mask", "").strip()
        if not rawname or not b:
            continue
        masked = _bytes_with_mask(b, m)
        script_lines.append(f"za {rawname} b {masked}")
        if realname:
            script_lines.append(f"za {rawname} n {realname}")
    script_lines.append(f"zos {out_sdb}")

    with tempfile.NamedTemporaryFile("w", suffix=".r2", delete=False) as f:
        f.write("\n".join(script_lines) + "\n")
        script_path = f.name
    try:
        subprocess.run(
            ["r2", "-qi", script_path, "/dev/null"],
            capture_output=True,
            text=True,
        )
    finally:
        os.unlink(script_path)


def _merge_sdb_files(sdb_paths: list, out_sdb: pathlib.Path, group_name: str) -> dict:
    """
    Merge per-rlib SDBs into one group SDB.
    Dedup key: (bytes, mask) — pure bytecode identity, independent of symbol names.
    Only signatures with a new (bytes, mask) pair are written to out_sdb.
    """
    seen: set = set()
    total_written = 0
    dupes = 0

    if out_sdb.exists():
        out_sdb.unlink()

    for sdb_path in sorted(sdb_paths):
        if not sdb_path or not sdb_path.exists():
            continue

        sigs = _parse_sdb_signatures(sdb_path)
        new_sigs = []
        for sig in sigs:
            b = sig.get("bytes", "").strip()
            m = sig.get("mask", "").strip()
            if not b or not m:
                continue
            key = (b, m)
            if key in seen:
                dupes += 1
            else:
                seen.add(key)
                new_sigs.append(sig)

        if not new_sigs:
            continue

        _write_sigs_to_sdb(new_sigs, out_sdb)
        total_written += len(new_sigs)

    size_kb = out_sdb.stat().st_size // 1024 if out_sdb.exists() else 0
    return {
        "group": group_name,
        "total_sigs": total_written,
        "dupes": dupes,
        "size_kb": size_kb,
    }


def collect_rlibs() -> dict:
    groups: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    seen: set = set()

    def add(rlib: pathlib.Path):
        if rlib in seen:
            return
        if HASH_ONLY_RE.match(rlib.name):
            return
        parsed = parse_rlib(rlib)
        if parsed:
            crate, _, sbpf, sol, pt = parsed
            groups[(sbpf, sol, pt)][crate].append(rlib)
            seen.add(rlib)

    for rlib in RLIBS_DIR.glob("*/*.rlib"):
        add(rlib)
    for rlib in RLIBS_DIR.glob("*/deps/*.rlib"):
        add(rlib)

    return {k: dict(v) for k, v in groups.items()}


def process_group(
    group_key: tuple, crate_map: dict, rlib_workers: int, verbose: bool = True
) -> dict:
    sbpf, sol, pt = group_key
    group_name = f"{sbpf}-{sol}-{pt}"
    group_out_dir = BY_CRATE_DIR / group_name

    all_rlibs = [(crate, r) for crate, rlibs in crate_map.items() for r in rlibs]
    total = len(all_rlibs)

    if verbose:
        print(
            f"\n[{group_name}] {total} rlibs / {len(crate_map)} crates / {rlib_workers} workers"
        )

    t0 = time.time()
    per_rlib_sdbs: list = []
    errors = 0
    total_sigs = 0
    cached = 0

    def _run(crate_rlib):
        crate, rlib = crate_rlib
        crate_dir = group_out_dir / crate
        return gen_sdb_for_rlib(rlib, crate_dir)

    with ThreadPoolExecutor(max_workers=rlib_workers) as ex:
        futures = {ex.submit(_run, cr): cr for cr in all_rlibs}
        done = 0
        for future in as_completed(futures):
            done += 1
            res = future.result()
            if res.get("error"):
                errors += 1
                if verbose:
                    print(
                        f"  [{done}/{total}] ERR {pathlib.Path(str(res['rlib'])).name}: {res['error'][:80]}"
                    )
            else:
                total_sigs += res["sigs"]
                if res.get("cached"):
                    cached += 1
                if res["sdb_path"]:
                    per_rlib_sdbs.append(pathlib.Path(str(res["sdb_path"])))
                if verbose and res["sigs"] > 0 and not res.get("cached"):
                    print(f"  [{done}/{total}] {res['ns']}: {res['sigs']} sigs")

    elapsed_r2 = time.time() - t0
    if verbose:
        print(
            f"  r2 done: {total_sigs} sigs, {errors} err, {cached} cached, {elapsed_r2:.0f}s"
        )

    out_sdb = SDB_DIR / f"merged-{group_name}.sdb"
    if verbose:
        print(f"  merging {len(per_rlib_sdbs)} per-rlib SDBs → {out_sdb.name}")

    merge_stats = _merge_sdb_files(per_rlib_sdbs, out_sdb, group_name)
    elapsed_total = time.time() - t0

    size_kb = out_sdb.stat().st_size // 1024 if out_sdb.exists() else 0
    print(
        f"  [{group_name}] {merge_stats['total_sigs']} sigs, "
        f"{merge_stats['dupes']} dupes dropped, {size_kb} KB, {elapsed_total:.0f}s"
    )
    return {**merge_stats, "size_kb": size_kb}


def main():
    default_workers = min(os.cpu_count() or 8, 32)

    parser = argparse.ArgumentParser(
        description="Generate SDB signature files from rlibs (parallel)."
    )
    parser.add_argument(
        "--rlib-workers",
        type=int,
        default=default_workers,
        help=f"Parallel r2 jobs (default: {default_workers})",
    )
    parser.add_argument(
        "--group-workers", type=int, default=2, help="Parallel groups (default: 2)"
    )
    parser.add_argument(
        "--groups",
        nargs="*",
        help="Only process these groups (e.g. sbpfv3-s2_1-pt1_43)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached per-rlib SDBs and regenerate",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show plan without generating SDBs"
    )
    args = parser.parse_args()

    groups = collect_rlibs()
    if not groups:
        print(f"No versioned rlibs found in {RLIBS_DIR}")
        sys.exit(1)

    if args.groups:
        filter_set = set(args.groups)
        groups = {
            k: v for k, v in groups.items() if f"{k[0]}-{k[1]}-{k[2]}" in filter_set
        }
        if not groups:
            print("No matching groups. Available:")
            for k in collect_rlibs():
                print(f"  {k[0]}-{k[1]}-{k[2]}")
            sys.exit(1)

    if args.no_cache and BY_CRATE_DIR.exists():
        shutil.rmtree(BY_CRATE_DIR)

    total_rlibs = sum(len(v) for cm in groups.values() for v in cm.values())
    print(f"Found {total_rlibs} rlibs in {len(groups)} groups:")
    for key in sorted(groups):
        rlib_count = sum(len(v) for v in groups[key].values())
        print(
            f"  {key[0]}-{key[1]}-{key[2]}: {rlib_count} rlibs, {len(groups[key])} crates"
        )

    if args.dry_run:
        print("\n[dry-run] Exiting.")
        sys.exit(0)

    SDB_DIR.mkdir(parents=True, exist_ok=True)
    BY_CRATE_DIR.mkdir(parents=True, exist_ok=True)

    sorted_keys = sorted(groups.keys())
    all_stats = []

    if args.group_workers <= 1 or len(sorted_keys) == 1:
        for key in sorted_keys:
            stats = process_group(key, groups[key], args.rlib_workers)
            all_stats.append(stats)
    else:
        with ProcessPoolExecutor(
            max_workers=min(args.group_workers, len(sorted_keys))
        ) as ex:
            futures = {
                ex.submit(process_group, k, groups[k], args.rlib_workers, False): k
                for k in sorted_keys
            }
            for future in as_completed(futures):
                try:
                    all_stats.append(future.result())
                except Exception as exc:
                    print(f"ERROR: {exc}")

    print("\n=== Summary ===")
    total_all = 0
    for stats in sorted(all_stats, key=lambda s: s["group"]):
        print(
            f"  {stats['group']}: {stats['total_sigs']} sigs, "
            f"{stats['dupes']} dupes dropped, {stats['size_kb']} KB"
        )
        total_all += stats["total_sigs"]
    print(f"  TOTAL: {total_all} unique sigs")
    print("\nMerged SDB files:")
    for sdb in sorted(SDB_DIR.glob("merged-*.sdb")):
        size_mb = sdb.stat().st_size / 1024 / 1024
        print(f"  {sdb.name}: {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
