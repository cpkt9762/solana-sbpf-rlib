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
ZMIJ_HINT = "package `zmij"
MSRV_HINT = "requires rustc"  # generic: "package `X vY.Z` cannot be built because it requires rustc A.B"
ZMIJ_REQUIRES_HINT = "requires rustc"

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

# ---------------------------------------------------------------------------
# Toolchain routing
# ---------------------------------------------------------------------------

# Maps Solana crate's major.minor version → (solana_release, platform_tools_tag, short_tag)
# short_tag is used in rlib file names: s{major}_{minor}
SOLANA_TOOLCHAIN_MAP: dict = {
    "1.9": ("1.9.28", "v1.25", "s1_9"),
    "1.10": ("1.10.38", "v1.27", "s1_10"),
    "1.11": ("1.11.10", "v1.29", "s1_11"),
    "1.13": ("1.13.7", "v1.27", "s1_13"),
    "1.14": ("1.14.29", "v1.29", "s1_14"),
    "1.15": ("1.15.2", "v1.32", "s1_15"),
    "1.16": ("1.16.27", "v1.37", "s1_16"),
    "1.17": ("1.17.34", "v1.37", "s1_17"),
    "1.18": ("1.18.26", "v1.41", "s1_18"),
    "2.0": ("2.0.21", "v1.42", "s2_0"),
    "2.1": ("2.1.21", "v1.43", "s2_1"),
    "2.2": ("2.2.1", "v1.48", "s2_2"),
    "2.3": ("2.3.13", "v1.48", "s2_3"),
}

# Prefixes that indicate a first-party Solana crate (toolchain routing by version)
SOLANA_CRATE_PREFIXES = ("solana-",)

# Third-party crates are compiled with BOTH toolchains (one sbpfv0, one sbpfv3)
THIRD_PARTY_TOOLCHAINS: List[Tuple[str, str, str, List[str]]] = [
    ("1.18.26", "v1.41", "s1_18", ["sbfv1"]),  # sbpfv0
    ("2.2.1", "v1.48", "s2_2", ["sbfv2"]),  # sbpfv3 (Solana 2.2, platform-tools v1.48)
    ("2.3.13", "v1.48", "s2_3", ["sbfv2"]),  # sbpfv3 (Solana 2.3, platform-tools v1.48)
]


