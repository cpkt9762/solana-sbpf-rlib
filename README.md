# solana-sbpf-rlib

Pre-compiled Solana sBPF `.rlib` files for IDA Pro / Ghidra / Binary Ninja signature generation.

## Downloads

Download the rlib packages from [Releases](https://github.com/cpkt9762/solana-sbpf-rlib/releases):

| Package | Description | Contents |
|---------|-------------|----------|
| **core** | Essential Solana program libraries | solana-program, account, instruction, pubkey, etc. |
| **crypto** | Cryptographic operations | zk-sdk, curve25519, bn254, merkle-tree, etc. |
| **anchor** | Anchor framework | anchor-lang, anchor-spl, anchor-syn, etc. |
| **extra** | Other dependencies | frozen-abi, nonce-account, bincode, etc. |

### Quick Start

```bash
# Install zstd if needed
brew install zstd  # macOS
apt install zstd   # Ubuntu

# Download and extract
curl -LO https://github.com/cpkt9762/solana-sbpf-rlib/releases/download/v1.0.0/solana-sbpf-rlib-core.tar.zst
tar -xf solana-sbpf-rlib-core.tar.zst
```

## Building from Source

To regenerate rlibs yourself:

```bash
# Requirements: Solana CLI, Rust, cargo-build-sbf

./build-rlibs-from-index.sh \
  --solana-version 1.18.16 \
  --compiler-solana-version 1.18.16 \
  --fallback-compiler-solana-version 1.17.0 \
  --platform-tools-version v1.43
```

## Coverage

- **Solana versions**: 1.17.x - 2.x
- **Anchor versions**: 0.29.x - 0.31.x
- **Platform tools**: v1.43+

## Use Cases

- IDA Pro FLIRT signature generation
- Ghidra function identification
- Binary Ninja signature matching
- Solana program reverse engineering

## License

MIT
