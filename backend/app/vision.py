from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from numbers import Real
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol

import numpy as np

from app.contracts import CameraDetectionIn


FRAME_SHAPE = (480, 640, 3)
CLASS_ORDER = ("person", "dog", "cat")
SUBJECTS = {"person": None, "dog": "dog_001", "cat": "cat_001"}
MODEL_BYTES = 5_613_764
MODEL_SHA256 = "0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1"


class CameraUnavailable(RuntimeError):
    pass


class InvalidFrame(CameraUnavailable):
    pass


class FrameSource(Protocol):
    def read(self) -> np.ndarray: ...


def _valid_frame(frame: object) -> np.ndarray:
    if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.shape != FRAME_SHAPE:
        raise InvalidFrame("invalid_frame_shape")
    return frame


class MockFrameSource:
    def __init__(self, frame: np.ndarray):
        self.frame = frame

    def read(self) -> np.ndarray:
        return _valid_frame(self.frame)


class FileFrameSource:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read(self) -> np.ndarray:
        import cv2

        try:
            frame = cv2.imdecode(np.fromfile(self.path, dtype=np.uint8), cv2.IMREAD_COLOR)
        except (OSError, cv2.error) as error:
            raise CameraUnavailable("frame_unavailable") from error
        if frame is None:
            raise CameraUnavailable("frame_unavailable")
        return _valid_frame(frame)


class UsbFrameSource:
    def __init__(self, index: int = 0, capture_factory: Callable[[int], object] | None = None):
        if capture_factory is None:
            import cv2

            capture_factory = cv2.VideoCapture
        self.capture = capture_factory(index)
        self.capture.set(3, 640)
        self.capture.set(4, 480)

    def read(self) -> np.ndarray:
        ok, frame = self.capture.read()
        if not ok or frame is None:
            raise CameraUnavailable("frame_unavailable")
        return _valid_frame(frame)

    def close(self) -> None:
        self.capture.release()


