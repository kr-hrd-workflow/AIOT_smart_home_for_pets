#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="${BASH_SOURCE[0]%/*}"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
RUNTIME="$ROOT/.runtime/platform-linux.json"
BUILD_DIR="$ROOT/build/pico-host-linux"
DRY_RUN=false
while (($#)); do
  case "$1" in
    --runtime) RUNTIME="$2"; shift 2 ;;
    --build-dir) BUILD_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

json_string() {
  local text
  text="$(<"$RUNTIME")"
  [[ "$text" =~ \"$1\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]] || { printf 'missing runtime key: %s\n' "$1" >&2; exit 1; }
  printf '%s\n' "${BASH_REMATCH[1]}"
}

PYTHON_PATH="$(json_string python_path)"
if [[ "$PYTHON_PATH" == *.exe ]]; then
  TMP_WIN="${PICO_HOST_TMP:-C:/tools/codex-tmp}"
  "$PYTHON_PATH" -c 'import pathlib, sys; pathlib.Path(sys.argv[1]).mkdir(parents=True, exist_ok=True)' "$TMP_WIN"
  export TMP="$TMP_WIN" TEMP="$TMP_WIN" TMPDIR="$TMP_WIN"
fi
MANIFEST="$ROOT/tools/platform-manifest.json"
TOOLCHAIN="$ROOT/.runtime/toolchains/pico-host-linux.cmake"
"$PYTHON_PATH" - "$RUNTIME" "$MANIFEST" "$TOOLCHAIN" "$DRY_RUN" <<'PY'
import hashlib, json, pathlib, re, sys
runtime_path, manifest_path, toolchain_path = map(pathlib.Path, sys.argv[1:4])
data = json.loads(runtime_path.read_text(encoding="utf-8"))
if data.get("fixture") and sys.argv[4] != "true":
    raise SystemExit("fixture runtime cannot prove a real host build")
expected = hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper()
if data.get("manifest_sha256", "").upper() != expected:
    raise SystemExit("runtime authority hash mismatch")
required = [
    "python_path", "cmake_path", "ctest_path", "ninja_path", "host_cc_path", "host_cxx_path",
    "host_as_path", "host_ar_path", "host_ranlib_path", "host_ld_path", "host_objcopy_path", "host_size_path",
]
paths = data.get("paths")
versions = data.get("versions")
if not isinstance(paths, dict) or not isinstance(versions, dict):
    raise SystemExit("malformed runtime manifest")
for key in required:
    raw = paths.get(key, "")
    if sys.platform == "win32" and re.match(r"^/[A-Za-z]/", raw):
        raw = raw[1].upper() + ":" + raw[2:]
    path = pathlib.Path(raw)
    if not path.is_absolute() or not path.exists() or not versions.get(key):
        raise SystemExit(f"invalid runtime closure: {key}")
toolchain_path.parent.mkdir(parents=True, exist_ok=True)
def cmake_path(key):
    raw = paths[key]
    if sys.platform == "win32" and re.match(r"^/[A-Za-z]/", raw):
        raw = raw[1].upper() + ":" + raw[2:]
    return raw.replace("\\", "/")
toolchain_path.write_text("\n".join([
    "set(CMAKE_SYSTEM_NAME Windows)" if sys.platform == "win32" else "set(CMAKE_SYSTEM_NAME Linux)",
    f'set(CMAKE_C_COMPILER "{cmake_path("host_cc_path")}")',
    f'set(CMAKE_CXX_COMPILER "{cmake_path("host_cxx_path")}")',
    f'set(CMAKE_ASM_COMPILER "{cmake_path("host_as_path")}")',
    f'set(CMAKE_AR "{cmake_path("host_ar_path")}")',
    f'set(CMAKE_RANLIB "{cmake_path("host_ranlib_path")}")',
    f'set(CMAKE_LINKER "{cmake_path("host_ld_path")}")',
    f'set(CMAKE_OBJCOPY "{cmake_path("host_objcopy_path")}")',
    f'set(CMAKE_SIZE "{cmake_path("host_size_path")}")',
]) + "\n", encoding="utf-8")
PY

CMAKE_PATH="$(json_string cmake_path)"
CTEST_PATH="$(json_string ctest_path)"
NINJA_PATH="$(json_string ninja_path)"
closure=(host_cc_path host_cxx_path host_as_path host_ar_path host_ranlib_path host_ld_path host_objcopy_path host_size_path)
if $DRY_RUN; then
  "$CMAKE_PATH" --version
  "$CTEST_PATH" --version
  "$NINJA_PATH" --version
  for key in "${closure[@]}"; do "$(json_string "$key")" --version; done
  printf 'manifest-backed host build PASS\n'
  exit 0
fi

"$CMAKE_PATH" -S "$ROOT/firmware/pico_pet_node" -B "$BUILD_DIR" -G Ninja \
  "-DCMAKE_TOOLCHAIN_FILE=$TOOLCHAIN" "-DCMAKE_MAKE_PROGRAM=$NINJA_PATH"
"$CMAKE_PATH" --build "$BUILD_DIR"
"$CTEST_PATH" --test-dir "$BUILD_DIR" --output-on-failure
printf 'manifest-backed host build PASS\n'
