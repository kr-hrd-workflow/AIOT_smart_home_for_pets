from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from app.vision import (
    CameraUnavailable,
    FileFrameSource,
    InvalidFrame,
    MockFrameSource,
    UsbFrameSource,
    VisionPipeline,
    bbox_overlaps_zone,
    normalize_detections,
    zone_for_center,
)


NOW = datetime(2026, 7, 15, tzinfo=UTC)
FRAME = np.zeros((480, 640, 3), dtype=np.uint8)
ZONES = {
    "food_bowl": (40, 260, 260, 470),
    "pet_bed": (320, 180, 630, 470),
}


@pytest.fixture
def vision_scratch():
    path = Path(__file__).parents[2] / ".runtime" / "vision-smoke" / "unit"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        path.parent.rmdir()


class Detector:
    def __init__(self, detections=()):
        self.detections = detections
        self.calls = 0

    def __call__(self, frame):
        self.calls += 1
        return self.detections


def test_all_sources_require_exact_uint8_bgr_shape(vision_scratch, monkeypatch):
    assert MockFrameSource(FRAME).read() is FRAME

    image = vision_scratch / "frame.png"
    import cv2

    encoded, buffer = cv2.imencode(".png", FRAME)
    assert encoded
    image.write_bytes(buffer.tobytes())
    assert FileFrameSource(image).read().shape == (480, 640, 3)

    wrong_image = vision_scratch / "wrong.png"
    encoded, buffer = cv2.imencode(".png", np.zeros((10, 10, 3), dtype=np.uint8))
    assert encoded
    wrong_image.write_bytes(buffer.tobytes())
    with pytest.raises(InvalidFrame, match="invalid_frame_shape"):
        FileFrameSource(wrong_image).read()
    monkeypatch.setattr(cv2, "imdecode", lambda *_: np.zeros((480, 640, 3), dtype=np.float32))
    with pytest.raises(InvalidFrame, match="invalid_frame_shape"):
        FileFrameSource(image).read()

    class Capture:
        def __init__(self, frame):
            self.frame = frame
            self.settings = []

        def set(self, prop, value):
            self.settings.append((prop, value))
            return True

        def read(self):
            return True, self.frame

        def release(self):
            pass

    capture = Capture(FRAME)
    assert UsbFrameSource(capture_factory=lambda _: capture).read() is FRAME
    assert {value for _, value in capture.settings} == {640, 480}

    invalid = [
        np.zeros((480, 640, 3), dtype=np.float32),
        np.zeros((640, 480, 3), dtype=np.uint8),
        np.zeros((480, 640), dtype=np.uint8),
    ]
    for frame in invalid:
        for source in (
            MockFrameSource(frame),
            UsbFrameSource(capture_factory=lambda _, frame=frame: Capture(frame)),
        ):
            with pytest.raises(InvalidFrame, match="invalid_frame_shape"):
                source.read()


def test_invalid_frame_stops_before_detector_annotation_jpeg_or_latest_frame(monkeypatch):
    import cv2

    monkeypatch.setattr(cv2, "imencode", lambda *_: pytest.fail("JPEG must not run"))
    monkeypatch.setattr(cv2, "rectangle", lambda *_: pytest.fail("annotation must not run"))
    detector = Detector()
    pipeline = VisionPipeline(detector, ZONES)
    with pytest.raises(InvalidFrame, match="invalid_frame_shape"):
        pipeline.process(np.zeros((10, 10, 3), dtype=np.uint8), NOW)
    assert detector.calls == 0
    assert pipeline.latest_frame is None
    assert pipeline.camera_state == ("offline", "invalid_frame_shape")


def test_naive_timestamp_stops_before_inference():
    detector = Detector()
    pipeline = VisionPipeline(detector, ZONES)
    with pytest.raises(ValueError, match="timezone-aware"):
        pipeline.process(FRAME, datetime(2026, 7, 15))
    assert detector.calls == 0

    result = pipeline.process(FRAME, datetime(2026, 7, 15, 9, tzinfo=timezone(timedelta(hours=9))))
    assert result.observed_at.tzinfo is UTC