class YoloDetector:
    def __init__(self, model_path: str | Path):
        model_path = Path(model_path)
        if not model_path.is_file():
            raise CameraUnavailable("model_unavailable")
        if model_path.stat().st_size != MODEL_BYTES:
            raise CameraUnavailable("model_invalid")
        digest = hashlib.sha256()
        with model_path.open("rb") as model:
            for chunk in iter(lambda: model.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().upper() != MODEL_SHA256:
            raise CameraUnavailable("model_invalid")
        from ultralytics import YOLO

        self.model = YOLO(str(model_path))

    def __call__(self, frame: np.ndarray) -> list[dict[str, object]]:
        results = self.model.predict(source=frame, device="cpu", save=False, verbose=False)
        detections: list[dict[str, object]] = []
        for result in results:
            names = result.names
            for box in result.boxes:
                class_id = int(box.cls[0].item())
                detections.append(
                    {
                        "detected_type": names[class_id],
                        "confidence": float(box.conf[0].item()),
                        "xyxy": tuple(float(value) for value in box.xyxy[0].tolist()),
                    }
                )
        return detections


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def normalize_detections(candidates: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    normalized: dict[str, list[dict[str, object]]] = {name: [] for name in CLASS_ORDER}
    for candidate in candidates:
        detected_type = candidate.get("detected_type")
        confidence = _number(candidate.get("confidence"))
        xyxy = candidate.get("xyxy")
        if detected_type not in normalized or confidence is None or not 0 <= confidence <= 1:
            continue
        if not isinstance(xyxy, (list, tuple)) or len(xyxy) != 4:
            continue
        coordinates = tuple(_number(value) for value in xyxy)
        if any(value is None for value in coordinates):
            continue
        x1, y1, x2, y2 = coordinates
        if not (x1 < x2 and y1 < y2):
            continue
        x1, x2 = min(640.0, max(0.0, x1)), min(640.0, max(0.0, x2))
        y1, y2 = min(480.0, max(0.0, y1)), min(480.0, max(0.0, y2))
        left, top, right, bottom = math.floor(x1), math.floor(y1), math.ceil(x2), math.ceil(y2)
        if right <= left or bottom <= top:
            continue
        normalized[detected_type].append(
            {
                "detected_type": detected_type,
                "subject_id": SUBJECTS[detected_type],
                "confidence": confidence,
                "bbox_x": left,
                "bbox_y": top,
                "bbox_width": right - left,
                "bbox_height": bottom - top,
                "center_x": math.floor((left + right) / 2),
                "center_y": math.floor((top + bottom) / 2),
            }
        )

    selected = []
    for detected_type in CLASS_ORDER:
        choices = normalized[detected_type]
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


def _zone_tuple(zone: object) -> tuple[int, int, int, int]:
    if isinstance(zone, (list, tuple)):
        return tuple(zone)  # type: ignore[return-value]
    return zone.x1, zone.y1, zone.x2, zone.y2  # type: ignore[attr-defined]


def zone_for_center(x: int, y: int, zones: Mapping[str, object]) -> str | None:
    for name, zone in zones.items():
        x1, y1, x2, y2 = _zone_tuple(zone)
        if x1 <= x < x2 and y1 <= y < y2:
            return name
    return None


def bbox_overlaps_zone(detection: Mapping[str, object] | CameraDetectionIn, zone: object) -> bool:
    def value(name: str) -> int:
        if isinstance(detection, Mapping):
            return int(detection[name])
        return getattr(detection, name)

    x1, y1, x2, y2 = _zone_tuple(zone)
    left, top = value("bbox_x"), value("bbox_y")
    return left < x2 and x1 < left + value("bbox_width") and top < y2 and y1 < top + value("bbox_height")


@dataclass(frozen=True)
class ProcessedFrame:
    jpeg: bytes
    detections: list[CameraDetectionIn]
    fps: float
    inference_ms: float
    observed_at: datetime
    bed_subject_ids: tuple[str, ...]
    selected_bed_subject_id: str | None

    def is_fresh(self, now: datetime) -> bool:
        return timedelta(0) <= now - self.observed_at <= timedelta(seconds=3)


class VisionPipeline:
    def __init__(
        self,
        detector: Callable[[np.ndarray], Iterable[Mapping[str, object]]],
        zones: Mapping[str, object],
        *,
        source: FrameSource | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.detector = detector
        self.zones = zones
        self.source = source
        self.now = now or (lambda: datetime.now(UTC))
        self.latest_frame: ProcessedFrame | None = None
        self.camera_state: tuple[str, str | None] = ("offline", "not_started")
        self._last_finished: float | None = None

    def process(self, frame: np.ndarray, observed_at: datetime) -> ProcessedFrame:
        try:
            frame = _valid_frame(frame)
        except InvalidFrame:
            self.camera_state = ("offline", "invalid_frame_shape")
            raise
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        observed_at = observed_at.astimezone(UTC)

        started = time.perf_counter()
        try:
            selected = normalize_detections(self.detector(frame))
        except Exception:
            self.camera_state = ("offline", "inference_failed")
            raise
        inference_finished = time.perf_counter()

        detections = [
            CameraDetectionIn(
                camera_id="pc-webcam-01",
                zone_name=zone_for_center(item["center_x"], item["center_y"], self.zones),
                observed_at=observed_at,
                **item,
            )
            for item in selected
        ]
        bed = [item for item in detections if item.subject_id is not None and item.zone_name == "pet_bed"]
        bed_subject_ids = tuple(subject for subject in ("dog_001", "cat_001") if any(item.subject_id == subject for item in bed))
        selected_bed_subject_id = None
        if bed:
            selected_bed_subject_id = min(
                bed,
                key=lambda item: (-item.confidence, 0 if item.subject_id == "dog_001" else 1),
            ).subject_id

        import cv2

        annotated = frame.copy()
        for name, zone in self.zones.items():
            x1, y1, x2, y2 = _zone_tuple(zone)
            cv2.rectangle(annotated, (x1, y1), (x2 - 1, y2 - 1), (100, 100, 100), 1)
            cv2.putText(annotated, name, (x1, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (100, 100, 100), 1)
        for item in detections:
            right = item.bbox_x + item.bbox_width - 1
            bottom = item.bbox_y + item.bbox_height - 1
            cv2.rectangle(annotated, (item.bbox_x, item.bbox_y), (right, bottom), (0, 255, 0), 2)
            cv2.putText(
                annotated,
                f"{item.detected_type} {item.confidence:.2f}",
                (item.bbox_x, max(12, item.bbox_y - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (0, 255, 0),
                1,
            )
        encoded, buffer = cv2.imencode(".jpg", annotated)
        if not encoded:
            self.camera_state = ("offline", "jpeg_failed")
            raise CameraUnavailable("jpeg_failed")

        finished = time.perf_counter()
        fps = 0.0 if self._last_finished is None or finished <= self._last_finished else 1 / (finished - self._last_finished)
        result = ProcessedFrame(
            jpeg=buffer.tobytes(),
            detections=detections,
            fps=fps,
            inference_ms=(inference_finished - started) * 1000,
            observed_at=observed_at,
            bed_subject_ids=bed_subject_ids,
            selected_bed_subject_id=selected_bed_subject_id,
        )
        self._last_finished = finished
        self.latest_frame = result
        self.camera_state = ("online", None)
        return result

    def run_vision(self, stop_event: object) -> None:
        if self.source is None:
            raise CameraUnavailable("source_unavailable")
        while not stop_event.is_set():
            try:
                self.process(self.source.read(), self.now())
            except CameraUnavailable as error:
                reason = str(error) or "camera_unavailable"
                self.camera_state = ("offline", reason)
                wait = getattr(stop_event, "wait", None)
                if wait is not None:
                    wait(0.05)
