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

**Top crates by size:**
| Crate | Size | Description |
|-------|------|-------------|
| solana-program | ~130MB | Core program library |
| solana-merkle-tree | ~95MB | Merkle tree implementation |
| solana-zk-sdk | ~75MB | Zero-knowledge SDK |
| solana-curve25519 | ~65MB | Curve25519 crypto |
| anchor-syn | ~50MB | Anchor syntax parsing |
| anchor-spl | ~40MB | Anchor SPL integration |

[View all individual packages →](https://github.com/cpkt9762/solana-sbpf-rlib/releases)

## Quick Start

```bash
# Install zstd if needed
brew install zstd  # macOS
apt install zstd   # Ubuntu

# Download bundle
curl -LO https://github.com/cpkt9762/solana-sbpf-rlib/releases/download/v1.0.0/solana-sbpf-rlib-core.tar.zst
tar -xf solana-sbpf-rlib-core.tar.zst

# Or download individual crate
curl -LO https://github.com/cpkt9762/solana-sbpf-rlib/releases/download/v1.0.0/solana-program.tar.zst
tar -xf solana-program.tar.zst
```

## Version Coverage

| Project | Versions |
|---------|----------|
| **Solana** (solana-labs/solana) | 1.17.x - 1.18.x |
| **Agave** (anza-xyz/agave) | 2.0.x - 4.0.x |
| **Anchor** | 0.28.x - 1.0.0-rc |
| **Platform Tools** | v1.43+ |

> **Note**: Solana development has moved from `solana-labs/solana` to `anza-xyz/agave`. 
> Versions 2.x+ come from the Agave repository.

## Building from Source

```bash
# Requirements: Solana CLI, Rust, cargo-build-sbf

./build-rlibs-from-index.sh \
  --solana-version 1.18.16 \
  --compiler-solana-version 1.18.16 \
  --fallback-compiler-solana-version 1.17.0 \
  --platform-tools-version v1.43
```

## Use Cases

- **IDA Pro**: FLIRT signature generation
- **Ghidra**: Function identification
- **Binary Ninja**: Signature matching
- **General**: Solana program reverse engineering

## Directory Structure

```
solana-program/
├── libsolana_program-1.17.0-v1_48.rlib
├── libsolana_program-1.18.0-v1_48.rlib
├── libsolana_program-2.0.0-v1_48.rlib
├── ...
└── deps/
    └── (dependency rlibs)
```

## License

MIT