def test_normalization_rejects_invalid_and_uses_clamp_floor_ceil():
    candidates = [
        {"detected_type": "dog", "confidence": 0.8, "xyxy": [-2.2, -3.8, 640.4, 480.9]},
        {"detected_type": "cat", "confidence": float("nan"), "xyxy": [1, 2, 3, 4]},
        {"detected_type": "cat", "confidence": 0.5, "xyxy": [4, 4, 3, 5]},
        {"detected_type": "person", "confidence": 0.5, "xyxy": [-10, 1, -1, 2]},
        {"detected_type": "person", "confidence": 0.5, "xyxy": [640, 1, 641, 2]},
        {"detected_type": "person", "confidence": 0.5, "xyxy": [1, 480, 2, 481]},
        {"detected_type": "bird", "confidence": 0.99, "xyxy": [1, 2, 3, 4]},
        {"detected_type": "cat", "confidence": 1.1, "xyxy": [1, 2, 3, 4]},
        {"detected_type": "cat", "confidence": 0.7, "xyxy": [1, 2, float("inf"), 4]},
    ]
    selected = normalize_detections(candidates)
    assert selected == [
        {
            "detected_type": "dog",
            "subject_id": "dog_001",
            "confidence": 0.8,
            "bbox_x": 0,
            "bbox_y": 0,
            "bbox_width": 640,
            "bbox_height": 480,
            "center_x": 320,
            "center_y": 240,
        }
    ]


def test_fractional_box_uses_floor_ceil_width_and_center():
    assert normalize_detections(
        [{"detected_type": "dog", "confidence": 0.5, "xyxy": [10.2, 20.8, 30.1, 40.2]}]
    ) == [
        {
            "detected_type": "dog",
            "subject_id": "dog_001",
            "confidence": 0.5,
            "bbox_x": 10,
            "bbox_y": 20,
            "bbox_width": 21,
            "bbox_height": 21,
            "center_x": 20,
            "center_y": 30,
        }
    ]


def test_selection_ranks_normalized_boxes_and_collapses_identical_facts():
    selected = normalize_detections(
        [
            {"detected_type": "dog", "confidence": 0.8, "xyxy": [10.2, 10.2, 20.1, 20.1]},
            {"detected_type": "dog", "confidence": 0.8, "xyxy": [9.9, 10.2, 19.9, 20.1]},
            {"detected_type": "cat", "confidence": 0.7, "xyxy": [30.1, 30.1, 40.1, 40.1]},
            {"detected_type": "cat", "confidence": 0.7, "xyxy": [30.8, 30.8, 40.0, 40.0]},
            {"detected_type": "cat", "confidence": 0.7, "xyxy": [30.2, 30.2, 40.0, 40.0]},
        ]
    )
    assert [(d["detected_type"], d["bbox_x"], d["bbox_width"]) for d in selected] == [
        ("dog", 9, 11),
        ("cat", 30, 10),
    ]


@pytest.mark.parametrize(
    ("point", "expected"),
    [((40, 260), "food_bowl"), ((259, 469), "food_bowl"), ((260, 260), None), ((320, 180), "pet_bed"), ((630, 470), None)],
)
def test_zone_membership_is_half_open(point, expected):
    assert zone_for_center(*point, ZONES) == expected


def test_shared_right_and_bottom_edges_belong_only_to_adjacent_zone():
    zones = {"top_left": (0, 0, 10, 10), "right": (10, 0, 20, 10), "bottom": (0, 10, 10, 20)}
    assert zone_for_center(10, 5, zones) == "right"
    assert zone_for_center(5, 10, zones) == "bottom"


def test_calibration_overlap_requires_positive_area():
    zone = ZONES["pet_bed"]
    assert bbox_overlaps_zone({"bbox_x": 319, "bbox_y": 179, "bbox_width": 2, "bbox_height": 2}, zone)
    assert not bbox_overlaps_zone({"bbox_x": 310, "bbox_y": 180, "bbox_width": 10, "bbox_height": 20}, zone)
    assert not bbox_overlaps_zone({"bbox_x": 320, "bbox_y": 170, "bbox_width": 20, "bbox_height": 10}, zone)


def test_whole_frame_selection_precedes_zone_and_bed_subject_choice():
    candidates = json.loads((Path(__file__).parent / "fixtures" / "detections.json").read_text())
    result = VisionPipeline(Detector(candidates), ZONES).process(FRAME, NOW)
    assert [d.detected_type for d in result.detections] == ["person", "dog", "cat"]
    assert next(d for d in result.detections if d.detected_type == "dog").zone_name is None
    assert result.bed_subject_ids == ("cat_001",)
    assert result.selected_bed_subject_id == "cat_001"


