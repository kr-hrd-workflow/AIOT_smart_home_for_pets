#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
MANIFEST="$ROOT/tools/platform-manifest.json"
FIXTURE_ROOT=""
OUTPUT="$ROOT/.runtime/platform-linux.json"
MUTATION="none"
HOST_PROOF=false

to_unix_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf '%s\n' "$1"; fi
}
while (($#)); do
  case "$1" in
    --fixture-root) FIXTURE_ROOT="$(to_unix_path "$2")"; shift 2 ;;
    --output) OUTPUT="$(to_unix_path "$2")"; shift 2 ;;
    --mutation) MUTATION="$2"; shift 2 ;;
    --host-proof) HOST_PROOF=true; shift ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

BASE_PYTHON=""
for candidate in "${PYTHON_PATH:-}" "$(command -v python3 || true)" "$(command -v python || true)"; do
  if [[ -n "$candidate" ]] && "$candidate" -c 'import sys' >/dev/null 2>&1; then BASE_PYTHON="$candidate"; break; fi
done
[[ -n "$BASE_PYTHON" ]] || { printf 'bootstrap Python is unavailable\n' >&2; exit 1; }
BASE_SHA256="$(command -v sha256sum)"
"$BASE_PYTHON" "$ROOT/tools/validate_platform_manifest.py" --manifest "$MANIFEST"
MANIFEST_SHA256="$("$BASE_SHA256" "$MANIFEST")"
MANIFEST_SHA256="${MANIFEST_SHA256%% *}"
MANIFEST_SHA256="${MANIFEST_SHA256^^}"

manifest_value() {
  "$BASE_PYTHON" - "$MANIFEST" "$1" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
separator = "/" if "/" in sys.argv[2] else "."
for part in sys.argv[2].split(separator):
    value = value[part]
print(value)
PY
}

UV_VERSION="$(manifest_value managed_exact.uv.version)"
UV_URL="$(manifest_value managed_exact.uv.linux.url)"
UV_SHA256="$(manifest_value managed_exact.uv.linux.sha256)"
PYTHON_VERSION="$(manifest_value managed_exact.python.version)"
PYTHON_BUILD="$(manifest_value managed_exact.python.build)"
PYTHON_URL="$(manifest_value managed_exact.python.linux.url)"
PYTHON_SHA256="$(manifest_value managed_exact.python.linux.sha256)"
NODE_VERSION="$(manifest_value managed_exact.node.version)"
NODE_URL="$(manifest_value managed_exact.node.linux.url)"
NODE_SHA256="$(manifest_value managed_exact.node.linux.sha256)"
CMAKE_VERSION="$(manifest_value managed_exact.cmake.version)"
CMAKE_URL="$(manifest_value managed_exact.cmake.linux.url)"
CMAKE_SHA256="$(manifest_value managed_exact.cmake.linux.sha256)"
NINJA_VERSION="$(manifest_value managed_exact.ninja.version)"
NINJA_URL="$(manifest_value managed_exact.ninja.linux.url)"
NINJA_SHA256="$(manifest_value managed_exact.ninja.linux.sha256)"
ARM_VERSION="$(manifest_value managed_exact.arm_gnu.version)"
ARM_URL="$(manifest_value managed_exact.arm_gnu.linux.url)"
ARM_SHA256="$(manifest_value managed_exact.arm_gnu.linux.sha256)"
PICO_URL="$(manifest_value managed_exact.pico_sdk.url)"
PICO_TAG="$(manifest_value managed_exact.pico_sdk.tag)"
PICO_COMMIT="$(manifest_value managed_exact.pico_sdk.commit)"
CAP_GIT="$(manifest_value runner_capability/ubuntu-24.04/git/minimum)"
CAP_BASH="$(manifest_value runner_capability/ubuntu-24.04/bash/minimum)"
CAP_GNU="$(manifest_value runner_capability/ubuntu-24.04/gnu_c_cpp/minimum)"
CAP_BINUTILS="$(manifest_value runner_capability/ubuntu-24.04/gnu_binutils/minimum)"
CAP_DOCKER="$(manifest_value runner_capability/ubuntu-24.04/docker/minimum)"
CAP_COMPOSE="$(manifest_value runner_capability/ubuntu-24.04/compose/minimum)"

