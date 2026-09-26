# solana-sbpf-rlib

Pre-compiled Solana sBPF `.rlib` files for IDA Pro / Ghidra / Binary Ninja / radare2 signature generation.

## Downloads

Download from [Releases](https://github.com/cpkt9762/solana-sbpf-rlib/releases):

- **v3.0.0** (latest): 74 per-crate packages, one `<crate>.tar.zst` per crate, including `pinocchio`.
- **v2.0.0**: the same packages except `pinocchio`, plus four bundles made by `pack-releases.sh`:

| Bundle | Size | Contents |
|--------|------|----------|
| `solana-sbpf-rlib-core.tar.zst` | 293 MB | solana-program and its core micro-crates |
| `solana-sbpf-rlib-crypto.tar.zst` | 70 MB | zk-sdk, curve25519, bn254, poseidon, hashers, etc. |
| `solana-sbpf-rlib-anchor.tar.zst` | 0.3 MB | anchor-lang 0.30.1 only |
| `solana-sbpf-rlib-extra.tar.zst` | 351 MB | everything else: spl-*, bincode, borsh, bytemuck, thiserror, etc. |

## Quick Start

```bash
brew install zstd   # or: apt install zstd
curl -LO https://github.com/cpkt9762/solana-sbpf-rlib/releases/download/v3.0.0/solana-program.tar.zst
zstd -dc solana-program.tar.zst | tar -xf -
```

Every package extracts to a directory named after the crate:

```
solana-sanitize/
├── libsolana_sanitize-0.0.1-sbpfv0-s1_18-pt1_41.rlib
├── libsolana_sanitize-0.0.2-sbpfv0-s1_18-pt1_41.rlib
└── deps/    # dependency rlibs, same naming
```

## File Naming

```
lib{crate}-{version}-{arch}-{sol}-pt{pt}.rlib

libsolana_sanitize-0.0.2-sbpfv0-s1_18-pt1_41.rlib
libpinocchio-0.10.1-sbpfv3-s2_3-pt1_48.rlib
```

| Field | Meaning |
|-------|---------|
| `crate` | Crate name with `-` replaced by `_` |
| `version` | Crate version |
| `arch` | `sbpfv0` or `sbpfv3`; not always the real sBPF version, see below |
| `sol` | Solana release whose `cargo-build-sbf` built the file, as `s{major}_{minor}` |
| `pt` | platform-tools version, as `pt{major}_{minor}` |

When a file in `deps/` has no resolvable version, cargo's hash takes the version's place: `lib{crate}-{hash}-{arch}-{sol}-pt{pt}.rlib`.

### What `arch` really means

The label comes from the build flag, not from the output. The ELF `e_flags` of the object files in the release say:

| Groups | Label | `e_flags` | Actual sBPF version |
|--------|-------|-----------|---------------------|
| `s1_16`, `s1_18` | `sbpfv0` | `0` | SBPFv0 |
| `s2_0`, `s2_1` | `sbpfv3` | `0x20` | legacy `sbfv2` flag, not SBPFv3 |
| `s2_2`, `s2_3` | `sbpfv3` | `2` | SBPFv2 |

Read `e_flags` from the `.o` members of an rlib when the exact version matters.

## Toolchain Groups in the Release

| Group | Solana | platform-tools | Crates built with it |
|-------|--------|----------------|----------------------|
| `sbpfv0-s1_18-pt1_41` | 1.18.26 | v1.41 | solana-* versions below 2.0, third-party crates, pinocchio |
| `sbpfv3-s2_1-pt1_43` | 2.1.21 | v1.43 | solana-* 2.1.x and 3.x to 5.x, third-party crates, pinocchio |
| `sbpfv3-s2_2-pt1_48` | 2.2.1 | v1.48 | pinocchio |
| `sbpfv3-s2_3-pt1_48` | 2.3.13 | v1.48 | pinocchio |

Two files come from other toolchains: `libsolana_program-1.16.27-sbpfv0-s1_16-pt1_37.rlib` and `libsolana_frozen_abi-2.0.23-sbpfv3-s2_0-pt1_42.rlib`. The release has no solana-* 2.2.x or 2.3.x builds.

## Building from Source

Requires Python 3, Rust (`cargo`, `rustc`), `curl`, `wget` and `zstd`. Solana releases are downloaded into `solana/` on first use. `gen_sdb.py` also needs `ar` and radare2 with the `sbpf` arch.

```bash
python3 build-rlibs-latest.py --scope solana --workers 8    # whitelisted crates, all versions
./build-rlibs-from-index.sh --scope solana --include '^solana-program$'
python3 get-rlibs-from-crate.py --crate pinocchio --version 0.10.1 --extract-deps
python3 gen_sdb.py --groups sbpfv3-s2_2-pt1_48              # radare2 SDB signatures into sdb/
./pack-individual.sh && ./pack-releases.sh                  # tarballs into releases/
```

- `build-rlibs-latest.py` fetches version lists from crates.io, refreshing `versions/`, and always extracts `deps/`.
- `build-rlibs-from-index.sh` uses only the crate lists and versions already in `versions/`.
- Output rlibs go to `rlibs/<crate>/`.

`build_crate.py` picks the toolchain for every crate version:

- `solana-*` crates use the Solana release that matches the crate's major.minor, from 1.9 to 2.3 (`SOLANA_TOOLCHAIN_MAP`). Versions outside the table fall back to a nearby entry; for example 0.0.x uses 1.18 and 3.x or later uses 2.1 (`get_toolchain_for_crate()`).
- Other crates, including spl-*, anchor-* and pinocchio, are built once per `THIRD_PARTY_TOOLCHAINS` entry: 1.18.26/v1.41, 2.2.1/v1.48 and 2.3.13/v1.48.

Except for pinocchio, the published third-party builds came from an earlier revision that used 1.18.26/v1.41 and 2.1.21/v1.43, so rebuilding them now produces `s2_2`/`s2_3` files instead of `s2_1`.

## Use Cases

- **IDA Pro**: FLIRT signature generation
- **Ghidra**: Function identification
- **Binary Ninja**: Signature matching
- **radare2**: SDB signatures from `gen_sdb.py`
- **General**: Solana program reverse engineering

## License

MIT
