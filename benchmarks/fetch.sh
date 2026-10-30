#!/usr/bin/env bash
# Download PolyBench/C 4.2.1 into benchmarks/src and verify its checksum.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/src"
URL="https://sourceforge.net/projects/polybench/files/polybench-c-4.2.1-beta.tar.gz/download"
SHA256="426519ee8443a5f2175de6a3e9328cda8917a5e33053d0e8f59855ab56d689a4"

mkdir -p "$SRC"
cd "$SRC"

if [[ -d polybench-c-4.2.1-beta ]]; then
  echo "PolyBench already present at $SRC/polybench-c-4.2.1-beta"
  exit 0
fi

curl -sSL -o polybench.tar.gz "$URL"
echo "$SHA256  polybench.tar.gz" | shasum -a 256 -c -
tar xzf polybench.tar.gz
echo "PolyBench extracted to $SRC/polybench-c-4.2.1-beta"
