import argparse
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Optional, List, Tuple

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

init()

ROOT_DIR = pathlib.Path(__file__).resolve().parent
SOLANA_DIR = ROOT_DIR / "solana"
CRATES_DIR = ROOT_DIR / "crates"

LOCKFILE_V4_HINT = "lock file version 4 requires `-Znext-lockfile-bump`"
EDITION_2024_HINTS = (
    "feature `edition2024` is required",
    "older than the `2024` edition",
)
AHASH_HINT = "use of unstable library feature 'build_hasher_simple_hash_one'"

BLAKE3_LOCK_V183 = """name = "blake3"
version = "1.8.3"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "2468ef7d57b3fb7e16b576e8377cdbde2320c60e1491e961d11da40fc4f02a2d"
dependencies = [
 "arrayref",
 "arrayvec",
 "cc",
 "cfg-if",
 "constant_time_eq",
 "cpufeatures",
 "digest 0.10.7",
]
"""

BLAKE3_LOCK_V182 = """name = "blake3"
version = "1.8.2"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "3888aaa89e4b2a40fca9848e400f6a658a5a3978de7be858e209cafa8be9a4a0"
dependencies = [
 "arrayref",
 "arrayvec",
 "cc",
 "cfg-if",
 "constant_time_eq",
 "digest 0.10.7",
]
"""


def with_cargo_bin_in_path(env=None):
    merged = dict(os.environ if env is None else env)
    cargo_bin = pathlib.Path.home() / ".cargo" / "bin"
    if cargo_bin.is_dir():
        cargo_bin_str = str(cargo_bin)
        current_path = merged.get("PATH", "")
        paths = current_path.split(os.pathsep) if current_path else []
        if cargo_bin_str not in paths:
            merged["PATH"] = (
                f"{cargo_bin_str}{os.pathsep}{current_path}" if current_path else cargo_bin_str
            )
    return merged


