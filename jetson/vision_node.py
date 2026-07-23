import argparse
import base64
import datetime
import email.utils
import hashlib
import ipaddress
import json
import math
import os
import re
import signal
import socket
import ssl
import stat
import subprocess
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

if __package__:
    from jetson.clip_writer import (
        ClipBusy,
        ClipGone,
        ClipNotReady,
        ClipWriter,
        CommandConflict,
        CommandExpired,
    )
    from jetson.protocol import MAX_BODY_BYTES, ProtocolError, ReplayGuard, verify_request
    from jetson.tensorrt_yolo import GstreamerEncoder, TensorRtYolo
else:
    from clip_writer import ClipBusy, ClipGone, ClipNotReady, ClipWriter, CommandConflict, CommandExpired
    from protocol import MAX_BODY_BYTES, ProtocolError, ReplayGuard, verify_request
    from tensorrt_yolo import GstreamerEncoder, TensorRtYolo


STATUS_PATH = "/v1/status"
PREVIEW_PATH = "/v1/preview.jpg"
CLIP_PATH = re.compile(r"^/v1/clips/([0-9a-f]{32})$")
OBSERVATION_PATH = re.compile(r"^/v1/observations\?after=(0|[1-9][0-9]*)&wait_ms=(0|[1-9][0-9]*)$")
CANONICAL_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
MAX_RESPONSE_JSON = 65536
MAX_PREVIEW_BYTES = 1048576
MAX_HEADER_BYTES = 16384
MAX_HEADER_COUNT = 64
MAX_HEADER_VALUE = 4096
MAX_TEMPERATURE_C = 80.0
ERRORS = {
    "invalid_request": (400, "Invalid request"),
    "unauthorized": (401, "Unauthorized"),
    "command_conflict": (409, "Command conflict"),
    "command_expired": (409, "Command expired"),
    "camera_unavailable": (503, "Camera unavailable"),
    "clip_busy": (503, "Clip busy"),
    "clip_not_ready": (425, "Clip not ready"),
    "clip_gone": (410, "Clip gone"),
    "internal_error": (500, "Internal error"),
}
AUTH_FAILURES = frozenset(("unauthorized", "stale_request", "replayed_request", "wrong_boot"))
PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)


class ServiceError(Exception):
    def __init__(self, code):
        self.code = code
        Exception.__init__(self, code)


class _HeaderLimit(Exception):
    pass


class _HeaderReader(object):
    def __init__(self, raw):
        self.raw = raw
        self.total = 0
        self.count = 0

    def readline(self, limit=-1):
        remaining = MAX_HEADER_BYTES - self.total
        maximum = remaining + 1
        if limit is not None and limit >= 0:
            maximum = min(maximum, limit)
        line = self.raw.readline(maximum)
        self.total += len(line)
        if self.total > MAX_HEADER_BYTES:
            raise _HeaderLimit()
        if line not in (b"", b"\n", b"\r\n"):
            self.count += 1
            if self.count > MAX_HEADER_COUNT:
                raise _HeaderLimit()
        return line


def _utc(unix_seconds):
    seconds = float(unix_seconds)
    whole = math.floor(seconds)
    micros = int(round((seconds - whole) * 1000000))
    if micros == 1000000:
        whole += 1
        micros = 0
    instant = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=whole)
    return instant.strftime("%Y-%m-%dT%H:%M:%S") + ".{:06d}Z".format(micros)


def _json_bytes(value):
    content = json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(content) > MAX_RESPONSE_JSON:
        raise ServiceError("internal_error")
    return content


class ClockGuard(object):
    def __init__(self):
        self._lock = threading.Lock()
        self._last_wall = None
        self._last_monotonic = None
        self._blocked_until = 0.0

    def sample(self, wall, monotonic):
        wall = float(wall)
        monotonic = float(monotonic)
        with self._lock:
            if self._last_wall is not None:
                wall_delta = wall - self._last_wall
                monotonic_delta = monotonic - self._last_monotonic
                if abs(wall_delta - monotonic_delta) > 0.025:
                    self._blocked_until = max(self._blocked_until, monotonic + 60.0)
            self._last_wall = wall
            self._last_monotonic = monotonic

    def ready(self, monotonic):
        with self._lock:
            return float(monotonic) >= self._blocked_until