def get_toolchain_for_crate(
    crate: str, version: str
) -> List[Tuple[str, str, str, List[str]]]:
    """
    Returns a list of (solana_version, platform_tools, short_tag, sbf_archs) tuples.

    - Solana/SPL crates: single entry matched by major.minor version.
    - Third-party crates: two entries (sbpfv0 + sbpfv3).
    """
    if any(crate.startswith(p) for p in SOLANA_CRATE_PREFIXES):
        major_minor = ".".join(version.split(".")[:2])
        entry = SOLANA_TOOLCHAIN_MAP.get(major_minor)
        if entry:
            sol_ver, pt_ver, tag = entry
            major = int(version.split(".")[0]) if version.split(".")[0].isdigit() else 1
            archs = ["sbfv2"] if major >= 2 else ["sbfv1"]
            return [(sol_ver, pt_ver, tag, archs)]
        # Fallback: use the closest available toolchain
        # (e.g. 1.12.x not in table → use 1.11 entry)
        try:
            major = int(version.split(".")[0])
            minor = int(version.split(".")[1])
        except (IndexError, ValueError):
            major, minor = 1, 18
        if major >= 2:
            fallback_key = f"{major}.{minor}"
            # Walk down minor until we find a match
            while minor >= 0:
                fallback_key = f"{major}.{minor}"
                if fallback_key in SOLANA_TOOLCHAIN_MAP:
                    sol_ver, pt_ver, tag = SOLANA_TOOLCHAIN_MAP[fallback_key]
                    return [(sol_ver, pt_ver, tag, ["sbfv2"])]
                minor -= 1
            # Last resort
            sol_ver, pt_ver, tag = SOLANA_TOOLCHAIN_MAP["2.1"]
            return [(sol_ver, pt_ver, tag, ["sbfv2"])]
        else:
            while minor >= 9:
                fallback_key = f"1.{minor}"
                if fallback_key in SOLANA_TOOLCHAIN_MAP:
                    sol_ver, pt_ver, tag = SOLANA_TOOLCHAIN_MAP[fallback_key]
                    return [(sol_ver, pt_ver, tag, ["sbfv1"])]
                minor -= 1
            sol_ver, pt_ver, tag = SOLANA_TOOLCHAIN_MAP["1.18"]
            return [(sol_ver, pt_ver, tag, ["sbfv1"])]

    # Third-party crate: build with both toolchains
    return list(THIRD_PARTY_TOOLCHAINS)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def with_cargo_bin_in_path(env=None):
    merged = dict(os.environ if env is None else env)
    cargo_bin = pathlib.Path.home() / ".cargo" / "bin"
    if cargo_bin.is_dir():
        cargo_bin_str = str(cargo_bin)
        current_path = merged.get("PATH", "")
        paths = current_path.split(os.pathsep) if current_path else []
        if cargo_bin_str not in paths:
            merged["PATH"] = (
                f"{cargo_bin_str}{os.pathsep}{current_path}"
                if current_path
                else cargo_bin_str
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
    missing = [
        tool
        for tool in ("cargo", "rustc")
        if shutil.which(tool, path=tool_path) is None
    ]
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
    print(
        f"{Fore.BLUE}Solana version {solana_version} not found, installing...{Style.RESET_ALL}"
    )
    code, _out = run_cmd(
        ["bash", str(ROOT_DIR / "install-solana.sh"), solana_version],
        cwd=ROOT_DIR,
        stream=True,
    )
    return code == 0 and solana_dir.exists()


def ensure_crate(crate: str, version: str):
    crate_dir = CRATES_DIR / f"{crate}-{version}"
    if crate_dir.exists():
        return True
    print(
        f"{Fore.BLUE}Crate {crate} version {version} not found, fetching...{Style.RESET_ALL}"
    )
    code, _out = run_cmd(
        ["bash", str(ROOT_DIR / "fetch-crate.sh"), crate, version],
        cwd=ROOT_DIR,
        stream=True,
    )
    return code == 0 and crate_dir.exists()


def apply_ahash_patch(crate_dir: pathlib.Path):
    cargo_toml = crate_dir / "Cargo.toml"
    marker = 'ahash = "=0.8.6"'
    txt = cargo_toml.read_text()
    if marker in txt:
        return False
    txt += '\n[dependencies]\nahash = "=0.8.6"\n'
    cargo_toml.write_text(txt)
    return True


def parse_msrv_error(status: str):
    """
    Parse MSRV errors. Two patterns supported:
    1. "package `X vY` cannot be built because it requires rustc A.B"
    2. "failed to parse manifest at .../CRATE-VERSION/Cargo.toml ... this version of Cargo
       is older than the `2024` edition" - these require downgrading the crate.
    Returns list of (crate_name, failing_ver, required_rustc, current_rustc)
    """
    import re

    results = []
    # Pattern 1: explicit MSRV check
    pat1 = re.compile(
        r"package `([a-z0-9_-]+) v([\d.]+)` cannot be built because it requires rustc "
        r"([\d.]+) or newer, while the currently active rustc version is ([\d.]+)"
    )
    for m in pat1.finditer(status):
        results.append((m.group(1), m.group(2), m.group(3), m.group(4)))

    # Pattern 2: agave 2.x format "rustc X.Y.Z-dev is not supported by... NAME@VER requires rustc A.B"
    pat2 = re.compile(
        r"rustc ([\d.]+)-dev is not supported.*?([a-z0-9_-]+)@([\d.]+) requires rustc ([\d.]+)",
        re.DOTALL,
    )
    for m in pat2.finditer(status):
        current_rustc = m.group(1)
        crate_name = m.group(2)
        fail_ver = m.group(3)
        if not any(r[0] == crate_name and r[1] == fail_ver for r in results):
            results.append((crate_name, fail_ver, m.group(4), current_rustc))

    # Pattern 3: edition 2024 parse error (rustc too old for edition 2024 crates)
    edition_hint = "older than the `2024` edition"
    if edition_hint in status:
        current_rustc_m = re.search(
            r"currently active rustc version is ([\d.]+)", status
        )
        current_rustc = current_rustc_m.group(1) if current_rustc_m else "1.68"
        # Match paths like: /path/to/crate-name-1.2.3/Cargo.toml
        pat3 = re.compile(r"/([a-z0-9_-]+)-([\d.]+)/Cargo\.toml")
        for m in pat3.finditer(status):
            crate_name, fail_ver = m.group(1), m.group(2)
            if not any(r[0] == crate_name and r[1] == fail_ver for r in results):
                results.append((crate_name, fail_ver, "1.85", current_rustc))
    return results


def find_msrv_compatible_version(crate_name: str, max_rustc_str: str) -> Optional[str]:
    """
    Query crates.io to find the newest version of `crate_name` whose MSRV <= max_rustc.
    """
    import urllib.request, json, time

    try:
        max_parts = tuple(int(x) for x in max_rustc_str.split(".")[:2])
    except ValueError:
        return None

    url = f"https://crates.io/api/v1/crates/{crate_name}/versions"
    headers = {"User-Agent": "solana-rlib-compat/1.0"}
    try:
        req = urllib.request.Request(url, headers=headers)
        data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        versions = data.get("versions", [])
    except Exception as e:
        print(f"  [msrv] crates.io query failed for {crate_name}: {e}")
        return None

    for item in versions:
        if item.get("yanked"):
            continue
        rv = item.get("rust_version") or ""
        num = item.get("num", "")
        if not num:
            continue
        if not rv:
            # No MSRV declared — assume compatible
            return num
        try:
            rv_parts = tuple(int(x) for x in rv.split(".")[:2])
        except ValueError:
            return num
        if rv_parts <= max_parts:
            return num
    return None


def apply_msrv_pins(crate_dir: pathlib.Path, status: str) -> bool:
    """
    For each MSRV error in status, run `cargo update -p crate@fail_ver --precise compat_ver`
    to downgrade the problematic dependency in Cargo.lock.
    Returns True if any update was applied.
    """
    import subprocess as _sp

    errors = parse_msrv_error(status)
    if not errors:
        return False

    patched = False
    for crate_name, fail_ver, _req_rustc, current_rustc in errors:
        compat_ver = find_msrv_compatible_version(crate_name, current_rustc)
        if not compat_ver:
            print(f"  [msrv] no compatible version found for {crate_name}")
            continue
        if compat_ver == fail_ver:
            continue
        print(
            f"  [msrv] cargo update {crate_name} {fail_ver} -> {compat_ver} (rustc {current_rustc})"
        )
        update_env = with_cargo_bin_in_path()
        cargo_bin = shutil.which("cargo", path=update_env.get("PATH", "")) or "cargo"
        proc = _sp.run(
            [
                cargo_bin,
                "update",
                "-p",
                f"{crate_name}@{fail_ver}",
                "--precise",
                compat_ver,
            ],
            cwd=str(crate_dir),
            env=update_env,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            patched = True
        else:
            print(f"  [msrv] cargo update failed: {proc.stderr.strip()[:200]}")
    return patched


def apply_zmij_pin_patch(crate_dir: pathlib.Path):
    """Use cargo update --precise to downgrade zmij to a version compatible with rustc 1.68."""
    import subprocess as _sp

    # Find the current zmij version from Cargo.lock
    lock = crate_dir / "Cargo.lock"
    fail_ver = "1.0.21"  # known problematic version
    if lock.exists():
        import re

        m = re.search(r'name = "zmij"\nversion = "([^"]+)"', lock.read_text())
        if m:
            fail_ver = m.group(1)
    compat_ver = "1.0.19"
    update_env = with_cargo_bin_in_path()
    cargo_bin = shutil.which("cargo", path=update_env.get("PATH", "")) or "cargo"
    proc = _sp.run(
        [cargo_bin, "update", "-p", f"zmij@{fail_ver}", "--precise", compat_ver],
        cwd=str(crate_dir),
        env=update_env,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def apply_blake3_pin_patch(crate_dir: pathlib.Path):
    cargo_toml = crate_dir / "Cargo.toml"
    marker = 'blake3 = "=1.8.2"'
    txt = cargo_toml.read_text()
    if marker in txt:
        return False
    if "[patch.crates-io]" in txt:
        txt += '\nblake3 = "=1.8.2"\n'
    else:
        txt += '\n[patch.crates-io]\nblake3 = "=1.8.2"\n'
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


def run_build(
    crate_dir: pathlib.Path,
    cargo_build_sbf: pathlib.Path,
    tools_version: Optional[str] = None,
    sbf_arch: Optional[str] = None,
    collect_artifacts: bool = False,
) -> Tuple[int, str, dict]:
    """
    Build the crate.

    Returns (returncode, combined_output, hash_to_version).
    hash_to_version maps 16-hex-char hash -> (crate_name, version)
    when collect_artifacts=True; otherwise returns empty dict.
    """
    import json as _json, threading, re

    rustc_env = os.environ.copy()
    rustc_env["RUSTFLAGS"] = "-C overflow-checks=on"
    cmd = [str(cargo_build_sbf)]
    if tools_version:
        cmd.extend(["--tools-version", tools_version])
    if sbf_arch:
        cmd.extend(["--arch", sbf_arch])

    if not collect_artifacts:
        code, out = run_cmd(cmd, cwd=crate_dir, env=rustc_env, stream=True)
        return code, out, {}

    _DEP_HASH_RE = re.compile(r"lib(.+)-([0-9a-f]{16})(?:\.rlib|\.rmeta)$")
    json_cmd = cmd + ["--", "--message-format=json-render-diagnostics"]
    run_env = with_cargo_bin_in_path(rustc_env)

    proc = subprocess.Popen(
        json_cmd,
        cwd=crate_dir,
        env=run_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )

    captured_out: list = []
    captured_err: list = []
    hash_to_version: dict = {}

    def _read_stderr():
        assert proc.stderr is not None
        for line in proc.stderr:
            captured_err.append(line)
            sys.stdout.write(line)
            sys.stdout.flush()
        proc.stderr.close()

    t = threading.Thread(target=_read_stderr, daemon=True)
    t.start()

    assert proc.stdout is not None
    for line in proc.stdout:
        captured_out.append(line)
        stripped = line.strip()
        if not stripped.startswith("{"):
            # Non-JSON lines (e.g. progress messages) go to stdout
            sys.stdout.write(line)
            sys.stdout.flush()
            continue
        try:
            msg = _json.loads(stripped)
        except _json.JSONDecodeError:
            continue
        if msg.get("reason") != "compiler-artifact":
            continue
        pkg_id = msg.get("package_id", "")
        pid_m = re.match(r"^([a-zA-Z0-9_-]+)\s+([\S]+)", pkg_id)
        if not pid_m:
            continue
        crate_name = pid_m.group(1).replace("-", "_")
        pkg_version = pid_m.group(2)
        for fname in msg.get("filenames", []):
            basename = pathlib.Path(fname).name
            m2 = _DEP_HASH_RE.match(basename)
            if m2:
                hash_to_version[m2.group(2)] = (crate_name, pkg_version)

    proc.stdout.close()
    t.join()
    code = proc.wait()
    combined = "".join(captured_out) + "".join(captured_err)
    return code, combined, hash_to_version


def get_sbf_archs_for_version(crate_version: str) -> List[str]:
    """Determine which sBPF architectures to build based on crate version."""
    try:
        major = int(crate_version.split(".")[0])
        if major >= 2:
            return ["sbfv2"]
        else:
            return ["sbfv1"]
    except (ValueError, IndexError):
        return ["sbfv1"]


def get_target_triple_for_arch(sbf_arch: str, solana_version: str = "") -> str:
    """Map sbf arch + Solana version to target triple directory name.
    Solana >= 2.2 uses sbpfvN-solana-solana; older versions use sbf-solana-solana."""
    try:
        major, minor = (
            int(solana_version.split(".")[0]),
            int(solana_version.split(".")[1]),
        )
    except (ValueError, IndexError):
        return "sbf-solana-solana"
    if (major, minor) >= (2, 2):
        new_arch = normalize_arch_for_toolchain(sbf_arch, solana_version)
        return f"sbpf{new_arch}-solana-solana"
    return "sbf-solana-solana"


def normalize_arch_for_toolchain(sbf_arch: str, solana_version: str) -> str:
    """
    Normalize the arch string for the given Solana toolchain version.
    Solana <= 2.1: accepts 'sbfv1', 'sbfv2'
    Solana >= 2.2: accepts 'v0', 'v1', 'v2', 'v3'
    """
    try:
        major, minor = (
            int(solana_version.split(".")[0]),
            int(solana_version.split(".")[1]),
        )
    except (ValueError, IndexError):
        return sbf_arch
    if (major, minor) >= (2, 2):
        mapping = {"sbfv1": "v1", "sbfv2": "v2", "sbfv3": "v3"}
        return mapping.get(sbf_arch, sbf_arch)
    return sbf_arch


def build_crate(
    crate: str,
    version: str,
    solana_version: str,
    only_rlib=True,
    tools_version: Optional[str] = None,
    sbf_archs: Optional[List[str]] = None,
) -> Tuple[bool, str, List[Tuple[str, pathlib.Path]], dict]:
    """
    Build crate for specified sBPF architectures.

    Returns:
        (success, last_status, [(arch, rlib_path), ...])
    """
    del only_rlib
    if not ensure_host_rust_toolchain():
        return False, "missing host rust toolchain (cargo/rustc)", [], {}
    ensure_solana_cache_dir()

    if not ensure_solana_release(solana_version):
        print(
            f"{Fore.RED}Failed to install solana version {solana_version}{Style.RESET_ALL}"
        )
        return False, "", [], {}
    if not ensure_crate(crate, version):
        print(
            f"{Fore.RED}Failed to fetch crate {crate} version {version}{Style.RESET_ALL}"
        )
        return False, "", [], {}

    solana_dir = SOLANA_DIR / f"solana-release-{solana_version}"
    crate_dir = CRATES_DIR / f"{crate}-{version}"
    cargo_build_sbf = (solana_dir / "bin" / "cargo-build-sbf").resolve()
    if not cargo_build_sbf.exists():
        print(
            f"{Fore.RED}cargo-build-sbf not found at {cargo_build_sbf}{Style.RESET_ALL}"
        )
        return False, "", [], {}

    if sbf_archs is None:
        sbf_archs = get_sbf_archs_for_version(version)

    rlib_name = f"lib{crate.replace('-', '_')}.rlib"
    built_rlibs: List[Tuple[str, pathlib.Path]] = []
    accumulated_hash_map: dict = {}
    last_status = ""

    for sbf_arch in sbf_archs:
        print(
            f"{Fore.BLUE}Building crate {crate} version {version} with toolchain {solana_version} "
            f"[arch={sbf_arch}]...{Style.RESET_ALL}"
        )

        clean_target_for_arch(crate_dir)

        patched_ahash = False
        patched_blake3 = False
        patched_blake3_toml = False
        built = False

        actual_arch = normalize_arch_for_toolchain(sbf_arch, solana_version)
        for _attempt in range(1, 6):
            code, status, attempt_hashes = run_build(
                crate_dir,
                cargo_build_sbf,
                tools_version=tools_version,
                sbf_arch=actual_arch,
                collect_artifacts=True,
            )
            accumulated_hash_map.update(attempt_hashes)
            last_status = status
            if code == 0:
                print(
                    f"{Fore.GREEN}Crate {crate} version {version} [{sbf_arch}] built successfully!{Style.RESET_ALL}"
                )
                built = True
                break

            if (not patched_ahash) and AHASH_HINT in status:
                print(f"{Fore.YELLOW}[compat] applying ahash pin...{Style.RESET_ALL}")
                patched_ahash = apply_ahash_patch(crate_dir)
                if patched_ahash:
                    continue

            if ZMIJ_HINT in status and ZMIJ_REQUIRES_HINT in status:
                print(
                    f"{Fore.YELLOW}[compat] pinning zmij to 1.0.19...{Style.RESET_ALL}"
                )
                if apply_zmij_pin_patch(crate_dir):
                    continue  # Cargo.lock already updated by cargo update --precise

            if MSRV_HINT in status:
                print(
                    f"{Fore.YELLOW}[compat] auto-pinning MSRV-incompatible deps...{Style.RESET_ALL}"
                )
                if apply_msrv_pins(crate_dir, status):
                    continue  # Cargo.lock already updated by cargo update --precise

            if LOCKFILE_V4_HINT in status:
                print(
                    f"{Fore.YELLOW}[compat] downgrading Cargo.lock version 4 -> 3...{Style.RESET_ALL}"
                )
                if downgrade_lockfile_v4(crate_dir):
                    continue
                print(
                    f"{Fore.YELLOW}[compat] dropping Cargo.lock v4...{Style.RESET_ALL}"
                )
                if drop_lockfile(crate_dir):
                    continue

            if (not patched_blake3) and any(h in status for h in EDITION_2024_HINTS):
                print(
                    f"{Fore.YELLOW}[compat] patching blake3 lock entry 1.8.3 -> 1.8.2...{Style.RESET_ALL}"
                )
                patched_blake3 = patch_blake3_lock(crate_dir)
                if patched_blake3:
                    continue
                if not patched_blake3_toml:
                    print(
                        f"{Fore.YELLOW}[compat] pinning blake3 in Cargo.toml to 1.8.2...{Style.RESET_ALL}"
                    )
                    patched_blake3_toml = apply_blake3_pin_patch(crate_dir)
                    if patched_blake3_toml:
                        continue

            break

        if not built:
            print(
                f"{Fore.RED}Crate {crate} version {version} [{sbf_arch}] build failed!{Style.RESET_ALL}"
            )
            continue

        target_triple = get_target_triple_for_arch(sbf_arch, solana_version)
        rlib_path = crate_dir / "target" / target_triple / "release" / rlib_name
        if rlib_path.exists():
            built_rlibs.append((sbf_arch, rlib_path))
        else:
            print(
                f"{Fore.RED}Rlib for {crate}:{version} [{sbf_arch}] not found at {rlib_path}{Style.RESET_ALL}"
            )

    success = len(built_rlibs) > 0
    return success, last_status, built_rlibs, accumulated_hash_map


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--solana-version",
        type=str,
        required=True,
        help="Compiler toolchain Solana version",
    )
    parser.add_argument(
        "--arch",
        type=str,
        choices=["sbfv1", "sbfv2", "both"],
        default=None,
        help="sBPF architecture: sbfv1 (v0), sbfv2 (v3), or both",
    )
    parser.add_argument("crate", type=str, help="Crate name")
    parser.add_argument("version", type=str, help="Crate version")
    args = parser.parse_args()

    sbf_archs = None
    if args.arch == "both":
        sbf_archs = ["sbfv1", "sbfv2"]
    elif args.arch:
        sbf_archs = [args.arch]

    ok, _, rlibs, _hm = build_crate(
        args.crate, args.version, args.solana_version, sbf_archs=sbf_archs
    )
    if rlibs:
        print(f"Built rlibs: {[(arch, str(p)) for arch, p in rlibs]}")
    raise SystemExit(0 if ok else 1)
