set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLCHAIN_WIN="${WINLIBS_ROOT:-C:/tools/codex-winlibs/mingw64}"
STAGE_WIN="${PICO_HOST_STAGE:-C:/tools/aiot-pico-host-stage}"
TMP_WIN="${PICO_HOST_TMP:-C:/tools/codex-tmp}"

TOOLCHAIN_UNIX="$(cygpath -u "$TOOLCHAIN_WIN")"
STAGE_UNIX="$(cygpath -u "$STAGE_WIN")"
TMP_UNIX="$(cygpath -u "$TMP_WIN")"

SRC_WIN="$STAGE_WIN/src"
BUILD_WIN="$STAGE_WIN/build"
SRC_UNIX="$STAGE_UNIX/src"
BUILD_UNIX="$STAGE_UNIX/build"

rm -rf "$SRC_UNIX" "$BUILD_UNIX"
mkdir -p "$SRC_UNIX" "$TMP_UNIX"
cp -R "$ROOT/firmware/pico_pet_node/." "$SRC_UNIX/"

export PATH="$TOOLCHAIN_UNIX/bin:$PATH"
export TMP="$TMP_WIN"
export TEMP="$TMP_WIN"
export TMPDIR="$TMP_WIN"

"$TOOLCHAIN_UNIX/bin/cmake.exe" \
    -S "$SRC_WIN" \
    -B "$BUILD_WIN" \
    -G Ninja \
    -DCMAKE_CXX_COMPILER="$TOOLCHAIN_WIN/bin/g++.exe" \
    -DCMAKE_MAKE_PROGRAM="$TOOLCHAIN_WIN/bin/ninja.exe"
"$TOOLCHAIN_UNIX/bin/cmake.exe" --build "$BUILD_WIN"
"$TOOLCHAIN_UNIX/bin/ctest.exe" --test-dir "$BUILD_WIN" --output-on-failure
"$BUILD_UNIX/pet_node_demo.exe"