class SystemProbe(object):
    def __init__(self, temperature_path, max_temperature_c=MAX_TEMPERATURE_C):
        self.temperature_path = temperature_path
        self.max_temperature_c = float(max_temperature_c)

    def __call__(self):
        with open(self.temperature_path, "r", encoding="ascii") as handle:
            temperature = float(handle.read().strip()) / 1000.0
        synchronized = False
        try:
            synchronized = subprocess.check_output(
                ["/usr/bin/timedatectl", "show", "--property=NTPSynchronized", "--value"],
                stderr=subprocess.STDOUT,
                timeout=1,
                universal_newlines=True,
            ).strip().lower() == "yes"
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            synchronized = False
        return {
            "clock_synchronized": synchronized,
            "temperature_c": temperature,
            "throttled": temperature >= self.max_temperature_c,
        }


class OpenCvCamera(object):
    def __init__(self, device):
        import cv2

        self.cv2 = cv2
        self.device = device
        self.capture = None
        self._open()

    def _open(self):
        self.capture = self.cv2.VideoCapture(self.device)
        self.capture.set(self.cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.capture.set(self.cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if not self.capture.isOpened():
            self.capture.release()
            raise RuntimeError("camera_unavailable")

    def read(self):
        ok, frame = self.capture.read()
        if not ok or frame is None or frame.shape != (480, 640, 3):
            self.capture.release()
            self._open()
            raise RuntimeError("camera_unavailable")
        return frame

    def close(self):
        self.capture.release()


def render_jpeg(frame, detections):
    import cv2

    annotated = frame.copy()
    for item in detections:
        left = item["bbox_x"]
        top = item["bbox_y"]
        right = left + item["bbox_width"]
        bottom = top + item["bbox_height"]
        cv2.rectangle(annotated, (left, top), (right, bottom), (0, 255, 0), 2)
        cv2.putText(
            annotated,
            "{} {:.2f}".format(item["detected_type"], item["confidence"]),
            (left, max(16, top - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )
    ok, encoded = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        raise RuntimeError("camera_unavailable")
    jpeg = encoded.tobytes()
    if not 0 < len(jpeg) <= MAX_PREVIEW_BYTES:
        raise RuntimeError("camera_unavailable")
    return jpeg


class VisionNode(object):
    def __init__(self, secret, boot_id, clip_writer, probe, wall_clock=None,
                 monotonic_clock=None, monotonic_ns=None, camera=None, detector=None,
                 renderer=None, max_temperature_c=MAX_TEMPERATURE_C):
        if type(secret) is not bytes or len(secret) != 32 or re.match(r"^[0-9a-f]{32}$", boot_id or "") is None:
            raise ValueError("invalid_configuration")
        self.secret = secret
        self.boot_id = boot_id
        self.clip_writer = clip_writer
        self.probe = probe
        self.wall_clock = wall_clock or time.time
        self.monotonic_clock = monotonic_clock or time.monotonic
        self.monotonic_ns = monotonic_ns or getattr(
            time, "monotonic_ns", lambda: int(time.monotonic() * 1000000000)
        )
        self.camera = camera
        self.detector = detector
        self.renderer = renderer or render_jpeg
        self.max_temperature_c = float(max_temperature_c)
        self.replay_guard = ReplayGuard(monotonic_clock=self.monotonic_clock)
        self.clock_guard = ClockGuard()
        self._condition = threading.Condition()
        self._admission_lock = threading.Lock()
        self._latest_observation = None
        self._latest_jpeg = None
        self._camera_online = False
        self._last_preview = None
        self._last_status = None
        self._put_admission = True
        self._stop = threading.Event()
        self._capture_thread = None
        self._sampler_thread = None
        self._closed = False

    def start(self):
        if self.camera is None or self.detector is None:
            raise RuntimeError("camera_unavailable")
        if self._capture_thread is not None:
            return
        self._capture_thread = threading.Thread(target=self._capture_loop, name="petcare-vision-capture")
        self._sampler_thread = threading.Thread(target=self._sampler_loop, name="petcare-vision-sampler")
        self._capture_thread.start()
        self._sampler_thread.start()

    def set_camera_state(self, online):
        with self._condition:
            self._camera_online = bool(online)
            self._condition.notify_all()

    def publish(self, observation, jpeg):
        self._validate_observation(observation)
        if type(jpeg) is not bytes or not 0 < len(jpeg) <= MAX_PREVIEW_BYTES:
            raise ValueError("invalid_preview")
        with self._condition:
            previous = self._latest_observation
            if previous is not None and observation["sequence"] <= previous["sequence"]:
                raise ValueError("invalid_sequence")
            self._latest_observation = OrderedDict(observation)
            self._latest_jpeg = jpeg
            self._camera_online = True
            self._condition.notify_all()

    def _validate_observation(self, value):
        required = (
            "boot_id", "sequence", "observed_at", "width", "height", "fps", "inference_ms", "detections"
        )
        if type(value) not in (dict, OrderedDict) or tuple(value) != required:
            raise ValueError("invalid_observation")
        if value["boot_id"] != self.boot_id or type(value["sequence"]) is not int or value["sequence"] < 0:
            raise ValueError("invalid_observation")
        if type(value["observed_at"]) is not str or CANONICAL_UTC.match(value["observed_at"]) is None:
            raise ValueError("invalid_observation")
        if type(value["width"]) is not int or value["width"] != 640 or type(value["height"]) is not int or value["height"] != 480:
            raise ValueError("invalid_observation")
        for name in ("fps", "inference_ms"):
            if type(value[name]) is not float or not math.isfinite(value[name]) or value[name] < 0:
                raise ValueError("invalid_observation")
        if type(value["detections"]) is not list or len(value["detections"]) > 3:
            raise ValueError("invalid_observation")
        classes = []
        for item in value["detections"]:
            keys = ("detected_type", "confidence", "bbox_x", "bbox_y", "bbox_width", "bbox_height")
            if type(item) is not dict or tuple(item) != keys or item["detected_type"] not in ("person", "dog", "cat"):
                raise ValueError("invalid_observation")
            classes.append(item["detected_type"])
            confidence = item["confidence"]
            coordinates = tuple(item[name] for name in keys[2:])
            if type(confidence) is not float or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("invalid_observation")
            if any(type(number) is not int for number in coordinates):
                raise ValueError("invalid_observation")
            x, y, width, height = coordinates
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 640 or y + height > 480:
                raise ValueError("invalid_observation")
        if len(classes) != len(set(classes)):
            raise ValueError("invalid_observation")

    def note_status(self, received_wall, received_monotonic):
        self.clock_guard.sample(received_wall, received_monotonic)
        with self._condition:
            self._last_status = float(received_monotonic)

    def status(self):
        health = self._health()
        state = getattr(self.clip_writer, "clip_state", None)
        if state is None:
            state = getattr(self.clip_writer, "state", "idle")
        if state not in ("idle", "recording", "finalizing", "ready"):
            state = "idle"
        with self._condition:
            camera_state = "online" if self._camera_online else "offline"
        return OrderedDict((
            ("boot_id", self.boot_id),
            ("server_time", _utc(self.wall_clock())),
            ("camera_state", camera_state),
            ("clip_state", state),
            ("jetpack", "4.6.6"),
            ("l4t", "32.7.6"),
            ("tensorrt", "8.2.1"),
            ("temperature_c", float(health["temperature_c"])),
            ("throttled", bool(health["throttled"])),
        ))

    def _health(self):
        try:
            value = self.probe()
        except BaseException:
            raise ServiceError("internal_error")
        if type(value) is not dict or set(value) != {"clock_synchronized", "temperature_c", "throttled"}:
            raise ServiceError("internal_error")
        temperature = value["temperature_c"]
        if type(value["clock_synchronized"]) is not bool or type(value["throttled"]) is not bool:
            raise ServiceError("internal_error")
        if type(temperature) not in (int, float) or isinstance(temperature, bool) or not math.isfinite(temperature):
            raise ServiceError("internal_error")
        return value

    def observation(self, after, wait_ms):
        deadline = self.monotonic_clock() + wait_ms / 1000.0
        with self._condition:
            while not self._stop.is_set():
                if self._latest_observation is not None and self._latest_observation["sequence"] > after:
                    return OrderedDict(self._latest_observation)
                remaining = deadline - self.monotonic_clock()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
        return None

    def preview(self):
        now = float(self.monotonic_clock())
        with self._condition:
            if not self._camera_online or self._latest_observation is None or self._latest_jpeg is None:
                raise ServiceError("camera_unavailable")
            if self._last_preview is not None and now - self._last_preview < 0.5:
                raise ServiceError("camera_unavailable")
            self._last_preview = now
            return OrderedDict(self._latest_observation), self._latest_jpeg

    def put(self, command_id, digest, value, received_wall, received_monotonic, received_monotonic_ns):
        with self._admission_lock:
            self.clock_guard.sample(received_wall, received_monotonic)
            try:
                existing = self.clip_writer.lookup_receipt(command_id, digest)
            except (CommandConflict, ClipGone):
                raise
            if existing is not None:
                return False, existing
            with self._condition:
                camera_online = self._camera_online
                last_status = self._last_status
                accepting = self._put_admission
            health = self._health()
            fresh = last_status is not None and 0 <= received_monotonic - last_status <= 1.0
            if (not accepting or not camera_online or not fresh or not self.clock_guard.ready(received_monotonic)
                    or not health["clock_synchronized"] or health["throttled"]
                    or float(health["temperature_c"]) >= self.max_temperature_c):
                raise ServiceError("camera_unavailable")
            receipt = self.clip_writer.put(
                command_id,
                digest,
                value["committed_at"],
                value["event_type"],
                value["event_id"],
                value["occurred_at"],
                received_wall,
                received_monotonic_ns,
            )
            return True, receipt

    def _capture_loop(self):
        sequence = 0
        first_monotonic = None
        while not self._stop.is_set():
            started = self.monotonic_clock()
            try:
                frame = self.camera.read()
                observed_at = _utc(self.wall_clock())
                detections = self.detector.infer(frame)
                jpeg = self.renderer(frame, detections)
                ended = self.monotonic_clock()
                if first_monotonic is None:
                    first_monotonic = ended
                sequence += 1
                elapsed = max(ended - first_monotonic, 0.000001)
                observation = OrderedDict((
                    ("boot_id", self.boot_id),
                    ("sequence", sequence),
                    ("observed_at", observed_at),
                    ("width", 640),
                    ("height", 480),
                    ("fps", float(sequence / elapsed)),
                    ("inference_ms", float(max(0.0, ended - started) * 1000.0)),
                    ("detections", detections),
                ))
                self.publish(observation, jpeg)
            except BaseException:
                self.set_camera_state(False)
                self._stop.wait(0.25)

    def _sampler_loop(self):
        last_bucket = None
        while not self._stop.is_set():
            now_ns = int(self.monotonic_ns())
            bucket = now_ns // 100000000
            self.clock_guard.sample(self.wall_clock(), now_ns / 1000000000.0)
            with self._condition:
                jpeg = self._latest_jpeg if self._camera_online else None
            if jpeg is not None and bucket != last_bucket:
                try:
                    self.clip_writer.push(bucket, jpeg)
                except (RuntimeError, ValueError):
                    pass
                last_bucket = bucket
            delay = max(0.0, ((bucket + 1) * 100000000 - int(self.monotonic_ns())) / 1000000000.0)
            self._stop.wait(min(delay, 0.1))

    def stop_admission(self):
        with self._condition:
            self._put_admission = False

    def shutdown(self, timeout=5.0):
        if type(timeout) not in (int, float) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("invalid_timeout")
        deadline = time.monotonic() + timeout

        def remaining():
            return max(0.001, deadline - time.monotonic())

        with self._condition:
            if self._closed:
                return
            self.stop_admission()
            self._stop.set()
            self._condition.notify_all()
        first_error = None
        try:
            self.clip_writer.shutdown(remaining())
        except BaseException as error:
            first_error = error
        if self._sampler_thread is not None:
            self._sampler_thread.join(remaining())
            if self._sampler_thread.is_alive():
                first_error = first_error or RuntimeError("sampler_shutdown_timeout")
        if self.camera is not None:
            try:
                self.camera.close()
            except BaseException as error:
                first_error = first_error or error
        if self._capture_thread is not None:
            self._capture_thread.join(remaining())
            if self._capture_thread.is_alive():
                first_error = first_error or RuntimeError("camera_shutdown_timeout")
        if self.detector is not None:
            try:
                self.detector.close()
            except BaseException as error:
                first_error = first_error or error
        self._closed = True
        if first_error is not None:
            raise RuntimeError("internal_error")


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = False
    request_queue_size = 8
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        self._requests_condition = threading.Condition()
        self._requests = {}
        super(_ThreadedHTTPServer, self).__init__(*args, **kwargs)

    def get_request(self):
        request, address = HTTPServer.get_request(self)
        request.settimeout(1.0)
        return request, address

    def process_request(self, request, client_address):
        thread = threading.Thread(target=self._run_request, args=(request, client_address))
        thread.daemon = False
        with self._requests_condition:
            self._requests[thread] = request
        try:
            thread.start()
        except BaseException:
            with self._requests_condition:
                self._requests.pop(thread, None)
            raise

    def _run_request(self, request, client_address):
        try:
            self.process_request_thread(request, client_address)
        finally:
            with self._requests_condition:
                self._requests.pop(threading.current_thread(), None)
                self._requests_condition.notify_all()

    @property
    def active_request_count(self):
        with self._requests_condition:
            return len(self._requests)

    def server_close(self):
        with self._requests_condition:
            requests = tuple(self._requests.values())
        for request in requests:
            try:
                request.settimeout(0.1)
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                request.close()
            except OSError:
                pass
        HTTPServer.server_close(self)
        deadline = time.monotonic() + 1.5
        with self._requests_condition:
            while self._requests:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("request_shutdown_timeout")
                self._requests_condition.wait(remaining)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __getattr__(self, name):
        if name.startswith("do_"):
            return lambda: self._handle(self.command)
        raise AttributeError(name)

    def log_message(self, unused_format, *unused_args):
        return

    def parse_request(self):
        original = self.rfile
        self.rfile = _HeaderReader(original)
        try:
            return super(_Handler, self).parse_request()
        except _HeaderLimit:
            self.send_error(400)
            return False
        finally:
            self.rfile = original

    def send_error(self, unused_code, unused_message=None, unused_explain=None):
        self.close_connection = True
        self._send_error("invalid_request")

    def do_GET(self):
        self._handle("GET")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def _handle(self, method):
        self.close_connection = True
        received_wall = self.server.node.wall_clock()
        received_monotonic = self.server.node.monotonic_clock()
        received_monotonic_ns = self.server.node.monotonic_ns()
        try:
            self._bounded_headers()
            body = self._read_body(method)
            try:
                verify_request(
                    method,
                    self.path,
                    self.headers,
                    body,
                    self.server.node.secret,
                    self.server.node.boot_id,
                    received_wall,
                    self.server.node.replay_guard,
                )
            except ProtocolError as error:
                raise ServiceError("unauthorized" if error.code in AUTH_FAILURES else error.code)
            operation = self._operation(method)
            if operation != "put" and body:
                raise ServiceError("invalid_request")
            if operation == "status":
                self.server.node.note_status(received_wall, received_monotonic)
                self._send_json(200, self.server.node.status())
            elif operation == "observations":
                match = OBSERVATION_PATH.match(self.path)
                after, wait_ms = int(match.group(1)), int(match.group(2))
                if after > 9223372036854775807 or wait_ms > 1000:
                    raise ServiceError("invalid_request")
                value = self.server.node.observation(after, wait_ms)
                if value is None:
                    self._send(204, (), b"")
                else:
                    self._send_json(200, value)
            elif operation == "preview":
                observation, jpeg = self.server.node.preview()
                headers = (
                    ("Content-Type", "image/jpeg"),
                    ("Content-Length", str(len(jpeg))),
                    ("Cache-Control", "private, no-store, no-transform"),
                    ("X-PetCare-Jetson-Boot-Id", self.server.node.boot_id),
                    ("X-PetCare-Jetson-Sequence", str(observation["sequence"])),
                    ("X-PetCare-Jetson-Observed-At", observation["observed_at"]),
                    ("X-PetCare-Jetson-Content-SHA256", hashlib.sha256(jpeg).hexdigest()),
                )
                self._send(200, headers, jpeg)
            elif operation == "put":
                command_id = CLIP_PATH.match(self.path).group(1)
                value = json.loads(body.decode("utf-8"), object_pairs_hook=OrderedDict)
                digest = hashlib.sha256(body).hexdigest()
                created, receipt = self.server.node.put(
                    command_id, digest, value, received_wall, received_monotonic, received_monotonic_ns
                )
                self._send_json(201 if created else 200, receipt)
            elif operation == "get_clip":
                self._send_media(CLIP_PATH.match(self.path).group(1))
            elif operation == "delete_clip":
                self.server.node.clip_writer.delete(CLIP_PATH.match(self.path).group(1))
                self._send(204, (), b"")
        except ServiceError as error:
            self._send_error(error.code)
        except CommandConflict:
            self._send_error("command_conflict")
        except CommandExpired:
            self._send_error("command_expired")
        except ClipBusy:
            self._send_error("clip_busy")
        except ClipNotReady:
            self._send_error("clip_not_ready")
        except ClipGone:
            self._send_error("clip_gone")
        except (UnicodeError, ValueError, json.JSONDecodeError):
            self._send_error("invalid_request")
        except BaseException:
            self._send_error("internal_error")

    def _operation(self, method):
        if self.path == STATUS_PATH:
            allowed = {"GET": "status"}
        elif self.path == PREVIEW_PATH:
            allowed = {"GET": "preview"}
        elif OBSERVATION_PATH.match(self.path):
            allowed = {"GET": "observations"}
        elif CLIP_PATH.match(self.path):
            allowed = {"PUT": "put", "GET": "get_clip", "DELETE": "delete_clip"}
        else:
            raise ServiceError("invalid_request_404")
        if method not in allowed:
            raise ServiceError("invalid_request_405")
        return allowed[method]

    def _read_body(self, method):
        values = self.headers.get_all("Content-Length", [])
        transfer = self.headers.get_all("Transfer-Encoding", [])
        if transfer or len(values) > 1:
            raise ServiceError("invalid_request")
        if not values:
            length = 0
        elif re.match(r"^(0|[1-9][0-9]{0,4})$", values[0]) is None:
            raise ServiceError("invalid_request")
        else:
            length = int(values[0])
        if length > MAX_BODY_BYTES:
            raise ServiceError("invalid_request")
        content = self.rfile.read(length)
        if len(content) != length:
            raise ServiceError("invalid_request")
        return content

    def _bounded_headers(self):
        pairs = list(self.headers.raw_items())
        if len(pairs) > MAX_HEADER_COUNT:
            raise ServiceError("invalid_request")
        total = 0
        for name, value in pairs:
            if len(name) > 128 or len(value) > MAX_HEADER_VALUE:
                raise ServiceError("invalid_request")
            total += len(name) + len(value) + 4
        if total > MAX_HEADER_BYTES:
            raise ServiceError("invalid_request")

    def _send_media(self, command_id):
        value = self.server.node.clip_writer.get(command_id)
        self.connection.settimeout(45.0)
        headers = (
            ("Content-Type", "video/mp4"),
            ("Content-Length", str(value["content_length"])),
            ("X-PetCare-Jetson-Boot-Id", self.server.node.boot_id),
            ("X-PetCare-Jetson-Command-Id", command_id),
            ("X-PetCare-Jetson-Content-SHA256", value["content_sha256"]),
            ("X-PetCare-Jetson-Started-At", value["started_at"]),
            ("X-PetCare-Jetson-Ended-At", value["ended_at"]),
            ("X-PetCare-Jetson-Events", value["events"]),
            ("X-PetCare-Jetson-Frame-Count", str(value["frame_count"])),
            ("X-PetCare-Jetson-Video-Codec", value["video_codec"]),
            ("X-PetCare-Jetson-Pixel-Format", value["pixel_format"]),
        )
        self.send_response_only(200)
        self.send_header("Date", email.utils.formatdate(usegmt=True))
        self.send_header("Connection", "close")
        for name, header_value in headers:
            self.send_header(name, header_value)
        self.end_headers()
        remaining = int(value["content_length"])
        with open(value["path"], "rb") as handle:
            while remaining:
                block = handle.read(min(65536, remaining))
                if not block:
                    raise RuntimeError("invalid_media")
                self.wfile.write(block)
                remaining -= len(block)

    def _send_json(self, status, value):
        content = _json_bytes(value)
        self._send(status, (("Content-Type", "application/json"), ("Content-Length", str(len(content)))), content)

    def _send_error(self, code):
        status_override = None
        if code == "invalid_request_404":
            status_override, code = 404, "invalid_request"
        elif code == "invalid_request_405":
            status_override, code = 405, "invalid_request"
        if code not in ERRORS:
            code = "internal_error"
        status, message = ERRORS[code]
        self._send_json(status_override or status, OrderedDict((("code", code), ("message", message))))

    def _send(self, status, headers, content):
        self.send_response_only(status)
        self.send_header("Date", email.utils.formatdate(usegmt=True))
        self.send_header("Connection", "close")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if content:
            self.wfile.write(content)


def create_https_server(node, address, certificate_path, private_key_path):
    server = _ThreadedHTTPServer(address, _Handler)
    server.node = node
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate_path, private_key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def _owner_descriptor(path, maximum):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError("invalid_configuration")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
            raise ValueError("invalid_configuration")
        if os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError("invalid_configuration")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _owner_bytes(path, maximum):
    descriptor = _owner_descriptor(path, maximum)
    with os.fdopen(descriptor, "rb") as handle:
        value = handle.read(maximum + 1)
    if not value or len(value) > maximum:
        raise ValueError("invalid_configuration")
    return value


def _configuration(path):
    raw = _owner_bytes(path, 65536)

    def unique_pairs(pairs):
        result = OrderedDict()
        for key, item in pairs:
            if key in result:
                raise ValueError("invalid_configuration")
            result[key] = item
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError("invalid_configuration")
    required = {
        "bind_ip", "port", "webcam", "certificate_path", "private_key_path", "psk_path",
        "engine_path", "engine_metadata_path", "state_dir", "temperature_path", "max_temperature_c",
    }
    if type(value) is not OrderedDict or set(value) != required:
        raise ValueError("invalid_configuration")
    try:
        bind_address = ipaddress.ip_address(value["bind_ip"])
    except (TypeError, ValueError):
        raise ValueError("invalid_configuration")
    if (type(bind_address) is not ipaddress.IPv4Address or not any(bind_address in network for network in PRIVATE_NETWORKS)
            or type(value["port"]) is not int or value["port"] != 9443
            or type(value["webcam"]) is not str or not value["webcam"].startswith("/dev/video")
            or type(value["max_temperature_c"]) not in (int, float)
            or isinstance(value["max_temperature_c"], bool)
            or not 40.0 <= float(value["max_temperature_c"]) <= 100.0):
        raise ValueError("invalid_configuration")
    for name in required - {"bind_ip", "port", "webcam", "max_temperature_c"}:
        if type(value[name]) is not str or not os.path.isabs(value[name]):
            raise ValueError("invalid_configuration")
    return value


def _boot_id():
    with open("/proc/sys/kernel/random/boot_id", "r", encoding="ascii") as handle:
        value = handle.read().strip().replace("-", "")
    if re.match(r"^[0-9a-f]{32}$", value) is None:
        raise ValueError("invalid_boot_id")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = _configuration(args.config)
    secret = _owner_bytes(config["psk_path"], 32)
    if len(secret) != 32:
        raise ValueError("invalid_configuration")
    certificate_descriptor = _owner_descriptor(config["certificate_path"], 65536)
    try:
        private_key_descriptor = _owner_descriptor(config["private_key_path"], 65536)
    except BaseException:
        os.close(certificate_descriptor)
        raise
    boot_id = _boot_id()
    encoder = GstreamerEncoder()
    writer = ClipWriter(config["state_dir"], encoder, boot_id=boot_id)
    detector = TensorRtYolo(
        config["engine_path"],
        metadata_path=config["engine_metadata_path"],
    )
    camera = OpenCvCamera(config["webcam"])
    probe = SystemProbe(config["temperature_path"], config["max_temperature_c"])
    node = VisionNode(
        secret, boot_id, writer, probe, camera=camera, detector=detector,
        max_temperature_c=config["max_temperature_c"],
    )
    try:
        server = create_https_server(
            node,
            (config["bind_ip"], config["port"]),
            "/proc/self/fd/{}".format(certificate_descriptor),
            "/proc/self/fd/{}".format(private_key_descriptor),
        )
    finally:
        os.close(private_key_descriptor)
        os.close(certificate_descriptor)

    def stop(unused_signum, unused_frame):
        threading.Thread(target=server.shutdown, name="petcare-vision-stop").start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    node.start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        node.stop_admission()
        try:
            server.server_close()
        finally:
            node.shutdown(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
