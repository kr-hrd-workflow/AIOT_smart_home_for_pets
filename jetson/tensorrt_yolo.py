import hashlib
import json
import math
import os
import re
import stat
import subprocess
import threading


FRAME_SHAPE = (480, 640, 3)
CLASS_ORDER = ("person", "dog", "cat")
CLASS_IDS = {"person": 0, "cat": 15, "dog": 16}
MANIFEST_KEYS = {
    "onnx_opset",
    "export_argv",
    "export_tool",
    "source_sha256",
    "onnx_sha256",
    "input_tensor",
    "output_tensor",
    "precision",
    "tensorrt_version",
}
_STARTING = object()


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _read_json(path, owner_only=False):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("invalid_metadata_file")
    if owner_only and os.name != "nt" and (
        info.st_uid != os.geteuid() or info.st_mode & 0o077 or not info.st_mode & stat.S_IRUSR
    ):
        raise ValueError("invalid_metadata_permissions")
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _valid_sha256(value):
    if type(value) is not str or len(value) != 64:
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value)


def _validate_manifest(value):
    if type(value) is not dict or set(value) != MANIFEST_KEYS:
        raise ValueError("invalid_model_manifest")
    if type(value["onnx_opset"]) is not int or value["onnx_opset"] not in (11, 12, 13):
        raise ValueError("invalid_model_manifest")
    if type(value["export_argv"]) is not list or not value["export_argv"] or any(type(x) is not str for x in value["export_argv"]):
        raise ValueError("invalid_model_manifest")
    tool = value["export_tool"]
    if type(tool) is not dict or set(tool) != {"ultralytics", "torch", "onnx", "dynamo"}:
        raise ValueError("invalid_model_manifest")
    if any(type(tool[name]) is not str or not tool[name] for name in ("ultralytics", "torch", "onnx")) or tool["dynamo"] is not False:
        raise ValueError("invalid_model_manifest")
    if not _valid_sha256(value["source_sha256"]) or not _valid_sha256(value["onnx_sha256"]):
        raise ValueError("invalid_model_manifest")
    if value["input_tensor"] != {"name": "images", "shape": [1, 3, 640, 640]}:
        raise ValueError("invalid_model_manifest")
    if value["output_tensor"] != {"name": "output0", "shape": [1, 84, 8400]}:
        raise ValueError("invalid_model_manifest")
    if value["precision"] != "fp16" or type(value["tensorrt_version"]) is not str or not value["tensorrt_version"].startswith("8.2.1"):
        raise ValueError("invalid_model_manifest")
    if any("TBD" in item.upper() for item in value["export_argv"]):
        raise ValueError("invalid_model_manifest")
    return value


def _module_model():
    with open("/sys/firmware/devicetree/base/model", "rb") as handle:
        return handle.read().rstrip(b"\0").decode("utf-8")


def _validated_engine(engine_path, manifest_path, metadata_path, module_model):
    manifest = _validate_manifest(_read_json(manifest_path))
    metadata = _read_json(metadata_path, owner_only=True)
    if type(metadata) is not dict or set(metadata) != MANIFEST_KEYS | {"jetson_module_model", "engine_sha256"}:
        raise ValueError("engine_metadata_mismatch")
    if any(metadata.get(key) != manifest[key] for key in MANIFEST_KEYS):
        raise ValueError("engine_metadata_mismatch")
    if metadata["jetson_module_model"] != module_model or not _valid_sha256(metadata["engine_sha256"]):
        raise ValueError("engine_metadata_mismatch")
    info = os.lstat(engine_path)
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("invalid_engine_file")
    if os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o077 or not info.st_mode & stat.S_IRUSR):
        raise ValueError("invalid_engine_permissions")
    with open(engine_path, "rb") as handle:
        engine = handle.read()
    if not engine or _sha256_bytes(engine).lower() != metadata["engine_sha256"].lower():
        raise ValueError("engine_metadata_mismatch")
    return engine, manifest


def letterbox_geometry(width, height):
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("invalid_frame")
    scale = min(640.0 / width, 640.0 / height)
    resized_width = int(round(width * scale))
    resized_height = int(round(height * scale))
    return scale, (640 - resized_width) // 2, (640 - resized_height) // 2, resized_width, resized_height