def run_cmd(args, cwd=None, env=None, stream=False):
    run_env = with_cargo_bin_in_path(env)

    if not stream:
        proc = subprocess.run(
            args,
            cwd=cwd,
            env=run_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return proc.returncode, proc.stdout

    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=run_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    captured = []
    assert proc.stdout is not None
    for line in proc.stdout:
        captured.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()
    proc.stdout.close()
    return proc.wait(), "".join(captured)


def ensure_solana_cache_dir():
    cache_dir = pathlib.Path.home() / ".cache" / "solana"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def ensure_host_rust_toolchain():
    tool_path = with_cargo_bin_in_path().get("PATH", "")
    missing = [tool for tool in ("cargo", "rustc") if shutil.which(tool, path=tool_path) is None]
    if not missing:
        return True
    print(
        f"{Fore.RED}Missing host tools: {', '.join(missing)}. "
        "Install them first (e.g. apt: cargo rustc)."
        f"{Style.RESET_ALL}"
    )
    return False


def ensure_solana_release(solana_version: str):
    solana_dir = SOLANA_DIR / f"solana-release-{solana_version}"
    if solana_dir.exists():
        return True
    print(f"{Fore.BLUE}Solana version {solana_version} not found, installing...{Style.RESET_ALL}")
    code, _out = run_cmd(["bash", str(ROOT_DIR / "install-solana.sh"), solana_version], cwd=ROOT_DIR, stream=True)
    return code == 0 and solana_dir.exists()


def ensure_crate(crate: str, version: str):
    crate_dir = CRATES_DIR / f"{crate}-{version}"
    if crate_dir.exists():
        return True
    print(f"{Fore.BLUE}Crate {crate} version {version} not found, fetching...{Style.RESET_ALL}")
    code, _out = run_cmd(["bash", str(ROOT_DIR / "fetch-crate.sh"), crate, version], cwd=ROOT_DIR, stream=True)
    return code == 0 and crate_dir.exists()


def apply_ahash_patch(crate_dir: pathlib.Path):
    cargo_toml = crate_dir / "Cargo.toml"
    marker = "ahash = \"=0.8.6\""
    txt = cargo_toml.read_text()
    if marker in txt:
        return False
    txt += "\n[dependencies]\nahash = \"=0.8.6\"\n"
    cargo_toml.write_text(txt)
    return True


def apply_blake3_pin_patch(crate_dir: pathlib.Path):
    cargo_toml = crate_dir / "Cargo.toml"
    marker = "blake3 = \"=1.8.2\""
    txt = cargo_toml.read_text()
    if marker in txt:
        return False
    if "[patch.crates-io]" in txt:
        txt += "\nblake3 = \"=1.8.2\"\n"
    else:
        txt += "\n[patch.crates-io]\nblake3 = \"=1.8.2\"\n"
    cargo_toml.write_text(txt)
    return True


def drop_lockfile(crate_dir: pathlib.Path):
    lock_file = crate_dir / "Cargo.lock"
    if not lock_file.exists():
        return False
    lock_file.unlink()
    return True


def downgrade_lockfile_v4(crate_dir: pathlib.Path):
    lock_file = crate_dir / "Cargo.lock"
    if not lock_file.exists():
        return False
    txt = lock_file.read_text()
    if "version = 4" not in txt:
        return False
    lock_file.write_text(txt.replace("version = 4", "version = 3", 1))
    return True


def patch_blake3_lock(crate_dir: pathlib.Path):
    lock_file = crate_dir / "Cargo.lock"
    if not lock_file.exists():
        return False
    txt = lock_file.read_text()
    if BLAKE3_LOCK_V183 not in txt:
        return False
    lock_file.write_text(txt.replace(BLAKE3_LOCK_V183, BLAKE3_LOCK_V182))
    return True


def clean_target_for_arch(crate_dir: pathlib.Path):
    """Clean target directory to allow rebuilding with different arch."""
    target_dir = crate_dir / "target"
    if target_dir.exists():
        shutil.rmtree(target_dir)


def run_build(crate_dir: pathlib.Path, cargo_build_sbf: pathlib.Path, 
              tools_version: Optional[str] = None, sbf_arch: Optional[str] = None):
    rustc_env = os.environ.copy()
    rustc_env["RUSTFLAGS"] = "-C overflow-checks=on"
    cmd = [str(cargo_build_sbf)]
    if tools_version:
        cmd.extend(["--tools-version", tools_version])
    if sbf_arch:
        cmd.extend(["--arch", sbf_arch])
    return run_cmd(cmd, cwd=crate_dir, env=rustc_env, stream=True)


def get_sbf_archs_for_version(crate_version: str) -> List[str]:
    """Determine which sBPF architectures to build based on crate version."""
    try:
        major = int(crate_version.split(".")[0])
        if major >= 2:
            # Agave 2.x+ : only sbfv2 (v3)
            return ["sbfv2"]
        else:
            # Solana 1.x: only sbfv1 (v0)
            return ["sbfv1"]
    except (ValueError, IndexError):
        # Default to sbfv1 for unknown versions
        return ["sbfv1"]


def get_target_triple_for_arch(sbf_arch: str) -> str:
    """Map sbf arch to target triple directory name."""
    if sbf_arch == "sbfv2":
        return "sbpfv3-solana-solana"
    return "sbf-solana-solana"


def build_crate(crate: str, version: str, solana_version: str, 
                only_rlib=True, tools_version: Optional[str] = None,
                sbf_archs: Optional[List[str]] = None) -> Tuple[bool, str, List[Tuple[str, pathlib.Path]]]:
    """
    Build crate for specified sBPF architectures.
    
    Returns:
        (success, last_status, [(arch, rlib_path), ...])
    """
    del only_rlib
    if not ensure_host_rust_toolchain():
        return False, "missing host rust toolchain (cargo/rustc)", []
    ensure_solana_cache_dir()

    if not ensure_solana_release(solana_version):
        print(f"{Fore.RED}Failed to install solana version {solana_version}{Style.RESET_ALL}")
        return False, "", []
    if not ensure_crate(crate, version):
        print(f"{Fore.RED}Failed to fetch crate {crate} version {version}{Style.RESET_ALL}")
        return False, "", []

    solana_dir = SOLANA_DIR / f"solana-release-{solana_version}"
    crate_dir = CRATES_DIR / f"{crate}-{version}"
    cargo_build_sbf = (solana_dir / "bin" / "cargo-build-sbf").resolve()
    if not cargo_build_sbf.exists():
        print(f"{Fore.RED}cargo-build-sbf not found at {cargo_build_sbf}{Style.RESET_ALL}")
        return False, "", []

    # Determine architectures to build
    if sbf_archs is None:
        sbf_archs = get_sbf_archs_for_version(version)

    rlib_name = f"lib{crate.replace('-', '_')}.rlib"
    built_rlibs: List[Tuple[str, pathlib.Path]] = []
    last_status = ""

    for sbf_arch in sbf_archs:
        print(f"{Fore.BLUE}Building crate {crate} version {version} with toolchain {solana_version} "
              f"[arch={sbf_arch}]...{Style.RESET_ALL}")
        
        # Clean target to rebuild with new arch
        clean_target_for_arch(crate_dir)
        
        patched_ahash = False
        patched_blake3 = False
        patched_blake3_toml = False
        built = False

        for _attempt in range(1, 6):
            code, status = run_build(crate_dir, cargo_build_sbf, 
                                     tools_version=tools_version, sbf_arch=sbf_arch)
            last_status = status
            if code == 0:
                print(f"{Fore.GREEN}Crate {crate} version {version} [{sbf_arch}] built successfully!{Style.RESET_ALL}")
                built = True
                break

            if (not patched_ahash) and AHASH_HINT in status:
                print(f"{Fore.YELLOW}[compat] applying ahash pin...{Style.RESET_ALL}")
                patched_ahash = apply_ahash_patch(crate_dir)
                if patched_ahash:
                    continue

            if LOCKFILE_V4_HINT in status:
                print(f"{Fore.YELLOW}[compat] downgrading Cargo.lock version 4 -> 3...{Style.RESET_ALL}")
                if downgrade_lockfile_v4(crate_dir):
                    continue
                print(f"{Fore.YELLOW}[compat] dropping Cargo.lock v4...{Style.RESET_ALL}")
                if drop_lockfile(crate_dir):
                    continue

            if (not patched_blake3) and any(h in status for h in EDITION_2024_HINTS):
                print(f"{Fore.YELLOW}[compat] patching blake3 lock entry 1.8.3 -> 1.8.2...{Style.RESET_ALL}")
                patched_blake3 = patch_blake3_lock(crate_dir)
                if patched_blake3:
                    continue
                if not patched_blake3_toml:
                    print(f"{Fore.YELLOW}[compat] pinning blake3 in Cargo.toml to 1.8.2...{Style.RESET_ALL}")
                    patched_blake3_toml = apply_blake3_pin_patch(crate_dir)
                    if patched_blake3_toml:
                        continue

            break

        if not built:
            print(f"{Fore.RED}Crate {crate} version {version} [{sbf_arch}] build failed!{Style.RESET_ALL}")
            continue

        # Find the rlib
        target_triple = get_target_triple_for_arch(sbf_arch)
        rlib_path = crate_dir / "target" / target_triple / "release" / rlib_name
        if rlib_path.exists():
            built_rlibs.append((sbf_arch, rlib_path))
        else:
            print(f"{Fore.RED}Rlib for {crate}:{version} [{sbf_arch}] not found at {rlib_path}{Style.RESET_ALL}")

    success = len(built_rlibs) > 0
    return success, last_status, built_rlibs


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--solana-version", type=str, required=True, help="Compiler toolchain Solana version")
    parser.add_argument("--arch", type=str, choices=["sbfv1", "sbfv2", "both"], default=None,
                        help="sBPF architecture: sbfv1 (v0), sbfv2 (v3), or both")
    parser.add_argument("crate", type=str, help="Crate name")
    parser.add_argument("version", type=str, help="Crate version")
    args = parser.parse_args()

    sbf_archs = None
    if args.arch == "both":
        sbf_archs = ["sbfv1", "sbfv2"]
    elif args.arch:
        sbf_archs = [args.arch]

    ok, _, rlibs = build_crate(args.crate, args.version, args.solana_version, sbf_archs=sbf_archs)
    if rlibs:
        print(f"Built rlibs: {[(arch, str(p)) for arch, p in rlibs]}")
    raise SystemExit(0 if ok else 1)
