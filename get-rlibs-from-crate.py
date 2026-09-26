import argparse
import os
import pathlib
import re
import shutil
import subprocess
from typing import List

try:
    from colorama import Fore, Style, init
except ImportError:

    class _NoColor:
        def __getattr__(self, _name):
            return ""

    Fore = _NoColor()
    Style = _NoColor()

    def init(*_args, **_kwargs):
        return None


from build_crate import (
    build_crate,
    get_sbf_archs_for_version,
    get_target_triple_for_arch,
    get_toolchain_for_crate,
)

init()


def arch_to_name(sbf_arch: str) -> str:
    """Convert cargo-build-sbf arch to output name: sbfv1->sbpfv0, sbfv2->sbpfv3"""
    return {"sbfv1": "sbpfv0", "sbfv2": "sbpfv3"}.get(sbf_arch, sbf_arch)


ROOT_DIR = pathlib.Path(__file__).resolve().parent
CRATES_DIR = ROOT_DIR / "crates"
RLIBS_DIR = ROOT_DIR / "rlibs"

_DEP_RLIB_RE = re.compile(r"^lib(.+)-([0-9a-f]{16})$")


def parse_cargo_lock_versions(cargo_lock_path: pathlib.Path) -> dict:
    if not cargo_lock_path.exists():
        return {}
    text = cargo_lock_path.read_text(encoding="utf-8", errors="ignore")
    result = {}
    for m in re.finditer(
        r"^\[\[package\]\]\s*\nname\s*=\s*\"([^\"]+)\"\s*\nversion\s*=\s*\"([^\"]+)\"",
        text,
        re.MULTILINE,
    ):
        name = m.group(1).replace("-", "_")
        result.setdefault(name, []).append(m.group(2))
    return result


def resolve_dep_rlib_name(
    stem: str,
    lock_versions: dict,
    arch_name: str,
    solana_tag: str,
    pt_tag: str,
    hash_to_version: dict = None,
) -> str:
    """
    Resolve a dep rlib stem to an output filename.
    Output format: lib{crate}-{version}-{arch}-{solana_tag}-pt{pt_tag}.rlib

    Priority:
    1. hash_to_version mapping from compiler-artifact JSON (most accurate)
    2. Cargo.lock single-version lookup
    3. Fallback: keep hash in name (multi-version or stdlib crates)

    Returns None for stdlib crates that should be skipped entirely.
    """
    # Crates provided by the Rust toolchain itself (not in Cargo.lock)
    STDLIB_CRATES = frozenset(
        {
            "alloc",
            "core",
            "std",
            "compiler_builtins",
            "proc_macro",
            "unwind",
            "panic_abort",
            "panic_unwind",
            "rustc_std_workspace_alloc",
            "rustc_std_workspace_core",
            "std_detect",
            "test",
        }
    )

    m = _DEP_RLIB_RE.match(stem)
    if not m:
        # No hash suffix — use stem as-is with toolchain tags
        return f"{stem}-{arch_name}-{solana_tag}-pt{pt_tag}"

    crate_name = m.group(1)
    file_hash = m.group(2)

    # Skip stdlib crates entirely (they don't have useful version info)
    if crate_name in STDLIB_CRATES:
        return None

    # Priority 1: use compiler-artifact JSON mapping (exact hash->version)
    if hash_to_version and file_hash in hash_to_version:
        resolved_name, resolved_ver = hash_to_version[file_hash]
        return f"lib{resolved_name}-{resolved_ver}-{arch_name}-{solana_tag}-pt{pt_tag}"

    # Priority 2: Cargo.lock with unique version
    versions = lock_versions.get(crate_name, [])
    if len(versions) == 1:
        return f"lib{crate_name}-{versions[0]}-{arch_name}-{solana_tag}-pt{pt_tag}"

    # Fallback: multi-version or unknown — keep hash in name
    return f"{stem}-{arch_name}-{solana_tag}-pt{pt_tag}"


def resolve_versions_file(path_str: str):
    path = pathlib.Path(path_str)
    if path.exists():
        return path
    alt = ROOT_DIR / path_str
    if alt.exists():
        return alt
    raise FileNotFoundError(f"versions file not found: {path_str}")


def parse_versions(args):
    if args.versions_file:
        versions = []
        with open(resolve_versions_file(args.versions_file), "r") as f:
            for line in f:
                v = line.strip()
                if not v:
                    continue
                if v.startswith("v"):
                    versions.append(v[1:])
                else:
                    versions.append(v)
        return versions
    if args.version:
        return [args.version.strip()]
    raise ValueError("Either --versions-file or --version must be provided")


def needs_compiler_fallback(status: str):
    hints = (
        "requires rustc",
        "feature `edition2024` is required",
        "older than the `2024` edition",
        "lock file version 4 requires `-Znext-lockfile-bump`",
        "unknown feature `proc_macro_span_shrink`",
    )
    return any(h in status for h in hints)