def normalize_detections(candidates):
    grouped = {name: [] for name in CLASS_ORDER}
    for candidate in candidates:
        detected_type = candidate.get("detected_type")
        try:
            confidence = float(candidate.get("confidence"))
            coordinates = tuple(float(value) for value in candidate.get("xyxy", ()))
        except (TypeError, ValueError):
            continue
        if detected_type not in grouped or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            continue
        if len(coordinates) != 4 or not all(math.isfinite(value) for value in coordinates):
            continue
        x1, y1, x2, y2 = coordinates
        if not (x1 < x2 and y1 < y2):
            continue
        x1, x2 = min(640.0, max(0.0, x1)), min(640.0, max(0.0, x2))
        y1, y2 = min(480.0, max(0.0, y1)), min(480.0, max(0.0, y2))
        left, top, right, bottom = math.floor(x1), math.floor(y1), math.ceil(x2), math.ceil(y2)
        if right <= left or bottom <= top:
            continue
        grouped[detected_type].append(
            {
                "detected_type": detected_type,
                "confidence": confidence,
                "bbox_x": left,
                "bbox_y": top,
                "bbox_width": right - left,
                "bbox_height": bottom - top,
            }
        )
    selected = []
    for detected_type in CLASS_ORDER:
        choices = grouped[detected_type]
        if choices:
            selected.append(
                min(
                    choices,
                    key=lambda item: (
                        -item["confidence"],
                        item["bbox_x"],
                        item["bbox_y"],
                        item["bbox_width"],
                        item["bbox_height"],
                    ),
                )
            )
    return selected


def postprocess_yolo(output, confidence_threshold=0.25):
    if tuple(output.shape) != (1, 84, 8400):
        raise ValueError("invalid_engine_output")
    candidates = []
    for detected_type in CLASS_ORDER:
        class_row = 4 + CLASS_IDS[detected_type]
        for anchor in range(8400):
            confidence = float(output[0, class_row, anchor])
            if not math.isfinite(confidence) or confidence < confidence_threshold or confidence > 1:
                continue
            center_x = float(output[0, 0, anchor])
            center_y = float(output[0, 1, anchor])
            width = float(output[0, 2, anchor])
            height = float(output[0, 3, anchor])
            candidates.append(
                {
                    "detected_type": detected_type,
                    "confidence": confidence,
                    "xyxy": (
                        center_x - width / 2.0,
                        center_y - height / 2.0 - 80.0,
                        center_x + width / 2.0,
                        center_y + height / 2.0 - 80.0,
                    ),
                }
            )
    return normalize_detections(candidates)


class _TensorRtBackend(object):
    def __init__(self, engine_bytes, manifest, trt, cuda, np):
        self.logger = trt.Logger(trt.Logger.ERROR)
        self.runtime = trt.Runtime(self.logger)
        self.engine = self.runtime.deserialize_cuda_engine(engine_bytes)
        if self.engine is None or self.engine.num_bindings != 2:
            raise ValueError("invalid_engine_bindings")
        self.context = self.engine.create_execution_context()
        if self.context is None:
            raise ValueError("invalid_engine_bindings")
        expected = (manifest["input_tensor"], manifest["output_tensor"])
        self.host = []
        self.device = []
        self.bindings = []
        self.cuda = cuda
        self.np = np
        self.thread_id = threading.current_thread().ident
        try:
            for index, tensor in enumerate(expected):
                if self.engine.get_binding_name(index) != tensor["name"] or tuple(self.engine.get_binding_shape(index)) != tuple(tensor["shape"]):
                    raise ValueError("invalid_engine_bindings")
                if bool(self.engine.binding_is_input(index)) != (index == 0) or trt.nptype(self.engine.get_binding_dtype(index)) != np.float32:
                    raise ValueError("invalid_engine_bindings")
                host = np.zeros(int(np.prod(tensor["shape"])), dtype=np.float32)
                device = cuda.malloc(host.nbytes)
                self.host.append(host)
                self.device.append(device)
                self.bindings.append(device.value if hasattr(device, "value") else int(device))
        except BaseException:
            self.close()
            raise

    def __call__(self, value):
        if threading.current_thread().ident != self.thread_id:
            raise RuntimeError("tensorrt_thread_mismatch")
        self.np.copyto(self.host[0], value.ravel())
        self.cuda.copy_host_to_device(self.device[0], self.host[0])
        if not self.context.execute_v2(self.bindings):
            raise RuntimeError("tensorrt_execution_failed")
        self.cuda.copy_device_to_host(self.host[1], self.device[1])
        self.cuda.synchronize()
        return self.host[1].reshape((1, 84, 8400)).copy()

    def close(self):
        first_error = None
        while self.device:
            try:
                self.cuda.free(self.device.pop())
            except BaseException as error:
                first_error = first_error or error
        self.bindings = []
        if first_error is not None:
            raise first_error


