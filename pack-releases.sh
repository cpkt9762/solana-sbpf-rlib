#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p releases

echo "[*] Packing core..."
CORE_CRATES=(
  solana-program solana-account-decoder-client-types solana-account solana-account-info
  solana-instruction solana-pubkey solana-cpi solana-program-entrypoint solana-program-error
  solana-message solana-signer solana-hash solana-signature solana-sysvar solana-rent
  solana-clock solana-epoch-schedule solana-native-token solana-sanitize solana-borsh
  solana-bincode solana-short-vec solana-serialize-utils solana-stable-layout
  solana-program-memory solana-program-option solana-program-pack solana-msg
  solana-define-syscall solana-frozen-abi
)
tar -cf - -C rlibs "${CORE_CRATES[@]}" 2>/dev/null | zstd -19 -T0 > releases/solana-sbpf-rlib-core.tar.zst
echo "    -> $(du -h releases/solana-sbpf-rlib-core.tar.zst | cut -f1)"

echo "[*] Packing crypto..."
CRYPTO_CRATES=(
  solana-merkle-tree solana-zk-sdk solana-curve25519 solana-bn254 solana-bls-signatures
  solana-poseidon solana-secp256k1-recover solana-sha256-hasher solana-keccak-hasher
  solana-blake3-hasher solana-ed25519-program solana-secp256r1-program solana-big-mod-exp
  solana-lattice-hash solana-zk-elgamal-proof-program
)
tar -cf - -C rlibs "${CRYPTO_CRATES[@]}" 2>/dev/null | zstd -19 -T0 > releases/solana-sbpf-rlib-crypto.tar.zst
echo "    -> $(du -h releases/solana-sbpf-rlib-crypto.tar.zst | cut -f1)"

echo "[*] Packing anchor..."
tar -cf - -C rlibs anchor-idl anchor-lang anchor-lang-idl anchor-lang-idl-spec anchor-spl anchor-syn 2>/dev/null | zstd -19 -T0 > releases/solana-sbpf-rlib-anchor.tar.zst
echo "    -> $(du -h releases/solana-sbpf-rlib-anchor.tar.zst | cut -f1)"

echo "[*] Packing extra..."
# Get all remaining crates
CORE_AND_CRYPTO=$(printf "%s\n" "${CORE_CRATES[@]}" "${CRYPTO_CRATES[@]}" anchor-idl anchor-lang anchor-lang-idl anchor-lang-idl-spec anchor-spl anchor-syn | sort -u)
ALL_CRATES=$(ls rlibs | sort)
EXTRA_CRATES=$(comm -23 <(echo "$ALL_CRATES") <(echo "$CORE_AND_CRYPTO"))
if [ -n "$EXTRA_CRATES" ]; then
  tar -cf - -C rlibs $EXTRA_CRATES 2>/dev/null | zstd -19 -T0 > releases/solana-sbpf-rlib-extra.tar.zst
  echo "    -> $(du -h releases/solana-sbpf-rlib-extra.tar.zst | cut -f1)"
fi

echo "[*] Done! Packages:"
ls -lh releases/
