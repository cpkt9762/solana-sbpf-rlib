#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p releases/individual

echo "[*] Packing individual crates..."
total=$(ls -d rlibs/*/ | wc -l)
count=0

for dir in rlibs/*/; do
  crate=$(basename "$dir")
  count=$((count + 1))
  printf "\r[%d/%d] %s                    " "$count" "$total" "$crate"
  tar -cf - -C rlibs "$crate" 2>/dev/null | zstd -19 -T0 -q > "releases/individual/${crate}.tar.zst"
done

echo ""
echo "[*] Done! Individual packages:"
ls releases/individual/ | wc -l
du -sh releases/individual/