class _CudaRuntime(object):
    def __init__(self, library=None):
        import ctypes

        self.ctypes = ctypes
        self.lib = library or ctypes.CDLL("libcudart.so.10.2")
        self.lib.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
        self.lib.cudaMalloc.restype = ctypes.c_int
        self.lib.cudaFree.argtypes = [ctypes.c_void_p]
        self.lib.cudaFree.restype = ctypes.c_int
        self.lib.cudaMemcpy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        self.lib.cudaMemcpy.restype = ctypes.c_int
        self.lib.cudaDeviceSynchronize.argtypes = []
        self.lib.cudaDeviceSynchronize.restype = ctypes.c_int
        self.lib.cudaGetErrorString.argtypes = [ctypes.c_int]
        self.lib.cudaGetErrorString.restype = ctypes.c_char_p

    def _check(self, code):
        if code:
            message = self.lib.cudaGetErrorString(code)
            text = message.decode("ascii", "replace") if message else "unknown"
            raise RuntimeError("cuda_error_{}_{}".format(code, text))

    def malloc(self, size):
        pointer = self.ctypes.c_void_p()
        self._check(self.lib.cudaMalloc(self.ctypes.byref(pointer), size))
        if not pointer.value:
            raise RuntimeError("cuda_allocation_failed")
        return pointer

    def free(self, pointer):
        self._check(self.lib.cudaFree(pointer))

    def copy_host_to_device(self, device, host):
        source = self.ctypes.c_void_p(int(host.ctypes.data))
        self._check(self.lib.cudaMemcpy(device, source, host.nbytes, 1))

    def copy_device_to_host(self, host, device):
        destination = self.ctypes.c_void_p(int(host.ctypes.data))
        self._check(self.lib.cudaMemcpy(destination, device, host.nbytes, 2))

    def synchronize(self):
        self._check(self.lib.cudaDeviceSynchronize())


class TensorRtYolo(object):
    def __init__(self, engine_path, manifest_path=None, metadata_path=None, backend_factory=None, module_model=None):
        manifest_path = manifest_path or os.path.join(os.path.dirname(__file__), "model-manifest.json")
        metadata_path = metadata_path or engine_path + ".json"
        engine_bytes, manifest = _validated_engine(
            engine_path,
            manifest_path,
            metadata_path,
            module_model if module_model is not None else _module_model(),
        )
        import cv2
        import numpy as np

        self.cv2 = cv2
        self.np = np
        if backend_factory is None:
            import tensorrt as trt

            if trt.__version__ != manifest["tensorrt_version"]:
                raise ValueError("tensorrt_version_mismatch")
            backend_factory = lambda value, model: _TensorRtBackend(value, model, trt, _CudaRuntime(), np)
        self.backend = backend_factory(engine_bytes, manifest)

    def infer(self, frame):
        if not isinstance(frame, self.np.ndarray) or frame.dtype != self.np.uint8 or frame.shape != FRAME_SHAPE:
            raise ValueError("invalid_frame")
        canvas = self.np.full((640, 640, 3), 114, dtype=self.np.uint8)
        canvas[80:560, :, :] = frame
        tensor = self.np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1), dtype=self.np.float32)
        tensor = tensor.reshape((1, 3, 640, 640)) / 255.0
        return postprocess_yolo(self.backend(tensor))

    def close(self):
        close = getattr(self.backend, "close", None)
        if close is not None:
            close()


def build_gstreamer_pipeline(output_path):
    escaped = output_path.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "appsrc name=source is-live=false format=time "
        "caps=video/x-raw,format=BGR,width=640,height=480,framerate=10/1 "
        "! videoconvert ! video/x-raw,format=BGRx ! nvvidconv "
        "! video/x-raw(memory:NVMM),format=NV12,width=640,height=480,framerate=10/1 "
        "! nvv4l2h264enc insert-sps-pps=true ! h264parse ! qtmux "
        '! filesink location="{}"'.format(escaped)
    )


