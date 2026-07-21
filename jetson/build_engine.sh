#!/bin/sh
set -eu
umask 077

onnx=
manifest=
output=
precision=

while [ "$#" -gt 0 ]; do
    case "$1" in
        --onnx|--model-manifest|--output|--precision)
            [ "$#" -ge 2 ] || { echo "missing value for $1" >&2; exit 2; }
            case "$1" in
                --onnx) [ -z "$onnx" ] || exit 2; onnx=$2 ;;
                --model-manifest) [ -z "$manifest" ] || exit 2; manifest=$2 ;;
                --output) [ -z "$output" ] || exit 2; output=$2 ;;
                --precision) [ -z "$precision" ] || exit 2; precision=$2 ;;
            esac
            shift 2
            ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

[ -n "$onnx" ] && [ -n "$manifest" ] && [ -n "$output" ] && [ "$precision" = fp16 ] || exit 2
[ "$(uname -m)" = aarch64 ] || { echo "aarch64 required" >&2; exit 1; }
grep -q '^# R32 (release), REVISION: 7\.6,' /etc/nv_tegra_release || { echo "L4T R32.7.6 required" >&2; exit 1; }
trt_version=$(dpkg-query -W -f='${Version}' tensorrt 2>/dev/null || true)
case "$trt_version" in 8.2.1*) ;; *) echo "TensorRT 8.2.1 required" >&2; exit 1 ;; esac
[ -x /usr/src/tensorrt/bin/trtexec ] || { echo "trtexec unavailable" >&2; exit 1; }
[ -f "$onnx" ] && [ ! -L "$onnx" ] && [ -f "$manifest" ] && [ ! -L "$manifest" ] || exit 1
[ -d "$(dirname "$output")" ] || exit 1

python3 - "$onnx" "$manifest" <<'PY'
import hashlib
import json
import sys

onnx_path, manifest_path = sys.argv[1:]
with open(manifest_path, "r", encoding="utf-8") as handle:
    value = json.load(handle)
keys = {
    "onnx_opset", "export_argv", "export_tool", "source_sha256", "onnx_sha256",
    "input_tensor", "output_tensor", "precision", "tensorrt_version",
}
if set(value) != keys or value["onnx_opset"] not in (11, 12, 13):
    raise SystemExit("invalid model manifest")
if value["input_tensor"] != {"name": "images", "shape": [1, 3, 640, 640]}:
    raise SystemExit("invalid input tensor")
if value["output_tensor"] != {"name": "output0", "shape": [1, 84, 8400]}:
    raise SystemExit("invalid output tensor")
if value["precision"] != "fp16" or not value["tensorrt_version"].startswith("8.2.1"):
    raise SystemExit("invalid runtime pin")
if "TBD" in json.dumps(value).upper():
    raise SystemExit("placeholder in manifest")
digest = hashlib.sha256()
with open(onnx_path, "rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
if digest.hexdigest().lower() != value["onnx_sha256"].lower():
    raise SystemExit("ONNX SHA mismatch")
PY

engine_tmp=$(mktemp "${output}.partial.XXXXXX")
metadata_tmp=$(mktemp "${output}.json.partial.XXXXXX")
raw_tmp=$(mktemp "${output}.input.partial.XXXXXX")
output_tmp=$(mktemp "${output}.output.partial.XXXXXX")
layer_tmp=$(mktemp "${output}.layers.partial.XXXXXX")
cleanup() { rm -f "$engine_tmp" "$metadata_tmp" "$raw_tmp" "$output_tmp" "$layer_tmp"; }
trap cleanup EXIT HUP INT TERM
rm -f "$engine_tmp"

/usr/src/tensorrt/bin/trtexec \
    --onnx="$onnx" --saveEngine="$engine_tmp" --explicitBatch --fp16 --workspace=512 \
    --minTiming=1 --avgTiming=1 --buildOnly
chmod 600 "$engine_tmp"

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
golden="$script_dir/../dashboard/public/og.png"
[ "$(sha256sum "$golden" | awk '{print toupper($1)}')" = E87C167B3539CE863C2652C7D0055EA38C8E531948714B3119369CFD694601C8 ] || {
    echo "golden image mismatch" >&2
    exit 1
}
python3 - "$golden" "$raw_tmp" <<'PY'
import cv2
import numpy as np
import sys

frame = cv2.imread(sys.argv[1], cv2.IMREAD_COLOR)
if frame is None:
    raise SystemExit("golden image unavailable")
frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)
canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
canvas[80:560] = frame
tensor = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1), dtype=np.float32).reshape(1, 3, 640, 640) / 255.0
tensor.tofile(sys.argv[2])
PY

/usr/src/tensorrt/bin/trtexec \
    --loadEngine="$engine_tmp" --loadInputs=images:"$raw_tmp" --exportOutput="$output_tmp" \
    --exportLayerInfo="$layer_tmp" --iterations=1 --warmUp=0 --duration=0

python3 - "$output_tmp" <<'PY'
import json
import math
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    value = json.load(handle)
numbers = []
def walk(item):
    if type(item) in (int, float):
        numbers.append(float(item))
    elif type(item) is list:
        for child in item:
            walk(child)
    elif type(item) is dict:
        for child in item.values():
            walk(child)
walk(value)
if not numbers or not all(math.isfinite(number) for number in numbers):
    raise SystemExit("invalid smoke output")
PY

module_model=$(tr -d '\000' </sys/firmware/devicetree/base/model)
engine_sha=$(sha256sum "$engine_tmp" | awk '{print $1}')
MODULE_MODEL=$module_model ENGINE_SHA=$engine_sha python3 - "$manifest" "$metadata_tmp" <<'PY'
import json
import os
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    value = json.load(handle)
value["jetson_module_model"] = os.environ["MODULE_MODEL"]
value["engine_sha256"] = os.environ["ENGINE_SHA"]
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(value, handle, sort_keys=True, separators=(",", ":"))
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(sys.argv[2], 0o600)
PY

mv -f "$engine_tmp" "$output"
mv -f "$metadata_tmp" "$output.json"
trap - EXIT HUP INT TERM
cleanup