def test_bed_subjects_are_ordered_and_confidence_tie_prefers_dog():
    detections = [
        {"detected_type": "cat", "confidence": 0.9, "xyxy": [330, 190, 400, 300]},
        {"detected_type": "dog", "confidence": 0.9, "xyxy": [340, 200, 410, 310]},
    ]
    result = VisionPipeline(Detector(detections), ZONES).process(FRAME, NOW)
    assert result.bed_subject_ids == ("dog_001", "cat_001")
    assert result.selected_bed_subject_id == "dog_001"


def test_jpeg_freshness_detector_failure_and_memory_only(monkeypatch):
    detector = Detector([{"detected_type": "dog", "confidence": 1.0, "xyxy": [320, 180, 630, 470]}])
    pipeline = VisionPipeline(detector, ZONES)
    writes = []
    monkeypatch.setattr(Path, "write_bytes", lambda *args, **kwargs: writes.append(args))
    monkeypatch.setattr(Path, "write_text", lambda *args, **kwargs: writes.append(args))
    result = pipeline.process(FRAME, NOW)
    assert result.jpeg.startswith(b"\xff\xd8")
    assert result.is_fresh(NOW)
    assert result.is_fresh(NOW + timedelta(seconds=3))
    assert not result.is_fresh(NOW + timedelta(seconds=3, microseconds=1))
    assert not result.is_fresh(NOW - timedelta(microseconds=1))
    assert result.fps >= 0 and result.inference_ms >= 0
    assert writes == []

    class BrokenDetector:
        def __call__(self, frame):
            raise RuntimeError("detector failed")

    broken = VisionPipeline(BrokenDetector(), ZONES)
    with pytest.raises(RuntimeError, match="detector failed"):
        broken.process(FRAME, NOW)
    assert broken.latest_frame is None
    assert broken.camera_state == ("offline", "inference_failed")


def test_run_vision_updates_single_latest_slot():
    class Stop:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 2

    pipeline = VisionPipeline(Detector(), ZONES, source=MockFrameSource(FRAME), now=lambda: NOW)
    pipeline.run_vision(Stop())
    assert pipeline.latest_frame is not None
    assert pipeline.latest_frame.observed_at == NOW


def test_file_source_io_failure_is_controlled_before_inference(vision_scratch, monkeypatch):
    missing = vision_scratch / "missing.png"
    with pytest.raises(CameraUnavailable, match="frame_unavailable"):
        FileFrameSource(missing).read()

    monkeypatch.setattr(np, "fromfile", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(CameraUnavailable, match="frame_unavailable"):
        FileFrameSource(missing).read()

    class Stop:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 1

    detector = Detector()
    pipeline = VisionPipeline(detector, ZONES, source=FileFrameSource(missing), now=lambda: NOW)
    pipeline.run_vision(Stop())
    assert detector.calls == 0
    assert pipeline.latest_frame is None
    assert pipeline.camera_state == ("offline", "frame_unavailable")


def test_default_runtime_clock_is_timezone_aware():
    class Stop:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 1

    pipeline = VisionPipeline(Detector(), ZONES, source=MockFrameSource(FRAME))
    pipeline.run_vision(Stop())
    assert pipeline.latest_frame.observed_at.utcoffset() is not None


def test_yolo_adapter_rejects_missing_or_tampered_model_before_import(monkeypatch):
    import builtins

    from app.vision import YoloDetector

    imported = []
    original_import = builtins.__import__

    def record_import(name, *args, **kwargs):
        if name.startswith("ultralytics"):
            imported.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", record_import)
    model_dir = Path(__file__).parents[2] / ".runtime" / "models" / "adapter-test"
    shutil.rmtree(model_dir, ignore_errors=True)
    model_dir.mkdir(parents=True)
    try:
        with pytest.raises(Exception, match="model_unavailable"):
            YoloDetector(model_dir / "missing.pt")
        tampered = model_dir / "yolo11n.pt"
        tampered.write_bytes(b"wrong")
        with pytest.raises(Exception, match="model_invalid"):
            YoloDetector(tampered)
        assert imported == []
    finally:
        shutil.rmtree(model_dir, ignore_errors=True)
