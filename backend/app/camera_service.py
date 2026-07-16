from __future__ import annotations

from collections import deque
from datetime import datetime
from threading import Event, Lock, Thread
from typing import Callable

from sqlalchemy.orm import Session

from .contracts import CameraStatus
from .events import CameraFrameCommitted
from .models import Camera, CameraEvent
from .rule_ingress import IngressTicket, RuleIngress
from .vision import CameraUnavailable, ProcessedFrame, VisionPipeline


CAMERA_ID = "pc-webcam-01"
MJPEG_PREFIX = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
MJPEG_SUFFIX = b"\r\n"
AVAILABILITY_SLOTS = 61


class CameraService:
    def __init__(
        self,
        pipeline: VisionPipeline | None,
        ingress: RuleIngress | None,
        session_factory: Callable[[], Session] | None,
    ) -> None:
        self.pipeline = pipeline
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
    def latest_frame(self) -> ProcessedFrame | None:
        with self._lock:
            return self._latest_frame

    @property
    def status(self) -> CameraStatus:
        with self._lock:
            return self._status.model_copy()

    def start(self) -> None:
        if self.pipeline is None or self._worker is not None:
            return
        self._worker = Thread(target=self._run, name="petcare-camera", daemon=False)
        self._worker.start()

    def process_once(self) -> bool:
        if self.pipeline is None or self._ingress is None or self._session_factory is None:
            return False
        ticket = self._ingress.begin("camera")
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
            processed = self.pipeline.process(frame, ticket.received_at_utc)
        except CameraUnavailable as error:
            self._fail_frame(ticket, str(error) or "camera_unavailable")
            return False
        except Exception:
            self._fail_frame(ticket, self.pipeline.camera_state[1] or "inference_failed")
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
        if frame is None or self.status.state != "online":
            raise CameraUnavailable("camera_unavailable")
        return MJPEG_PREFIX + frame.jpeg + MJPEG_SUFFIX

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_once()
            except RuntimeError:
                if self._stop.is_set():
                    break
            if self.status.state == "offline":
                self._stop.wait(0.05)

    def shutdown(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join()
        if self.pipeline is not None and self.pipeline.source is not None:
            close = getattr(self.pipeline.source, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
