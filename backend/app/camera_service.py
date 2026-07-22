from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import AppConfig
from .contracts import CameraDetectionIn, CameraStatus
from .events import CameraFrameCommitted
from .jetson_client import JetsonVisionClient
from .models import Camera, CameraEvent, Zone
from .rule_ingress import IngressTicket, RuleIngress
from .vision import CameraUnavailable, FileFrameSource, ProcessedFrame, UsbFrameSource, VisionPipeline, YoloDetector


CAMERA_ID = "pc-webcam-01"
MJPEG_PREFIX = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
MJPEG_SUFFIX = b"\r\n"
AVAILABILITY_SLOTS = 61


def build_camera_service(
    config: AppConfig,
    ingress: RuleIngress,
    session_factory: Callable[[], Session],
) -> CameraService:
    if config.camera_source == "disabled":
        return CameraService.disabled()
    session = session_factory()
    try:
        zones = {
            row.zone_name: (row.x1, row.y1, row.x2, row.y2)
            for row in session.execute(select(Zone).where(Zone.enabled.is_(True))).scalars()
        }
    finally:
        session.close()
    if config.camera_source == "jetson":
        assert config.jetson_config is not None
        return CameraService(None, ingress, session_factory, JetsonVisionClient(config.jetson_config), zones)
    source = (
        FileFrameSource(config.camera_file_path)
        if config.camera_source == "file"
        else UsbFrameSource(config.camera_index)
    )
    pipeline = VisionPipeline(YoloDetector(config.camera_model_path), zones, source=source)
    return CameraService(pipeline, ingress, session_factory)