declare -A paths versions
keys=(
  git_path bash_path uv_path python_path node_path npm_cli_path cmake_path ctest_path ninja_path
  host_cc_path host_cxx_path host_as_path host_ar_path host_ranlib_path host_ld_path host_objcopy_path host_size_path
  arm_toolchain_root arm_gcc_path arm_gxx_path arm_asm_path arm_as_path arm_ar_path arm_ranlib_path arm_ld_path
  arm_objcopy_path arm_size_path docker_path compose_plugin_path
)

write_runtime() {
  local output_parent
  output_parent="$(dirname "$OUTPUT")"
  mkdir -p "$output_parent"
  {
    printf '{"schema_version":1,"manifest_sha256":"%s","platform":"ubuntu-24.04","fixture":%s,"paths":{' "$MANIFEST_SHA256" "$1"
    local first=1 key
    for key in "${keys[@]}"; do
      ((first)) || printf ','
      first=0
      printf '"%s":"%s"' "$key" "${paths[$key]}"
    done
    printf '},"versions":{'
    first=1
    for key in "${keys[@]}"; do
      ((first)) || printf ','
      first=0
      printf '"%s":"%s"' "$key" "${versions[$key]}"
    done
    printf '},"capabilities":{"git":"%s","bash":"%s","gnu_c_cpp":"%s","gnu_binutils":"%s","docker":"%s","compose":"%s"}' \
      "$2" "$3" "$4" "$5" "$6" "$7"
    printf ',"identities":{"pico_sdk":{"url":"%s","tag":"%s","commit":"%s"}}}\n' "$PICO_URL" "$PICO_TAG" "$PICO_COMMIT"
  } >"$OUTPUT"
}

