import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from jetson.tensorrt_yolo import (
    TensorRtYolo,
    _CudaRuntime,
    _TensorRtBackend,
    letterbox_geometry,
    normalize_detections,
    postprocess_yolo,
)


MANIFEST = {
    "onnx_opset": 13,
    "export_argv": ["python", "export.py", "--opset", "13"],
    "export_tool": {
        "ultralytics": "8.3.0",
        "torch": "2.13.0+cpu",
        "onnx": "1.16.1",
        "dynamo": False,
    },
    "source_sha256": "0" * 64,
    "onnx_sha256": "1" * 64,
    "input_tensor": {"name": "images", "shape": [1, 3, 640, 640]},
    "output_tensor": {"name": "output0", "shape": [1, 84, 8400]},
    "precision": "fp16",
    "tensorrt_version": "8.2.1.8",
}


class FakeBackend(object):
    def __init__(self, output):
        self.output = output
        self.inputs = []

    def __call__(self, value):
        self.inputs.append(value.copy())
        return self.output


class TensorRtYoloTests(unittest.TestCase):
    def test_build_script_reads_unicode_manifest_as_utf8(self):
        script = (Path(__file__).parents[1] / "build_engine.sh").read_text(encoding="utf-8")
        self.assertGreaterEqual(script.count('encoding="utf-8"'), 3)

    def test_build_script_uses_stable_jetson_smoke_fixture(self):
        script = (Path(__file__).parents[1] / "build_engine.sh").read_text(encoding="utf-8")
        fixture = Path(__file__).parent / "fixtures" / "engine-smoke.ppm"
        self.assertTrue(fixture.is_file())
        self.assertNotIn("dashboard/public", script)
        self.assertIn('golden="$script_dir/tests/fixtures/engine-smoke.ppm"', script)
        self.assertIn(hashlib.sha256(fixture.read_bytes()).hexdigest().upper(), script)

    def test_letterbox_and_half_open_normalization_are_deterministic(self):
        self.assertEqual(letterbox_geometry(640, 480), (1.0, 0, 80, 640, 480))
        selected = normalize_detections(
            [
                {"detected_type": "dog", "confidence": 0.8, "xyxy": [-2.2, -3.8, 640.4, 480.9]},
                {"detected_type": "dog", "confidence": 0.8, "xyxy": [9.9, 10.2, 19.9, 20.1]},
                {"detected_type": "cat", "confidence": 0.7, "xyxy": [30.2, 30.2, 40.0, 40.0]},
                {"detected_type": "horse", "confidence": 1.0, "xyxy": [1, 2, 3, 4]},
                {"detected_type": "person", "confidence": float("nan"), "xyxy": [1, 2, 3, 4]},
                {"detected_type": "cat", "confidence": 1.1, "xyxy": [1, 2, 3, 4]},
            ]
        )
        self.assertEqual(
            selected,
            [
                {
                    "detected_type": "dog",
                    "confidence": 0.8,
                    "bbox_x": 0,
                    "bbox_y": 0,
                    "bbox_width": 640,
                    "bbox_height": 480,
                },
                {
                    "detected_type": "cat",
                    "confidence": 0.7,
                    "bbox_x": 30,
                    "bbox_y": 30,
                    "bbox_width": 10,
                    "bbox_height": 10,
                },
            ],
        )

    def test_raw_output_maps_back_to_640_by_480_and_filters_classes(self):
        output = np.zeros((1, 84, 8400), dtype=np.float32)
        output[0, 0:4, 0] = [320.0, 320.0, 100.2, 120.2]
        output[0, 4 + 16, 0] = 0.9
        output[0, 0:4, 1] = [100.0, 100.0, 20.0, 20.0]
        output[0, 4 + 2, 1] = 0.99
        output[0, 0:4, 2] = [10.0, 10.0, 4.0, 4.0]
        output[0, 4, 2] = np.nan

        self.assertEqual(
            postprocess_yolo(output, 0.25),
            [
                {
                    "detected_type": "dog",
                    "confidence": float(output[0, 20, 0]),
                    "bbox_x": 269,
                    "bbox_y": 179,
                    "bbox_width": 102,
                    "bbox_height": 122,
                }
            ],
        )

    def test_tensorrt_82_backend_validates_bindings_and_copy_order(self):
        calls = []

        class Device(object):
            def __init__(self, value):
                self.value = value

            def __int__(self):
                return self.value

        class Cuda(object):
            def __init__(self):
                self.next_pointer = 1

            def malloc(self, unused_size):
                pointer = Device(self.next_pointer)
                self.next_pointer += 1
                return pointer

            @staticmethod
            def copy_host_to_device(unused_device, unused_host):
                calls.append("htod")

            @staticmethod
            def copy_device_to_host(unused_host, unused_device):
                calls.append("dtoh")

            @staticmethod
            def synchronize():
                calls.append("sync")

            @staticmethod
            def free(device):
                calls.append("free{}".format(int(device)))

        class Context(object):
            def execute_v2(self, bindings):
                self.bindings = bindings
                calls.append("execute")
                return True

        context = Context()

        class Engine(object):
            num_bindings = 2

            def create_execution_context(self):
                return context

            def get_binding_name(self, index):
                return ("images", "output0")[index]

            def get_binding_shape(self, index):
                return ((1, 3, 640, 640), (1, 84, 8400))[index]

            def binding_is_input(self, index):
                return index == 0

            def get_binding_dtype(self, unused_index):
                return np.float32

        class Runtime(object):
            def __init__(self, unused_logger):
                pass

            def deserialize_cuda_engine(self, unused_bytes):
                return Engine()

        class Logger(object):
            ERROR = 1

            def __init__(self, unused_level):
                pass

        class Trt(object):
            @staticmethod
            def nptype(value):
                return value

        Trt.Logger = Logger
        Trt.Runtime = Runtime

        backend = _TensorRtBackend(b"engine", MANIFEST, Trt, Cuda(), np)
        calls[:] = []

        result = backend(np.zeros((1, 3, 640, 640), dtype=np.float32))
        backend.close()

        self.assertEqual(result.shape, (1, 84, 8400))
        self.assertEqual(calls, ["htod", "execute", "dtoh", "sync", "free2", "free1"])
        self.assertEqual(context.bindings, [1, 2])

    def test_cuda_runtime_turns_nonzero_return_code_into_fixed_error(self):
        class Library(object):
            @staticmethod
            def cudaGetErrorString(unused_code):
                return b"invalid value"

        runtime = _CudaRuntime.__new__(_CudaRuntime)
        runtime.lib = Library()
        with self.assertRaisesRegex(RuntimeError, "cuda_error_7_invalid value"):
            runtime._check(7)

    def test_constructor_rejects_metadata_before_backend_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = os.path.join(directory, "model.engine")
            manifest = os.path.join(directory, "model-manifest.json")
            metadata = engine + ".json"
            with open(engine, "wb") as handle:
                handle.write(b"engine")
            os.chmod(engine, 0o600)
            with open(manifest, "w", encoding="utf-8") as handle:
                json.dump(MANIFEST, handle)
            bad = dict(MANIFEST)
            bad["onnx_opset"] = 12
            bad["jetson_module_model"] = "NVIDIA Jetson Nano Developer Kit"
            bad["engine_sha256"] = hashlib.sha256(b"engine").hexdigest()
            with open(metadata, "w", encoding="utf-8") as handle:
                json.dump(bad, handle)
            os.chmod(metadata, 0o600)
            called = []

            with self.assertRaisesRegex(ValueError, "engine_metadata_mismatch"):
                TensorRtYolo(
                    engine,
                    manifest_path=manifest,
                    metadata_path=metadata,
                    backend_factory=lambda unused_bytes, unused_manifest: called.append(True),
                    module_model="NVIDIA Jetson Nano Developer Kit",
                )
            self.assertEqual(called, [])
            if os.name != "nt":
                bad["onnx_opset"] = 13
                with open(metadata, "w", encoding="utf-8") as handle:
                    json.dump(bad, handle)
                os.chmod(metadata, 0o600)
                os.chmod(engine, 0o644)
                with self.assertRaisesRegex(ValueError, "invalid_engine_permissions"):
                    TensorRtYolo(
                        engine,
                        manifest_path=manifest,
                        metadata_path=metadata,
                        backend_factory=lambda unused_bytes, unused_manifest: called.append(True),
                        module_model="NVIDIA Jetson Nano Developer Kit",
                    )
                self.assertEqual(called, [])

    def test_infer_uses_fixed_bgr_letterbox_and_fake_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = os.path.join(directory, "model.engine")
            manifest = os.path.join(directory, "model-manifest.json")
            metadata = engine + ".json"
            engine_bytes = b"engine"
            with open(engine, "wb") as handle:
                handle.write(engine_bytes)
            os.chmod(engine, 0o600)
            with open(manifest, "w", encoding="utf-8") as handle:
                json.dump(MANIFEST, handle)
            generated = dict(MANIFEST)
            generated["jetson_module_model"] = "NVIDIA Jetson Nano Developer Kit"
            generated["engine_sha256"] = hashlib.sha256(engine_bytes).hexdigest()
            with open(metadata, "w", encoding="utf-8") as handle:
                json.dump(generated, handle)
            os.chmod(metadata, 0o600)
            output = np.zeros((1, 84, 8400), dtype=np.float32)
            output[0, 0:4, 0] = [320.0, 320.0, 100.0, 100.0]
            output[0, 4 + 15, 0] = 0.8
            backend = FakeBackend(output)
            detector = TensorRtYolo(
                engine,
                manifest_path=manifest,
                metadata_path=metadata,
                backend_factory=lambda unused_bytes, unused_manifest: backend,
                module_model="NVIDIA Jetson Nano Developer Kit",
            )
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            frame[0, 0] = [1, 2, 3]

            result = detector.infer(frame)

            tensor = backend.inputs[0]
            self.assertEqual(tensor.shape, (1, 3, 640, 640))
            self.assertTrue(np.allclose(tensor[0, :, 80, 0], (3 / 255.0, 2 / 255.0, 1 / 255.0)))
            self.assertTrue(np.allclose(tensor[0, :, 0, 0], 114 / 255.0))
            self.assertEqual(result[0]["detected_type"], "cat")
            with self.assertRaisesRegex(ValueError, "invalid_frame"):
                detector.infer(np.zeros((640, 480, 3), dtype=np.uint8))


if __name__ == "__main__":
    unittest.main()