class CameraService:
    def __init__(
        self,
        pipeline: VisionPipeline | None,
        ingress: RuleIngress | None,
        session_factory: Callable[[], Session] | None,
        jetson_client: JetsonVisionClient | None = None,
        zones: dict[str, tuple[int, int, int, int]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.pipeline = pipeline
        self._jetson_client = jetson_client
        self._zones = zones or {}
        self._now = now or (lambda: datetime.now(UTC))
        self._ingress = ingress
        self._session_factory = session_factory
        self._stop = Event()
        self._worker: Thread | None = None
        self._lock = Lock()
        self._latest_frame: ProcessedFrame | None = None
        self._availability: deque[int] = deque(maxlen=AVAILABILITY_SLOTS)
        self._status = CameraStatus(
            state="offline",
            fps=0.0,
            inference_ms=0.0,
            last_frame_at=None,
            reason="not_started",
        )

    @classmethod
    def disabled(cls) -> CameraService:
        return cls(None, None, None)

    @property
    def worker(self) -> Thread | None:
        return self._worker

    @property
    def jetson_client(self) -> JetsonVisionClient | None:
        return self._jetson_client

    @property
    def latest_frame(self) -> ProcessedFrame | None:
        with self._lock:
            return self._latest_frame

    @property
    def status(self) -> CameraStatus:
        with self._lock:
            return self._status.model_copy()

    def start(self) -> None:
        if (self.pipeline is None and self._jetson_client is None) or self._worker is not None:
            return
        self._worker = Thread(target=self._run, name="petcare-camera", daemon=False)
        self._worker.start()

    def process_once(self) -> bool:
        if self.pipeline is None or self._ingress is None or self._session_factory is None:
            if self._jetson_client is None or self._ingress is None or self._session_factory is None:
                return False
        ticket = self._ingress.begin("camera")
        if self._jetson_client is not None:
            self._expire_remote_status()
            try:
                processed = self._jetson_client.next_frame(self._zones)
            except CameraUnavailable as error:
                self._fail_frame(ticket, str(error) or "camera_unavailable")
                return False
            except Exception:
                self._fail_frame(ticket, "camera_error")
                return False
        else:
            assert self.pipeline is not None
            if self.pipeline.source is None:
                self._fail_frame(ticket, "source_unavailable")
                return False
            try:
                frame = self.pipeline.source.read()
            except CameraUnavailable as error:
                reason = str(error) or "camera_unavailable"
                self._fail_frame(ticket, reason)
                return False
            except Exception:
                self._fail_frame(ticket, "camera_error")
                return False
            try:
                observed_at = ticket.received_at_utc
                latest = self.latest_frame
                if latest is not None and observed_at <= latest.observed_at:
                    observed_at = latest.observed_at + timedelta(microseconds=1)
                processed = self.pipeline.process(frame, observed_at)
            except CameraUnavailable as error:
                self._fail_frame(ticket, str(error) or "camera_unavailable")
                return False
            except Exception:
                self._fail_frame(ticket, self.pipeline.camera_state[1] or "inference_failed")
                return False
        if self._jetson_client is not None and not processed.is_fresh(self._now()):
            self._fail_frame(ticket, "stale_observation")
            return False
        try:
            event = self._persist_frame(processed)
        except Exception:
            self._fail_frame(ticket, "database_rollback")
            return False

        with self._lock:
            slot = int(processed.observed_at.timestamp())
            if not self._availability or self._availability[-1] != slot:
                self._availability.append(slot)
            self._latest_frame = processed
            self._status = CameraStatus(
                state="online",
                fps=processed.fps,
                inference_ms=processed.inference_ms,
                last_frame_at=processed.observed_at,
                reason=None,
            )
        self._ingress.resolve_committed(ticket, event)
        return True

    def _expire_remote_status(self) -> None:
        transitioned = False
        with self._lock:
            if (
                self._status.state == "online"
                and self._status.last_frame_at is not None
                and not timedelta(0) <= self._now() - self._status.last_frame_at <= timedelta(seconds=3)
            ):
                self._status = CameraStatus(
                    state="offline",
                    fps=self._status.fps,
                    inference_ms=self._status.inference_ms,
                    last_frame_at=self._status.last_frame_at,
                    reason="observation_timeout",
                )
                transitioned = True
        if transitioned:
            self._persist_offline()

    def _persist_frame(self, processed: ProcessedFrame) -> CameraFrameCommitted:
        assert self._session_factory is not None
        session: Session | None = None
        try:
            session = self._session_factory()
            camera = session.get(Camera, CAMERA_ID)
            if camera is None:
                camera = Camera(camera_id=CAMERA_ID)
                session.add(camera)
            camera.status = "online"
            camera.last_frame_at = processed.observed_at
            camera.updated_at = processed.observed_at
            rows = [CameraEvent(**detection.model_dump()) for detection in processed.detections]
            for row in rows:
                session.add(row)
            session.flush()
            event = CameraFrameCommitted(
                camera_id=CAMERA_ID,
                observed_at=processed.observed_at,
                detection_ids=tuple(row.id for row in rows),
                bed_subject_ids=processed.bed_subject_ids,
                selected_bed_subject_id=processed.selected_bed_subject_id,
            )
            session.commit()
            return event
        except Exception:
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    pass
            raise
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

    def _fail_frame(self, ticket: IngressTicket, reason: str) -> None:
        previous = self.status
        remote_still_fresh = (
            self._jetson_client is not None
            and previous.state == "online"
            and previous.last_frame_at is not None
            and timedelta(0) <= self._now() - previous.last_frame_at <= timedelta(seconds=3)
        )
        if not remote_still_fresh:
            with self._lock:
                self._status = CameraStatus(
                    state="offline",
                    fps=previous.fps,
                    inference_ms=previous.inference_ms,
                    last_frame_at=previous.last_frame_at,
                    reason=reason,
                )
        assert self._ingress is not None
        try:
            if not remote_still_fresh and (self._jetson_client is None or previous.state != "offline"):
                self._persist_offline()
        finally:
            self._ingress.resolve_tombstone(ticket, reason)

    def _persist_offline(self) -> None:
        assert self._session_factory is not None
        session: Session | None = None
        try:
            session = self._session_factory()
            camera = session.get(Camera, CAMERA_ID)
            if camera is None:
                camera = Camera(camera_id=CAMERA_ID)
                session.add(camera)
            camera.status = "offline"
            session.commit()
        except Exception:
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    pass
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

    def available_for(self, start_exclusive: datetime, end_inclusive: datetime) -> bool:
        if start_exclusive.tzinfo is None or end_inclusive.tzinfo is None or end_inclusive <= start_exclusive:
            return False
        start_slot = int(start_exclusive.timestamp())
        end_slot = int(end_inclusive.timestamp())
        with self._lock:
            available = set(self._availability)
        return all(slot in available for slot in range(start_slot + 1, end_slot + 1))

    def mjpeg_chunk(self) -> bytes:
        with self._lock:
            frame = self._latest_frame
            online = self._status.state == "online"
            fresh = frame is not None and (self._jetson_client is None or frame.is_fresh(self._now()))
        if frame is None or not online or not fresh:
            raise CameraUnavailable("camera_unavailable")
        return MJPEG_PREFIX + frame.jpeg + MJPEG_SUFFIX

    def _wait_remote_retry(self, retry_seconds: float) -> None:
        status = self.status
        if status.state == "online" and status.last_frame_at is not None:
            remaining = 3 - (self._now() - status.last_frame_at).total_seconds()
            if remaining < 0:
                self._expire_remote_status()
            elif remaining < retry_seconds:
                before_expiry = min(retry_seconds, max(0.001, remaining + 0.001))
                if self._stop.wait(before_expiry):
                    return
                self._expire_remote_status()
                self._stop.wait(retry_seconds - before_expiry)
                return
        self._stop.wait(retry_seconds)

    def _run(self) -> None:
        retry_seconds = 1.0
        while not self._stop.is_set():
            try:
                succeeded = self.process_once()
            except RuntimeError:
                if self._stop.is_set():
                    break
                succeeded = False
            if self._jetson_client is not None:
                if succeeded:
                    retry_seconds = 1.0
                else:
                    self._wait_remote_retry(retry_seconds)
                    retry_seconds = min(retry_seconds * 2, 30.0)
            elif self.status.state == "offline":
                self._stop.wait(0.05)

    def shutdown(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(16.0 if self._jetson_client is not None else None)
            if self._worker.is_alive():
                raise RuntimeError("camera worker did not stop")
        if self.pipeline is not None and self.pipeline.source is not None:
            close = getattr(self.pipeline.source, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
