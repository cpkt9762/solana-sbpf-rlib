# solana-sbpf-rlib

Pre-compiled Solana sBPF `.rlib` files for IDA Pro / Ghidra / Binary Ninja signature generation.

## Downloads

Download from [Releases](https://github.com/cpkt9762/solana-sbpf-rlib/releases):

### Option 1: Bundle Packages (Recommended)

| Package | Size | Description |
|---------|------|-------------|
| **core** | 275MB | Essential: solana-program, account, instruction, pubkey, sysvar, etc. |
| **crypto** | 280MB | Cryptographic: zk-sdk, curve25519, bn254, merkle-tree, poseidon, etc. |
| **anchor** | 144MB | Anchor framework: anchor-lang, anchor-spl, anchor-syn, etc. |
| **extra** | 50MB | Other dependencies: frozen-abi, nonce-account, bincode, etc. |

### Option 2: Individual Crate Packages

Download only what you need - 139 individual crate packages available.

[View all individual packages →](https://github.com/cpkt9762/solana-sbpf-rlib/releases)

## Quick Start

```bash
# Install zstd if needed
brew install zstd  # macOS
apt install zstd   # Ubuntu

# Download and extract
curl -LO https://github.com/cpkt9762/solana-sbpf-rlib/releases/download/v1.0.0/solana-sbpf-rlib-core.tar.zst
tar -xf solana-sbpf-rlib-core.tar.zst
```

## sBPF Architecture Versions

Solana programs can be compiled for different sBPF bytecode versions:

| Architecture | Bytecode | Solana Version | Description |
|--------------|----------|----------------|-------------|
| **sbfv1** | v0 | 1.x (1.4 - 1.18) | Legacy format, solana-labs/solana |
| **sbfv3** | v3 | 2.x+ (Agave) | New format, anza-xyz/agave |

### File Naming Convention

```
lib{crate}-{version}-{sbf_arch}-{tools}.rlib

Examples:
libsolana_program-1.17.0-sbfv1-v1_48.rlib  # Solana 1.x, sBPF v0
libsolana_program-2.0.0-sbfv3-v1_48.rlib   # Agave 2.x, sBPF v3
```

## Version Coverage

| Project | Versions | sBPF Arch |
|---------|----------|-----------|
| **Solana** (solana-labs/solana) | 1.17.x - 1.18.x | sbfv1 |
| **Agave** (anza-xyz/agave) | 2.0.x - 4.0.x | sbfv3 |
| **Anchor** | 0.28.x - 1.0.0-rc | auto |
| **Platform Tools** | v1.43+ | - |

> **Note**: Solana development has moved from `solana-labs/solana` to `anza-xyz/agave`. 
> Versions 2.x+ come from the Agave repository and use sBPF v3 (sbfv3).

## Building from Source

```bash
# Requirements: Solana CLI, Rust, cargo-build-sbf

# Build with auto-detected architecture (recommended)
./build-rlibs-from-index.sh \
  --solana-version 1.18.16 \
  --compiler-solana-version 1.18.16 \
  --fallback-compiler-solana-version 1.17.0 \
  --platform-tools-version v1.43

# Build specific architecture
python3 get-rlibs-from-crate.py \
  --crate solana-program \
  --version 1.18.0 \
  --solana-version 1.18.16 \
  --compiler-solana-version 1.18.16 \
  --fallback-compiler-solana-version 1.17.0 \
  --platform-tools-version v1.48 \
  --sbf-arch sbfv1  # or sbfv3, both, auto
```

### Build Options

| Option | Values | Description |
|--------|--------|-------------|
| `--sbf-arch` | `sbfv1`, `sbfv3`, `both`, `auto` | sBPF architecture to build |

- `auto` (default): sbfv1 for 1.x, sbfv3 for 2.x+
- `both`: Build both architectures for all versions

## Use Cases

- **IDA Pro**: FLIRT signature generation
- **Ghidra**: Function identification  
- **Binary Ninja**: Signature matching
- **General**: Solana program reverse engineering

## Directory Structure

```
solana-program/
├── libsolana_program-1.17.0-sbfv1-v1_48.rlib
├── libsolana_program-1.18.0-sbfv1-v1_48.rlib
├── libsolana_program-2.0.0-sbfv3-v1_48.rlib
├── ...
└── deps/
    └── (dependency rlibs with same naming)
```

## License

MIT