if [[ -n "$FIXTURE_ROOT" ]]; then
  mkdir -p "$FIXTURE_ROOT/bin" "$FIXTURE_ROOT/arm"
  archive="$FIXTURE_ROOT/managed-archive.fixture"
  printf 'sealed managed fixture bytes' >"$archive"
  archive_hash="$("$BASE_SHA256" "$archive")"
  archive_hash="${archive_hash%% *}"
  if [[ "$MUTATION" == wrong-byte ]]; then printf 'tampered' >>"$archive"; fi
  actual_hash="$("$BASE_SHA256" "$archive")"
  actual_hash="${actual_hash%% *}"
  [[ "$actual_hash" == "$archive_hash" ]] || { printf 'managed artifact SHA-256 mismatch\n' >&2; exit 1; }

  for key in "${keys[@]}"; do
    if [[ "$key" == arm_toolchain_root ]]; then
      paths[$key]="$FIXTURE_ROOT/arm"
    else
      paths[$key]="$FIXTURE_ROOT/bin/$key"
      printf '#!/bin/bash\nprintf "fixture-verified\\n"\nexit 0\n' >"${paths[$key]}"
      chmod +x "${paths[$key]}"
    fi
  done
  if [[ -x "$ROOT/.runtime/cpython-3.12.13+20260623/python/python.exe" ]]; then paths[python_path]="$ROOT/.runtime/cpython-3.12.13+20260623/python/python.exe"; fi
  if [[ -x "$ROOT/.runtime/uv-0.11.28/uv.exe" ]]; then paths[uv_path]="$ROOT/.runtime/uv-0.11.28/uv.exe"; fi
  for key in "${keys[@]}"; do versions[$key]='fixture-verified'; done
  versions[git_path]="$CAP_GIT"; versions[bash_path]="$CAP_BASH"; versions[uv_path]="$UV_VERSION"; versions[python_path]="$PYTHON_VERSION+$PYTHON_BUILD"
  versions[node_path]="$NODE_VERSION"; versions[cmake_path]="$CMAKE_VERSION"; versions[ctest_path]="$CMAKE_VERSION"; versions[ninja_path]="$NINJA_VERSION"
  for key in arm_toolchain_root arm_gcc_path arm_gxx_path arm_asm_path arm_as_path arm_ar_path arm_ranlib_path arm_ld_path arm_objcopy_path arm_size_path; do versions[$key]="$ARM_VERSION"; done
  capabilities=("$CAP_GIT" "$CAP_BASH" "$CAP_GNU" "$CAP_BINUTILS" "$CAP_DOCKER" "$CAP_COMPOSE")
  [[ "$MUTATION" != low-capability ]] || capabilities[2]='0'
  [[ "${capabilities[2]}" == "$CAP_GNU" ]] || { printf 'GNU C/C++ capability below %s\n' "$CAP_GNU" >&2; exit 1; }
  if $HOST_PROOF; then
    : "${PETCARE_HOST_CMAKE:?}" "${PETCARE_HOST_CTEST:?}" "${PETCARE_HOST_NINJA:?}" "${PETCARE_HOST_BIN:?}"
    paths[cmake_path]="$(to_unix_path "$PETCARE_HOST_CMAKE")"; paths[ctest_path]="$(to_unix_path "$PETCARE_HOST_CTEST")"; paths[ninja_path]="$(to_unix_path "$PETCARE_HOST_NINJA")"
    host_bin="$(to_unix_path "$PETCARE_HOST_BIN")"
    paths[host_cc_path]="$host_bin/gcc.exe"; paths[host_cxx_path]="$host_bin/g++.exe"; paths[host_as_path]="$host_bin/as.exe"; paths[host_ar_path]="$host_bin/ar.exe"
    paths[host_ranlib_path]="$host_bin/ranlib.exe"; paths[host_ld_path]="$host_bin/ld.exe"; paths[host_objcopy_path]="$host_bin/objcopy.exe"; paths[host_size_path]="$host_bin/size.exe"
    versions[cmake_path]="$CMAKE_VERSION"; versions[ctest_path]="$CMAKE_VERSION"; versions[ninja_path]="$NINJA_VERSION"
    for key in host_cc_path host_cxx_path host_as_path host_ar_path host_ranlib_path host_ld_path host_objcopy_path host_size_path; do versions[$key]='16.1.0'; done
    capabilities[2]='16.1.0'; capabilities[3]='2.46'
  fi
  write_runtime true "${capabilities[@]}"
  printf 'Ubuntu 24.04 complete fixture PASS: %s\n' "$OUTPUT"
  exit 0
fi

RUNTIME="$ROOT/.runtime"
CACHE="$RUNTIME/bootstrap-cache"
MANAGED="$RUNTIME/platform-linux-managed"
mkdir -p "$CACHE" "$MANAGED"
BASE_CURL="$(command -v curl)"
BASE_TAR="$(command -v tar)"
BASE_UNZIP="$(command -v unzip || true)"

download_verify() {
  local name="$1" url="$2" expected="$3" archive="$CACHE/$4"
  [[ -f "$archive" ]] || "$BASE_CURL" -fL --retry 3 -o "$archive" "$url"
  local actual
  actual="$("$BASE_SHA256" "$archive")"; actual="${actual%% *}"; actual="${actual^^}"
  [[ "$actual" == "$expected" ]] || { printf '%s SHA-256 mismatch\n' "$name" >&2; exit 1; }
  printf '%s\n' "$archive"
}
extract_tar() { local archive="$1" target="$2"; [[ -d "$target" ]] || { mkdir -p "$target"; "$BASE_TAR" -xf "$archive" -C "$target"; }; }
extract_zip() {
  local archive="$1" target="$2"
  [[ -d "$target" ]] || {
    mkdir -p "$target"
    if [[ -n "$BASE_UNZIP" ]]; then "$BASE_UNZIP" -q "$archive" -d "$target"
    else "$BASE_PYTHON" -m zipfile -e "$archive" "$target"
    fi
  }
}

