from __future__ import annotations

import hashlib
import os
import secrets
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .agent_config import protect_runtime_file
from .config import JetsonConfig
from .contracts import CameraDetectionIn
from .jetson_contracts import (
    COMMAND_ID,
    MAX_CLIP_BYTES,
    MAX_JSON_BYTES,
    MAX_PREVIEW_BYTES,
    STATUS_BY_CODE,
    ClockCalibration,
    JetsonClipCommand,
    JetsonClipHeaders,
    JetsonClipReceipt,
    JetsonPutResult,
    JetsonError,
    JetsonObservation,
    JetsonStatus,
    b64url,
    canonical_json,
    canonical_query,
    canonical_utc,
    parse_observation,
    sign_request,
    strict_json,
)
from .vision import CameraUnavailable, ProcessedFrame, SUBJECTS, zone_for_center


class JetsonClientError(CameraUnavailable):
    pass


def pinned_ssl_context(ca_pem: bytes) -> ssl.SSLContext:
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cadata=ca_pem.decode("ascii"))
        return context
    except (UnicodeDecodeError, ssl.SSLError) as error:
        raise ValueError("invalid Jetson CA certificate") from error


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class JetsonVisionClient:
    def __init__(
        self,
        config: JetsonConfig,
        *,
        clients: tuple[httpx.Client, httpx.Client, httpx.Client] | None = None,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        now: Callable[[], datetime] | None = None,
        now_seconds: Callable[[], float] | None = None,
        monotonic: Callable[[], float] | None = None,
        nonce: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(config, JetsonConfig):
            raise TypeError("config must be JetsonConfig")
        self.config = config
        self._secret = config.psk
        self._now = now or (lambda: datetime.now(UTC))
        self._wall = now_seconds or (lambda: self._now().timestamp())
        self._monotonic = monotonic or time.monotonic
        self._nonce = nonce or (lambda: b64url(secrets.token_bytes(16)))
        if clients is None:
            context = pinned_ssl_context(config.ca_pem)
            created: list[httpx.Client] = []
            try:
                for _ in range(3):
                    created.append(client_factory(
                    base_url=config.url,
                    verify=context,
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                    timeout=httpx.Timeout(2.0, connect=1.0),
                    ))
            except BaseException:
                for client in created:
                    try:
                        client.close()
                    except Exception:
                        pass
                raise
            clients = tuple(created)  # type: ignore[assignment]
        if type(clients) is not tuple or len(clients) != 3:
            raise ValueError("three Jetson clients required")
        self._control, self._admission, self._media = clients
        self._locks = tuple(threading.Lock() for _ in range(3))
        self._boot_id: str | None = None
        self._sequence = 0
        self._preview: bytes | None = None
        self._preview_monotonic: float | None = None
        self._guard_sample: tuple[float, float] | None = None
        self._guard_disabled_until = 0.0
        self._calibration: ClockCalibration | None = None

    @property
    def boot_id(self) -> str | None:
        return self._boot_id

    def close(self) -> None:
        first_error: BaseException | None = None
        for client in (self._control, self._admission, self._media):
            try:
                client.close()
            except BaseException as error:
                first_error = first_error or error
        if first_error is not None:
            raise first_error

    def _sample_guard(self, wall: float, monotonic_now: float) -> None:
        previous = self._guard_sample
        self._guard_sample = (wall, monotonic_now)
        if previous is not None and abs((wall - previous[0]) - (monotonic_now - previous[1])) > 0.025:
            self._guard_disabled_until = monotonic_now + 60.0
            self._calibration = None

    def _headers(
        self, method: str, target: str, body: bytes, *, boot_id: str | None = None, timestamp: float | None = None
    ) -> dict[str, str]:
        nonce = self._nonce()
        return sign_request(
            method=method,
            target=target,
            boot_id=boot_id or self._boot_id or "bootstrap",
            timestamp=str(int(self._wall() if timestamp is None else timestamp)),
            nonce=nonce,
            body=body,
            secret=self._secret,
        )

    def _send(
        self,
        client: httpx.Client,
        method: str,
        target: str,
        *,
        body: bytes = b"",
        timeout: float | None = None,
        deadline: float | None = None,
        boot_id: str | None = None,
        timestamp: float | None = None,
    ) -> httpx.Response:
        headers = self._headers(method, target, body, boot_id=boot_id, timestamp=timestamp)
        if body:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        operation_deadline = deadline or time.monotonic() + (timeout or 2.0)
        try:
            request = client.build_request(method, target, headers=headers, content=body)
            remaining = max(0.001, operation_deadline - time.monotonic())
            if timeout is not None or deadline is not None:
                request.extensions["timeout"] = {
                    "connect": min(1.0, remaining), "read": remaining, "write": min(2.0, remaining), "pool": min(1.0, remaining),
                }
            response = client.send(request, stream=True)
            response.extensions["petcare_deadline"] = operation_deadline
            return response
        except (httpx.HTTPError, OSError) as error:
            raise JetsonClientError("jetson_unavailable") from error

    @staticmethod
    def _bounded(response: httpx.Response, maximum: int, *, require_length: bool = False) -> bytes:
        raw_length = response.headers.get("content-length")
        if require_length and raw_length is None:
            response.close()
            raise JetsonClientError("missing_content_length")
        if raw_length is not None:
            try:
                length = int(raw_length)
            except ValueError as error:
                response.close()
                raise JetsonClientError("invalid_content_length") from error
            if length < 0 or length > maximum:
                response.close()
                raise JetsonClientError("response_too_large")
        content = bytearray()
        deadline = float(response.extensions.get("petcare_deadline", time.monotonic() + 2.0))
        expired = threading.Event()
        timer = threading.Timer(max(0.0, deadline - time.monotonic()), lambda: (expired.set(), response.close()))
        timer.daemon = True
        timer.start()
        try:
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if expired.is_set() or time.monotonic() > deadline:
                    raise JetsonClientError("response_timeout")
                if len(content) > maximum:
                    raise JetsonClientError("response_too_large")
        except httpx.HTTPError as error:
            raise JetsonClientError("response_timeout" if expired.is_set() else "jetson_unavailable") from error
        finally:
            timer.cancel()
            response.close()
        if raw_length is not None and len(content) != int(raw_length):
            raise JetsonClientError("content_length_mismatch")
        return bytes(content)

    def _json(self, response: httpx.Response, success: tuple[int, ...]) -> dict[str, Any]:
        content = self._bounded(response, MAX_JSON_BYTES)
        if response.status_code not in success:
            try:
                code = JetsonError.model_validate(strict_json(content)).code
            except (ValueError, TypeError):
                code = "invalid_response"
            if STATUS_BY_CODE.get(code) != response.status_code:
                code = "invalid_response"
            raise JetsonClientError(code)
        try:
            return strict_json(content)
        except ValueError as error:
            raise JetsonClientError("invalid_json") from error

    def _validate_unauthorized(self, response: httpx.Response) -> None:
        try:
            error = JetsonError.model_validate(strict_json(self._bounded(response, MAX_JSON_BYTES)))
        except (ValueError, TypeError) as parse_error:
            raise JetsonClientError("invalid_response") from parse_error
        if (
            response.status_code != 401
            or STATUS_BY_CODE.get(error.code) != response.status_code
            or error.code != "unauthorized"
            or error.message != "Unauthorized"
        ):
            raise JetsonClientError("invalid_response")

    def _status(
        self, *, timestamp: float | None = None, bootstrap: bool = False, deadline: float | None = None
    ) -> JetsonStatus:
        response = self._send(
            self._control, "GET", "/v1/status", boot_id="bootstrap" if bootstrap or self._boot_id is None else self._boot_id,
            timestamp=timestamp, deadline=deadline,
        )
        if response.status_code == 401 and not bootstrap:
            self._validate_unauthorized(response)
            return self._status(timestamp=timestamp, bootstrap=True, deadline=deadline)
        try:
            status = JetsonStatus.model_validate(self._json(response, (200,)))
        except (ValueError, TypeError) as error:
            raise JetsonClientError("invalid_status") from error
        if self._boot_id != status.boot_id:
            self._boot_id = status.boot_id
            self._sequence = 0
            self._preview = None
            self._preview_monotonic = None
        return status

    def status(self) -> JetsonStatus:
        with self._locks[0]:
            return self._status()

    def calibrate_clock(self) -> ClockCalibration:
        with self._locks[0]:
            return self._calibrate_clock()

    def _calibrate_clock(self) -> ClockCalibration:
        send_wall = self._wall()
        send_monotonic = self._monotonic()
        self._sample_guard(send_wall, send_monotonic)
        status = self._status(timestamp=send_wall)
        receive_monotonic = self._monotonic()
        receive_wall = self._wall()
        self._sample_guard(receive_wall, receive_monotonic)
        calibration = ClockCalibration(
            measured_monotonic=receive_monotonic,
            offset_ms=(status.server_time.timestamp() - ((send_wall + receive_wall) / 2)) * 1000,
            half_rtt_ms=(receive_monotonic - send_monotonic) * 500,
        )
        if receive_monotonic < self._guard_disabled_until or not calibration.valid_at(receive_monotonic):
            self._calibration = None
            raise JetsonClientError("clock_uncertain")
        self._calibration = calibration
        return calibration

    def _preview_for(self, observation: JetsonObservation) -> bytes:
        monotonic_now = self._monotonic()
        if self._preview is not None and self._preview_monotonic is not None and monotonic_now - self._preview_monotonic < 0.5:
            return self._preview
        response = self._send(self._control, "GET", "/v1/preview.jpg")
        expected = {
            "content-type", "content-length", "cache-control", "x-petcare-jetson-boot-id",
            "x-petcare-jetson-sequence", "x-petcare-jetson-observed-at", "x-petcare-jetson-content-sha256",
        }
        if response.status_code != 200:
            self._json(response, (200,))
            raise JetsonClientError("invalid_response")
        if not self._valid_media_headers(response.headers, expected):
            response.close()
            raise JetsonClientError("invalid_preview_headers")
        headers = response.headers
        try:
            valid_headers = (
                headers["content-type"] == "image/jpeg"
                and headers["cache-control"] == "private, no-store, no-transform"
                and headers["x-petcare-jetson-boot-id"] == observation.boot_id
                and headers["x-petcare-jetson-sequence"] == str(observation.sequence)
                and canonical_utc(headers["x-petcare-jetson-observed-at"]) == observation.observed_at
            )
        except (ValueError, TypeError) as error:
            response.close()
            raise JetsonClientError("invalid_preview_headers") from error
        if not valid_headers:
            response.close()
            raise JetsonClientError("invalid_preview_headers")
        try:
            content = self._bounded(response, MAX_PREVIEW_BYTES, require_length=True)
        except JetsonClientError as error:
            raise JetsonClientError("invalid_preview") from error
        if hashlib.sha256(content).hexdigest() != headers["x-petcare-jetson-content-sha256"]:
            raise JetsonClientError("invalid_preview_digest")
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.dtype != np.uint8 or image.shape != (480, 640, 3):
            raise JetsonClientError("invalid_preview_jpeg")
        self._preview = content
        self._preview_monotonic = monotonic_now
        return content

    @staticmethod
    def _valid_media_headers(headers: httpx.Headers, expected: set[str]) -> bool:
        names = set(headers)
        return (
            expected <= names <= expected | {"date", "connection"}
            and ("connection" not in headers or headers["connection"].lower() == "close")
        )

    def next_frame(self, zones: Mapping[str, object]) -> ProcessedFrame:
        with self._locks[0]:
            if self._boot_id is None:
                self._status()
            assert self._boot_id is not None
            for attempt in range(2):
                after = self._sequence
                target = "/v1/observations?" + canonical_query((("after", str(after)), ("wait_ms", "1000")))
                response = self._send(self._control, "GET", target, timeout=2.0)
                if response.status_code != 401 or attempt:
                    break
                self._validate_unauthorized(response)
                self._status(bootstrap=True)
            if response.status_code == 204:
                response.close()
                raise JetsonClientError("observation_timeout")
            if response.status_code != 200:
                self._json(response, (200,))
                raise JetsonClientError("invalid_response")
            try:
                observation = parse_observation(
                    self._bounded(response, MAX_JSON_BYTES), self._boot_id, after, self._now()
                )
            except (ValueError, TypeError) as error:
                raise JetsonClientError("invalid_observation") from error
            try:
                preview = self._preview_for(observation)
            except JetsonClientError:
                if self._preview is None:
                    raise
                preview = self._preview
            final_age = self._now().astimezone(UTC) - observation.observed_at
            if not 0 <= final_age.total_seconds() <= 3:
                raise JetsonClientError("invalid_observation_freshness")

            detections = tuple(self._home_detection(item, observation, zones) for item in observation.detections)
            bed = [item for item in detections if item.subject_id is not None and item.zone_name == "pet_bed"]
            bed_subject_ids = tuple(
                subject for subject in ("dog_001", "cat_001") if any(item.subject_id == subject for item in bed)
            )
            selected_bed_subject_id = None
            if bed:
                selected_bed_subject_id = min(
                    bed, key=lambda item: (-item.confidence, 0 if item.subject_id == "dog_001" else 1)
                ).subject_id
            self._sequence = observation.sequence
            return ProcessedFrame(
                jpeg=preview,
                detections=detections,
                fps=observation.fps,
                inference_ms=observation.inference_ms,
                observed_at=observation.observed_at,
                bed_subject_ids=bed_subject_ids,
                selected_bed_subject_id=selected_bed_subject_id,
            )

    @staticmethod
    def _home_detection(item: object, observation: JetsonObservation, zones: Mapping[str, object]) -> CameraDetectionIn:
        center_x = item.bbox_x + item.bbox_width // 2  # type: ignore[attr-defined]
        center_y = item.bbox_y + item.bbox_height // 2  # type: ignore[attr-defined]
        return CameraDetectionIn(
            camera_id="pc-webcam-01",
            subject_id=SUBJECTS[item.detected_type],  # type: ignore[attr-defined]
            detected_type=item.detected_type,  # type: ignore[attr-defined]
            confidence=item.confidence,  # type: ignore[attr-defined]
            bbox_x=item.bbox_x,  # type: ignore[attr-defined]
            bbox_y=item.bbox_y,  # type: ignore[attr-defined]
            bbox_width=item.bbox_width,  # type: ignore[attr-defined]
            bbox_height=item.bbox_height,  # type: ignore[attr-defined]
            center_x=center_x,
            center_y=center_y,
            zone_name=zone_for_center(center_x, center_y, zones),
            observed_at=observation.observed_at,
        )

    def put_clip(
        self, command_id: str, command: JetsonClipCommand | Mapping[str, object], *, first: bool = True
    ) -> JetsonPutResult:
        if COMMAND_ID.fullmatch(command_id) is None:
            raise ValueError("invalid command id")
        try:
            parsed = command if isinstance(command, JetsonClipCommand) else JetsonClipCommand.model_validate(command)
        except (ValueError, TypeError) as error:
            raise ValueError("invalid clip command") from error
        body = canonical_json({
            "committed_at": _utc_text(parsed.committed_at),
            "event_id": parsed.event_id,
            "event_type": parsed.event_type,
            "occurred_at": _utc_text(parsed.occurred_at),
        })
        with self._locks[1]:
            if first:
                with self._locks[0]:
                    self._calibrate_clock()
            put_wall = self._wall()
            monotonic_now = self._monotonic()
            self._sample_guard(put_wall, monotonic_now)
            if first and (self._calibration is None or not self._calibration.valid_at(monotonic_now) or monotonic_now < self._guard_disabled_until):
                raise JetsonClientError("clock_uncertain")
            response = self._send(
                self._admission, "PUT", f"/v1/clips/{command_id}", body=body, timestamp=put_wall,
            )
            if response.status_code == 401:
                self._validate_unauthorized(response)
                with self._locks[0]:
                    self._boot_id = None
                    if first:
                        self._calibrate_clock()
                    else:
                        self._status(bootstrap=True)
                put_wall = self._wall()
                monotonic_now = self._monotonic()
                self._sample_guard(put_wall, monotonic_now)
                if first and (
                    self._calibration is None
                    or not self._calibration.valid_at(monotonic_now)
                    or monotonic_now < self._guard_disabled_until
                ):
                    raise JetsonClientError("clock_uncertain")
                response = self._send(
                    self._admission, "PUT", f"/v1/clips/{command_id}", body=body, timestamp=put_wall,
                )
            status_code = response.status_code
            try:
                receipt = JetsonClipReceipt.model_validate(self._json(response, (200, 201)))
            except (ValueError, TypeError) as error:
                raise JetsonClientError("invalid_clip_receipt") from error
            if receipt.command_id != command_id or receipt.accepted_boot_id != self._boot_id:
                raise JetsonClientError("invalid_clip_receipt")
            return JetsonPutResult(status_code=status_code, receipt=receipt)

    def download_clip(self, command_id: str, destination: Path) -> JetsonClipHeaders:
        if COMMAND_ID.fullmatch(command_id) is None:
            raise ValueError("invalid command id")
        destination = Path(destination)
        with self._locks[2]:
            deadline = time.monotonic() + 45.0
            response = self._send(
                self._media, "GET", f"/v1/clips/{command_id}", timeout=45.0, deadline=deadline,
            )
            if response.status_code == 401:
                self._validate_unauthorized(response)
                with self._locks[0]:
                    self._boot_id = None
                    self._status(bootstrap=True, deadline=deadline)
                response = self._send(
                    self._media, "GET", f"/v1/clips/{command_id}", timeout=45.0, deadline=deadline,
                )
            expected = {
                "content-type", "content-length", "x-petcare-jetson-boot-id", "x-petcare-jetson-command-id",
                "x-petcare-jetson-content-sha256", "x-petcare-jetson-started-at", "x-petcare-jetson-ended-at",
                "x-petcare-jetson-events", "x-petcare-jetson-frame-count", "x-petcare-jetson-video-codec",
                "x-petcare-jetson-pixel-format",
            }
            if response.status_code != 200:
                content = self._bounded(response, MAX_JSON_BYTES)
                try:
                    code = JetsonError.model_validate(strict_json(content)).code
                except (ValueError, TypeError):
                    code = "invalid_response"
                if STATUS_BY_CODE.get(code) != response.status_code:
                    code = "invalid_response"
                raise JetsonClientError(code)
            if not self._valid_media_headers(response.headers, expected):
                response.close()
                raise JetsonClientError("invalid_clip_headers")
            headers = response.headers
            try:
                length = int(headers["content-length"])
                parsed = JetsonClipHeaders(
                    boot_id=headers["x-petcare-jetson-boot-id"],
                    command_id=headers["x-petcare-jetson-command-id"],
                    content_sha256=headers["x-petcare-jetson-content-sha256"],
                    started_at=headers["x-petcare-jetson-started-at"],
                    ended_at=headers["x-petcare-jetson-ended-at"],
                    events=headers["x-petcare-jetson-events"],
                    frame_count=int(headers["x-petcare-jetson-frame-count"]),
                    video_codec=headers["x-petcare-jetson-video-codec"],
                    pixel_format=headers["x-petcare-jetson-pixel-format"],
                )
                if headers["content-type"] != "video/mp4" or not 0 < length <= MAX_CLIP_BYTES:
                    raise ValueError("invalid clip length")
                if parsed.boot_id != self._boot_id or parsed.command_id != command_id:
                    raise ValueError("invalid clip identity")
            except (ValueError, TypeError) as error:
                response.close()
                raise JetsonClientError("invalid_clip_headers") from error

            descriptor: int | None = None
            created = False
            expired = threading.Event()
            timer = threading.Timer(max(0.0, deadline - time.monotonic()), lambda: (expired.set(), response.close()))
            timer.daemon = True
            timer.start()
            try:
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
                protect_runtime_file(destination)
                digest = hashlib.sha256()
                written = 0
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = None
                    for chunk in response.iter_bytes():
                        if expired.is_set() or time.monotonic() > deadline:
                            raise JetsonClientError("clip_timeout")
                        written += len(chunk)
                        if written > length or written > MAX_CLIP_BYTES:
                            raise JetsonClientError("invalid_clip_length")
                        output.write(chunk)
                        digest.update(chunk)
                if written != length or digest.hexdigest() != parsed.content_sha256:
                    raise JetsonClientError("invalid_clip_digest")
                return parsed
            except BaseException as error:
                if descriptor is not None:
                    os.close(descriptor)
                if created:
                    destination.unlink(missing_ok=True)
                if isinstance(error, httpx.HTTPError):
                    raise JetsonClientError("clip_timeout" if expired.is_set() else "jetson_unavailable") from error
                raise
            finally:
                timer.cancel()
                response.close()

    def delete_clip(self, command_id: str) -> int:
        if COMMAND_ID.fullmatch(command_id) is None:
            raise ValueError("invalid command id")
        with self._locks[2]:
            response = self._send(self._media, "DELETE", f"/v1/clips/{command_id}")
            if response.status_code == 401:
                self._validate_unauthorized(response)
                with self._locks[0]:
                    self._boot_id = None
                    self._status(bootstrap=True)
                response = self._send(self._media, "DELETE", f"/v1/clips/{command_id}")
            status = response.status_code
            content = self._bounded(response, MAX_JSON_BYTES)
            if status == 204:
                if content:
                    raise JetsonClientError("invalid_delete_response")
                return status
            try:
                code = JetsonError.model_validate(strict_json(content)).code
            except (ValueError, TypeError):
                code = "invalid_response"
            if STATUS_BY_CODE.get(code) != status:
                code = "invalid_response"
            if status == 410 and code == "clip_gone":
                return status
            raise JetsonClientError(code)