def validate_mp4_description(description, frame_count):
    required = (
        "video: video/x-h264",
        "width=(int)640",
        "height=(int)480",
        "framerate=(fraction)10/1",
        "chroma-format=(string)4:2:0",
        "bit-depth-luma=(uint)8",
        "bit-depth-chroma=(uint)8",
        "container format: Quicktime",
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", description)
    if any(value not in description for value in required) or match is None:
        raise RuntimeError("invalid_media")
    duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    if abs(duration - frame_count / 10.0) > 0.1:
        raise RuntimeError("invalid_media")


class GstreamerEncoder(object):
    def __init__(self):
        import cv2
        import gi
        import numpy as np

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        self.cv2 = cv2
        self.np = np
        self.Gst = Gst
        self.discoverer = "/usr/bin/gst-discoverer-1.0"
        if not os.path.isfile(self.discoverer) or not os.access(self.discoverer, os.X_OK):
            raise RuntimeError("discoverer_unavailable")
        self._lock = threading.Lock()
        self._pipeline = None
        self._aborting = None
        self._abort_requested = False

    def abort(self):
        with self._lock:
            self._abort_requested = True
            pipeline = self._pipeline
            if pipeline is None or getattr(self, "_aborting", None) is pipeline:
                return
            self._aborting = pipeline
        if pipeline is not _STARTING:
            pipeline.set_state(self.Gst.State.NULL)

    def __call__(self, frames, partial_path):
        with self._lock:
            if self._abort_requested:
                raise RuntimeError("encoder_aborted")
            if self._pipeline is not None:
                raise RuntimeError("encoder_busy")
            self._pipeline = _STARTING
        pipeline = None
        try:
            if type(frames) is not tuple or not frames or type(partial_path) is not str:
                raise ValueError("invalid_frames")
            buckets = tuple(item[0] for item in frames)
            if any(type(item) is not tuple or len(item) != 2 or type(item[0]) is not int or type(item[1]) is not bytes for item in frames):
                raise ValueError("invalid_frames")
            if buckets != tuple(range(buckets[0], buckets[0] + len(buckets))):
                raise ValueError("invalid_frames")
            info = os.lstat(partial_path)
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or (
                os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o077)
            ):
                raise ValueError("invalid_output_path")
            pipeline = self.Gst.parse_launch(build_gstreamer_pipeline(partial_path))
            source = pipeline.get_by_name("source")
            bus = pipeline.get_bus()
            with self._lock:
                if self._abort_requested:
                    self._pipeline = None
                    self._aborting = None
                    raise RuntimeError("encoder_aborted")
                self._pipeline = pipeline
                if pipeline.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
                    self._pipeline = None
                    raise RuntimeError("encoder_failed")
            for index, unused_pair in enumerate(frames):
                jpeg = unused_pair[1]
                frame = self.cv2.imdecode(self.np.frombuffer(jpeg, dtype=self.np.uint8), self.cv2.IMREAD_COLOR)
                if frame is None or frame.dtype != self.np.uint8 or frame.shape != FRAME_SHAPE:
                    raise ValueError("invalid_frame")
                data = self.np.ascontiguousarray(frame).tobytes()
                buffer = self.Gst.Buffer.new_allocate(None, len(data), None)
                buffer.fill(0, data)
                buffer.pts = index * self.Gst.SECOND // 10
                buffer.dts = buffer.pts
                buffer.duration = self.Gst.SECOND // 10
                if source.emit("push-buffer", buffer) != self.Gst.FlowReturn.OK:
                    raise RuntimeError("encoder_failed")
            if source.emit("end-of-stream") != self.Gst.FlowReturn.OK:
                raise RuntimeError("encoder_failed")
            message = bus.timed_pop_filtered(5 * self.Gst.SECOND, self.Gst.MessageType.ERROR | self.Gst.MessageType.EOS)
            if message is None or message.type != self.Gst.MessageType.EOS:
                raise RuntimeError("encoder_failed")
            if os.path.getsize(partial_path) <= 0:
                raise RuntimeError("encoder_failed")
            try:
                description = subprocess.check_output(
                    [self.discoverer, "-v", partial_path],
                    stderr=subprocess.STDOUT,
                    timeout=5,
                    universal_newlines=True,
                )
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                raise RuntimeError("invalid_media")
            validate_mp4_description(description, len(frames))
        except BaseException:
            if type(partial_path) is str:
                try:
                    os.unlink(partial_path)
                except OSError:
                    pass
            raise
        finally:
            with self._lock:
                if self._pipeline is _STARTING or self._pipeline is pipeline:
                    self._pipeline = None
                if self._aborting is _STARTING or self._aborting is pipeline:
                    self._aborting = None
            if pipeline is not None:
                pipeline.set_state(self.Gst.State.NULL)
        return {
            "width": 640,
            "height": 480,
            "frame_count": len(frames),
            "frame_rate": "10/1",
            "duration_seconds": len(frames) / 10.0,
            "video_codec": "h264",
            "pixel_format": "yuv420p",
        }
