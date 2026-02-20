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

from build_crate import build_crate, get_sbf_archs_for_version, get_target_triple_for_arch

init()

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


def resolve_dep_rlib_name(stem: str, lock_versions: dict, sbf_arch: str, tools_tag: str) -> str:
    m = _DEP_RLIB_RE.match(stem)
    if not m:
        return f"{stem}-{sbf_arch}-{tools_tag}"
    crate_name = m.group(1)
    versions = lock_versions.get(crate_name, [])
    if len(versions) == 1:
        return f"lib{crate_name}-{versions[0]}-{sbf_arch}-{tools_tag}"
    return f"{stem}-{sbf_arch}-{tools_tag}"


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
    return f"{cargo_bin_str}{os.pathsep}{current_path}" if current_path else cargo_bin_str


def preflight_host_rust_toolchain():
    tool_path = with_cargo_bin_in_path()
    missing = [tool for tool in ("cargo", "rustc") if shutil.which(tool, path=tool_path) is None]
    if not missing:
        return True
    print(f"{Fore.RED}Missing host tools: {, .join(missing)}.{Style.RESET_ALL}")
    return False


def parse_sbf_archs(args, version: str) -> List[str]:
    if args.sbf_arch == "auto":
        return get_sbf_archs_for_version(version)
    elif args.sbf_arch == "both":
        return ["sbfv1", "sbfv2"]
    else:
        return [args.sbf_arch]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solana-version", required=True)
    parser.add_argument("--compiler-solana-version", required=True)
    parser.add_argument("--fallback-compiler-solana-version", required=True)
    parser.add_argument("--disable-compiler-fallback", action="store_true")
    parser.add_argument("--platform-tools-version", required=True)
    parser.add_argument("--sbf-arch", choices=["sbfv1", "sbfv2", "both", "auto"], default="auto")
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
    tools_tag = args.platform_tools_version.replace(".", "_")

    print(f"{Fore.BLUE}Getting rlibs for {crate} from {len(versions)} versions{Style.RESET_ALL}")
    for version in versions:
        version = version.strip()
        try:
            compiler_versions = [args.compiler_solana_version]
            if (not args.disable_compiler_fallback) and args.fallback_compiler_solana_version:
                if args.fallback_compiler_solana_version not in compiler_versions:
                    compiler_versions.append(args.fallback_compiler_solana_version)

            sbf_archs = parse_sbf_archs(args, version)
            built = False
            last_status = ""
            used_compiler = None
            built_rlibs = []
            
            for i, compiler_version in enumerate(compiler_versions):
                used_compiler = compiler_version
                print(f"{Fore.BLUE}Building {crate}:{version} with compiler {compiler_version} [archs={sbf_archs}]{Style.RESET_ALL}")
                ok, status, rlibs = build_crate(
                    crate, version, compiler_version,
                    only_rlib=True,
                    tools_version=args.platform_tools_version,
                    sbf_archs=sbf_archs,
                )
                last_status = status
                built_rlibs = rlibs
                if ok:
                    built = True
                    break
                if i + 1 < len(compiler_versions) and not needs_compiler_fallback(status):
                    break

            if not built or not built_rlibs:
                print(f"{Fore.RED}Error building {crate}:{version}: build failed{Style.RESET_ALL}")
                continue

            for sbf_arch, rlib_path in built_rlibs:
                target_name = f"lib{crate.replace(-, _)}-{version}-{sbf_arch}-{tools_tag}.rlib"
                target_path = RLIBS_DIR / crate / target_name
                target_path.parent.mkdir(parents=True, exist_ok=True)
                
                if target_path.exists():
                    print(f"{Fore.YELLOW}Rlib {target_path.name} exists, skipping{Style.RESET_ALL}")
                else:
                    shutil.copy(rlib_path, target_path)
                    print(f"{Fore.GREEN}Saved {target_path.name} (compiler={used_compiler}){Style.RESET_ALL}")

                if args.extract_deps:
                    target_triple = get_target_triple_for_arch(sbf_arch)
                    crate_dir = CRATES_DIR / f"{crate}-{version}"
                    deps_dir = crate_dir / "target" / target_triple / "release" / "deps"
                    if deps_dir.is_dir():
                        lock_versions = parse_cargo_lock_versions(crate_dir / "Cargo.lock")
                        deps_dst = RLIBS_DIR / crate / "deps"
                        deps_dst.mkdir(parents=True, exist_ok=True)
                        dep_count = 0
                        for dep_rlib in sorted(deps_dir.glob("*.rlib")):
                            out_name = resolve_dep_rlib_name(dep_rlib.stem, lock_versions, sbf_arch, tools_tag)
                            dep_target = deps_dst / f"{out_name}.rlib"
                            if not dep_target.exists():
                                shutil.copy(dep_rlib, dep_target)
                                dep_count += 1
                        if dep_count:
                            print(f"{Fore.CYAN}  deps: {dep_count} new rlibs [{sbf_arch}]{Style.RESET_ALL}")

            success_count += 1

            if args.cleanup_target:
                target_dir = CRATES_DIR / f"{crate}-{version}" / "target"
                if target_dir.exists():
                    shutil.rmtree(target_dir)

            if args.cleanup_solana and used_compiler:
                run_cleanup_solana(used_compiler)

        except KeyboardInterrupt:
            print(f"{Fore.RED}Exiting...{Style.RESET_ALL}")
            break
        except Exception as e:
            print(f"{Fore.RED}Error building {crate}:{version}: {e}{Style.RESET_ALL}")
            continue

    print(f"{Fore.BLUE}Done: {success_count}/{len(versions)} versions produced rlibs{Style.RESET_ALL}")
    raise SystemExit(0 if success_count == len(versions) else 1)


if __name__ == "__main__":
    main()