def run_cleanup_solana(version: str):
    subprocess.run(
        ["bash", str(ROOT_DIR / "remove-solana.sh"), version],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def with_cargo_bin_in_path():
    cargo_bin = pathlib.Path.home() / ".cargo" / "bin"
    current_path = os.environ.get("PATH", "")
    if not cargo_bin.is_dir():
        return current_path
    cargo_bin_str = str(cargo_bin)
    paths = current_path.split(os.pathsep) if current_path else []
    if cargo_bin_str in paths:
        return current_path
    return (
        f"{cargo_bin_str}{os.pathsep}{current_path}" if current_path else cargo_bin_str
    )


def preflight_host_rust_toolchain():
    tool_path = with_cargo_bin_in_path()
    missing = [
        tool
        for tool in ("cargo", "rustc")
        if shutil.which(tool, path=tool_path) is None
    ]
    if not missing:
        return True
    missing_str = ", ".join(missing)
    print(f"{Fore.RED}Missing host tools: {missing_str}.{Style.RESET_ALL}")
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Build rlibs for a single crate across all its versions using the correct toolchain per version."
    )
    parser.add_argument("--cleanup-target", action="store_true")
    parser.add_argument("--cleanup-solana", action="store_true")
    parser.add_argument("--extract-deps", action="store_true")
    parser.add_argument("--crate", required=True)
    parser.add_argument("--versions-file")
    parser.add_argument("--version")
    args = parser.parse_args()

    if not preflight_host_rust_toolchain():
        raise SystemExit(2)

    versions = parse_versions(args)
    crate = args.crate
    success_count = 0

    print(
        f"{Fore.BLUE}Getting rlibs for {crate} from {len(versions)} versions{Style.RESET_ALL}"
    )

    for version in versions:
        version = version.strip()
        try:
            # Get the correct toolchain(s) for this crate+version
            toolchain_entries = get_toolchain_for_crate(crate, version)

            built_any = False
            for sol_ver, pt_ver, solana_tag, sbf_archs in toolchain_entries:
                pt_tag = pt_ver.lstrip("v").replace(".", "_")

                print(
                    f"{Fore.BLUE}Building {crate}:{version} with solana={sol_ver} "
                    f"tools={pt_ver} archs={sbf_archs}{Style.RESET_ALL}"
                )

                ok, status, rlibs, hash_to_version = build_crate(
                    crate,
                    version,
                    sol_ver,
                    only_rlib=True,
                    tools_version=pt_ver,
                    sbf_archs=sbf_archs,
                )

                if not ok or not rlibs:
                    print(
                        f"{Fore.RED}Error building {crate}:{version} "
                        f"(solana={sol_ver}): build failed{Style.RESET_ALL}"
                    )
                    continue

                built_any = True

                for sbf_arch, rlib_path in rlibs:
                    arch_name = arch_to_name(sbf_arch)
                    # New filename format: lib{crate}-{version}-{arch}-{solana_tag}-pt{pt_tag}.rlib
                    target_name = (
                        f"lib{crate.replace('-', '_')}-{version}"
                        f"-{arch_name}-{solana_tag}-pt{pt_tag}.rlib"
                    )
                    target_path = RLIBS_DIR / crate / target_name
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    if target_path.exists():
                        print(
                            f"{Fore.YELLOW}Rlib {target_path.name} exists, skipping{Style.RESET_ALL}"
                        )
                    else:
                        shutil.copy(rlib_path, target_path)
                        print(f"{Fore.GREEN}Saved {target_path.name}{Style.RESET_ALL}")

                    if args.extract_deps:
                        target_triple = get_target_triple_for_arch(sbf_arch, sol_ver)
                        crate_dir = CRATES_DIR / f"{crate}-{version}"
                        deps_dir = (
                            crate_dir / "target" / target_triple / "release" / "deps"
                        )
                        if deps_dir.is_dir():
                            lock_versions = parse_cargo_lock_versions(
                                crate_dir / "Cargo.lock"
                            )
                            deps_dst = RLIBS_DIR / crate / "deps"
                            deps_dst.mkdir(parents=True, exist_ok=True)
                            dep_count = 0
                            for dep_rlib in sorted(deps_dir.glob("*.rlib")):
                                out_name = resolve_dep_rlib_name(
                                    dep_rlib.stem,
                                    lock_versions,
                                    arch_name,
                                    solana_tag,
                                    pt_tag,
                                    hash_to_version=hash_to_version,
                                )
                                if out_name is None:
                                    # Stdlib crate — skip
                                    continue
                                dep_target = deps_dst / f"{out_name}.rlib"
                                if not dep_target.exists():
                                    shutil.copy(dep_rlib, dep_target)
                                    dep_count += 1
                            if dep_count:
                                print(
                                    f"{Fore.CYAN}  deps: {dep_count} new rlibs [{sbf_arch}]{Style.RESET_ALL}"
                                )

            if built_any:
                success_count += 1

            if args.cleanup_target:
                target_dir = CRATES_DIR / f"{crate}-{version}" / "target"
                if target_dir.exists():
                    shutil.rmtree(target_dir)

            if args.cleanup_solana:
                # cleanup all solana versions used for this crate
                for sol_ver, _pt, _tag, _archs in toolchain_entries:
                    run_cleanup_solana(sol_ver)

        except KeyboardInterrupt:
            print(f"{Fore.RED}Exiting...{Style.RESET_ALL}")
            break
        except Exception as e:
            print(f"{Fore.RED}Error building {crate}:{version}: {e}{Style.RESET_ALL}")
            continue

    print(
        f"{Fore.BLUE}Done: {success_count}/{len(versions)} versions produced rlibs{Style.RESET_ALL}"
    )
    raise SystemExit(0 if success_count == len(versions) else 1)


if __name__ == "__main__":
    main()