if $HOST_PROOF; then
  . /etc/os-release
  [[ "$ID" == ubuntu ]] || { printf 'host proof requires Ubuntu\n' >&2; exit 1; }
  python_archive="$(download_verify python "$PYTHON_URL" "$PYTHON_SHA256" python-linux.tar.gz)"
  cmake_archive="$(download_verify cmake "$CMAKE_URL" "$CMAKE_SHA256" cmake-linux.tar.gz)"
  ninja_archive="$(download_verify ninja "$NINJA_URL" "$NINJA_SHA256" ninja-linux.zip)"
  extract_tar "$python_archive" "$MANAGED/python"
  extract_tar "$cmake_archive" "$MANAGED/cmake"
  extract_zip "$ninja_archive" "$MANAGED/ninja"
  python_binary="python${PYTHON_VERSION%.*}"
  proof_python="$(find "$MANAGED/python" -type f -path "*/bin/$python_binary" -print -quit)"
  proof_cmake="$(find "$MANAGED/cmake" -type f -path '*/bin/cmake' -print -quit)"
  proof_ctest="$(find "$MANAGED/cmake" -type f -path '*/bin/ctest' -print -quit)"
  proof_ninja="$(find "$MANAGED/ninja" -type f -name ninja -print -quit)"
  proof_paths=(
    "$proof_python" "$proof_cmake" "$proof_ctest" "$proof_ninja"
    "$(command -v gcc)" "$(command -v g++)" "$(command -v as)" "$(command -v ar)"
    "$(command -v ranlib)" "$(command -v ld)" "$(command -v objcopy)" "$(command -v size)"
  )
  for path in "${proof_paths[@]}"; do [[ "$path" == /* && -x "$path" ]] || { printf 'missing host proof path: %s\n' "$path" >&2; exit 1; }; done
  "$BASE_PYTHON" - "$OUTPUT" "$MANIFEST_SHA256" "ubuntu-$VERSION_ID" "$PYTHON_VERSION+$PYTHON_BUILD" "$CMAKE_VERSION" "$NINJA_VERSION" "${proof_paths[@]}" <<'PY'
import json, pathlib, subprocess, sys
output, manifest_hash, platform, python_version, cmake_version, ninja_version, *values = sys.argv[1:]
keys = ["python_path", "cmake_path", "ctest_path", "ninja_path", "host_cc_path", "host_cxx_path",
        "host_as_path", "host_ar_path", "host_ranlib_path", "host_ld_path", "host_objcopy_path", "host_size_path"]
paths = dict(zip(keys, values, strict=True))
versions = {key: subprocess.check_output([path, "--version"], text=True, stderr=subprocess.STDOUT).splitlines()[0]
            for key, path in paths.items()}
versions.update(python_path=python_version, cmake_path=cmake_version, ctest_path=cmake_version, ninja_path=ninja_version)
target = pathlib.Path(output)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps({"schema_version": 1, "manifest_sha256": manifest_hash, "platform": platform,
                              "fixture": False, "proof_scope": "host-cpp", "paths": paths, "versions": versions},
                             ensure_ascii=False),
                  encoding="utf-8")
PY
  printf 'Ubuntu host proof bootstrap PASS: %s\n' "$OUTPUT"
  exit 0
fi

[[ "$(. /etc/os-release; printf '%s' "$VERSION_ID")" == 24.04 ]] || { printf 'bootstrap_ci.sh requires Ubuntu 24.04\n' >&2; exit 1; }

uv_archive="$(download_verify uv "$UV_URL" "$UV_SHA256" uv-linux.tar.gz)"
python_archive="$(download_verify python "$PYTHON_URL" "$PYTHON_SHA256" python-linux.tar.gz)"
node_archive="$(download_verify node "$NODE_URL" "$NODE_SHA256" node-linux.tar.xz)"
cmake_archive="$(download_verify cmake "$CMAKE_URL" "$CMAKE_SHA256" cmake-linux.tar.gz)"
ninja_archive="$(download_verify ninja "$NINJA_URL" "$NINJA_SHA256" ninja-linux.zip)"
arm_archive="$(download_verify arm "$ARM_URL" "$ARM_SHA256" arm-linux.tar.xz)"
extract_tar "$uv_archive" "$MANAGED/uv"; extract_tar "$python_archive" "$MANAGED/python"; extract_tar "$node_archive" "$MANAGED/node"
extract_tar "$cmake_archive" "$MANAGED/cmake"; extract_zip "$ninja_archive" "$MANAGED/ninja"; extract_tar "$arm_archive" "$MANAGED/arm"

paths[git_path]="$(command -v git)"; paths[bash_path]="$(command -v bash)"
paths[uv_path]="$(find "$MANAGED/uv" -type f -name uv -print -quit)"; paths[python_path]="$(find "$MANAGED/python" -type f -path '*/bin/python3' -print -quit)"
paths[node_path]="$(find "$MANAGED/node" -type f -path '*/bin/node' -print -quit)"; paths[npm_cli_path]="$(find "$MANAGED/node" -type f -path '*/npm/bin/npm-cli.js' -print -quit)"
paths[cmake_path]="$(find "$MANAGED/cmake" -type f -path '*/bin/cmake' -print -quit)"; paths[ctest_path]="$(find "$MANAGED/cmake" -type f -path '*/bin/ctest' -print -quit)"
paths[ninja_path]="$(find "$MANAGED/ninja" -type f -name ninja -print -quit)"
paths[host_cc_path]="$(command -v gcc)"; paths[host_cxx_path]="$(command -v g++)"; paths[host_as_path]="$(command -v as)"; paths[host_ar_path]="$(command -v ar)"
paths[host_ranlib_path]="$(command -v ranlib)"; paths[host_ld_path]="$(command -v ld)"; paths[host_objcopy_path]="$(command -v objcopy)"; paths[host_size_path]="$(command -v size)"
arm_gcc="$(find "$MANAGED/arm" -type f -name arm-none-eabi-gcc -print -quit)"; arm_bin="$(dirname "$arm_gcc")"; paths[arm_toolchain_root]="$(dirname "$arm_bin")"
paths[arm_gcc_path]="$arm_gcc"; paths[arm_gxx_path]="$arm_bin/arm-none-eabi-g++"; paths[arm_asm_path]="$arm_gcc"; paths[arm_as_path]="$arm_bin/arm-none-eabi-as"
paths[arm_ar_path]="$arm_bin/arm-none-eabi-ar"; paths[arm_ranlib_path]="$arm_bin/arm-none-eabi-ranlib"; paths[arm_ld_path]="$arm_bin/arm-none-eabi-ld"
paths[arm_objcopy_path]="$arm_bin/arm-none-eabi-objcopy"; paths[arm_size_path]="$arm_bin/arm-none-eabi-size"
paths[docker_path]="$(command -v docker)"
for candidate in /usr/libexec/docker/cli-plugins/docker-compose /usr/lib/docker/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose; do [[ -x "$candidate" ]] && paths[compose_plugin_path]="$candidate" && break; done
[[ -n "${paths[compose_plugin_path]:-}" ]] || { printf 'absolute Docker Compose plugin path not found\n' >&2; exit 1; }

for key in "${keys[@]}"; do [[ -n "${paths[$key]:-}" && "${paths[$key]}" == /* && -e "${paths[$key]}" ]] || { printf 'missing absolute path: %s\n' "$key" >&2; exit 1; }; done
[[ "$("${paths[uv_path]}" --version)" == uv\ "$UV_VERSION"* ]] || { printf 'managed uv version mismatch\n' >&2; exit 1; }
[[ "$("${paths[python_path]}" --version)" == "Python $PYTHON_VERSION" ]] || { printf 'managed Python version/build mismatch\n' >&2; exit 1; }
[[ "$("${paths[node_path]}" --version)" == "v$NODE_VERSION" ]] || { printf 'managed Node version mismatch\n' >&2; exit 1; }
[[ "$("${paths[cmake_path]}" --version | head -1)" == "cmake version $CMAKE_VERSION" ]] || { printf 'managed CMake version mismatch\n' >&2; exit 1; }
[[ "$("${paths[ctest_path]}" --version | head -1)" == "ctest version $CMAKE_VERSION" ]] || { printf 'managed CTest version mismatch\n' >&2; exit 1; }
[[ "$("${paths[ninja_path]}" --version)" == "$NINJA_VERSION" ]] || { printf 'managed Ninja version mismatch\n' >&2; exit 1; }
"${paths[arm_gcc_path]}" --version | head -1 | grep -q '14\.2\.1' || { printf 'managed Arm GNU version mismatch\n' >&2; exit 1; }
for key in "${keys[@]}"; do versions[$key]='verified'; done
versions[uv_path]="$UV_VERSION"; versions[python_path]="$PYTHON_VERSION+$PYTHON_BUILD"; versions[node_path]="$NODE_VERSION"; versions[cmake_path]="$CMAKE_VERSION"; versions[ctest_path]="$CMAKE_VERSION"; versions[ninja_path]="$NINJA_VERSION"
versions[npm_cli_path]="$("${paths[node_path]}" "${paths[npm_cli_path]}" --version)"
for key in arm_toolchain_root arm_gcc_path arm_gxx_path arm_asm_path arm_as_path arm_ar_path arm_ranlib_path arm_ld_path arm_objcopy_path arm_size_path; do versions[$key]="$ARM_VERSION"; done

version_ge() { "$BASE_PYTHON" - "$1" "$2" <<'PY'
import re, sys
def parts(value): return tuple(map(int, re.findall(r"\d+", value)[:3]))
raise SystemExit(0 if parts(sys.argv[1]) >= parts(sys.argv[2]) else 1)
PY
}
git_version="$("${paths[git_path]}" --version | grep -oE '[0-9]+(\.[0-9]+)+' | head -1)"; bash_version="$("${paths[bash_path]}" --version | grep -oE '[0-9]+(\.[0-9]+)+' | head -1)"
cc_version="$("${paths[host_cc_path]}" -dumpfullversion)"; binutils_version="$("${paths[host_ld_path]}" --version | grep -oE '[0-9]+(\.[0-9]+)+' | head -1)"
docker_version="$("${paths[docker_path]}" version --format '{{.Client.Version}}')"; compose_version="$("${paths[compose_plugin_path]}" version --short)"
for pair in "$git_version $CAP_GIT" "$bash_version $CAP_BASH" "$cc_version $CAP_GNU" "$binutils_version $CAP_BINUTILS" "$docker_version $CAP_DOCKER" "$compose_version $CAP_COMPOSE"; do version_ge $pair || { printf 'runner capability below floor: %s\n' "$pair" >&2; exit 1; }; done
versions[git_path]="$git_version"; versions[bash_path]="$bash_version"; versions[host_cc_path]="$cc_version"; versions[host_cxx_path]="$cc_version"; versions[docker_path]="$docker_version"; versions[compose_plugin_path]="$compose_version"
write_runtime false "$git_version" "$bash_version" "$cc_version" "$binutils_version" "$docker_version" "$compose_version"
printf 'Ubuntu 24.04 managed bootstrap PASS: %s\n' "$OUTPUT"
