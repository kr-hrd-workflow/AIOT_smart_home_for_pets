import base64
import hashlib
import hmac
import http.client
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest

from jetson.clip_writer import (
    ClipBusy,
    ClipGone,
    ClipNotReady,
    CommandConflict,
    CommandExpired,
)
from jetson.vision_node import OpenCvCamera, VisionNode, _configuration, create_https_server


CERTIFICATE = """-----BEGIN CERTIFICATE-----
MIICtjCCAZ6gAwIBAgIBATANBgkqhkiG9w0BAQsFADAUMRIwEAYDVQQDDAkxMjcu
MC4wLjEwHhcNMjYwNzIwMDcyNDIyWhcNMzYwNzE4MDcyNDIyWjAUMRIwEAYDVQQD
DAkxMjcuMC4wLjEwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCn37Ah
qRfDf4Irx4J9jWjrtZOfsnvvTZZkNi93J/kFvO28yswBbsbd993jJ2uk1A6ZmRhs
wzpdq+bnr2DzJYES6vibqWye9pNv3e7w5N5XQyJ2gLG6HHU4vp4VT7ARKyuSctER
jiQcslfzNH+UDPtXoc9VtLHYdy3bPaVRRpANT7rE8IKfpHuobcBCBFGa3BJsXs/c
iQ6zjSWnD5zHdeVE3c+ZMrZhC0Bq/3oVlWyNAoyVzJDyh0Cs+9Ro9X5YBxdpXB6/
atHn7bz3v7ue1qZTxEZFQ+BfBzJD5BCBrTjiQOw1mDboXp/jrnLeP2H/bOYbUBYu
a1iF0Pi9NeLp9binAgMBAAGjEzARMA8GA1UdEQQIMAaHBH8AAAEwDQYJKoZIhvcN
AQELBQADggEBAIG0fMCbCA4pWAqzLWnYttbK91mzrHKAeVbjq9QvKomQ4mmGQcd9
VZxb8tUHbiPQNou1qcsWg0fVfUpb0rQE71WapE1rFYRFFsX48uMf9v9/s8+8N6vO
UJGji1gBD75BjGlIqk+uk/URiFXLvUsGUBHqvURBWR+4lqo9JLpTn3LYp1Qart+k
SwHkazkZ9rkcuQtyuvvEuI6rWI7SVEmrZz1FXJhkPpVLaZRrPCvHiiLYd4b64bYJ
lWWvoeGEzFxlvh5B9PHvKBO9RJnmeOuq8QkW795FUair4Es7WAsEQ1/zLpjsEvnR
4d2IPxbLTwtmGE8KYuJjDpmA0f6nqo43nU4=
-----END CERTIFICATE-----
"""

PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEogIBAAKCAQEAp9+wIakXw3+CK8eCfY1o67WTn7J7702WZDYvdyf5BbztvMrM
AW7G3ffd4ydrpNQOmZkYbMM6Xavm569g8yWBEur4m6lsnvaTb93u8OTeV0MidoCx
uhx1OL6eFU+wESsrknLREY4kHLJX8zR/lAz7V6HPVbSx2Hct2z2lUUaQDU+6xPCC
n6R7qG3AQgRRmtwSbF7P3IkOs40lpw+cx3XlRN3PmTK2YQtAav96FZVsjQKMlcyQ
8odArPvUaPV+WAcXaVwev2rR5+2897+7ntamU8RGRUPgXwcyQ+QQga044kDsNZg2
6F6f465y3j9h/2zmG1AWLmtYhdD4vTXi6fW4pwIDAQABAoIBAEaDZvA8noa3oG3T
N7SVGWUouAF0byptZKZjPDzIxYjretC6Pka7yTyjSMiJXmW1zQwnimLk/jcqZasb
t5VqQ3U2zYZU/BMpb3SlvB/jgqEUyf0MwZpzKanUJ4K8HjCX+Y1iPP8qvXBwWREH
FC+T5F9C0FwnsixCozhcBHzHLurnyyD9sfUeLl/P4K2g5BpgmTqXsC+/nOGYXIUL
aEhRI0N2MA0LwuOFKTm70D7sS83qPU7CbWmPjxcIm9J0PfRXl5lwPP43/w0Kdxok
Rx9ZvggMYkTEdZoXg9FMe+xMgMNG+XFWR8HpTSPscTo0YLxcrOIjb01yIiADTiKD
6biPgsECgYEA6XWPLMkyzBZeIHN3cEkc4NtxYqCVTx7bQs5cEM2VrXg8uptkAlD1
cyMeM3anVD3VeyqgiNvYwre5zT9rMIgeS8dHSorskEQPlmarnOuPQ3g1APVwx8Tc
ZB0CT3SCPLreEVWl+5w6cKdy8G2fwHbKE4J1Vop0yk0jrUETogKoeS0CgYEAuBUL
mkG5JhqFdulknYNmepgiq0lYcFY6H+BKX9Lz+Wlxzqf+BTyS98WQY1AG6w4r2QQ1
g/8zSNoIC9Ifil2ojd/CCig+5URA6UuPdVbLnSsRS4zqlWEhrIRSV7KyfTwSKi3X
qWc2RZSG5nEqps8dMa/9LlXCLx9BzvbxtRHrdaMCgYBiJpOgL6KUnR7Lo9/mLEbg
3kGIRp0fW4ixSJL6WYSBHtjhV20vcBwRYQVUe6ET0L9M/fnqMAusqZOLEAufpsqd
71UwqMGWUZcAE4A5A+wCYKpgEdBtnH1P9cY/42rR33p3bTvQUblcHXo8TMpqH0cL
9sAgNyus1cuXDpITfeyYrQKBgF5AyFQPWtGbusKL9iyAXzReOUIip9m9DL3NhcdS
qAEIcHEzqujbfxTGX4u6KhCojOLtOMmBHa0rlfsXd3bNRcR+0UeKG8ogDGxnd+jI
rDCpII3idSpPNYKzrcWhhaqB23slRctDQZKW3guX3hLS8UvDpIrY9jhSdMuXvWLM
7hapAoGAKAEK+H5f1GX3x2MNHbNe/0zPmtOIb8iZrFYrtdcAYqW6bizlgkz3YlpQ
lSaufC24F9hzbjAFeAfShCX3orXGXzgUFWMZKP7DkX+Roy8RQjK8DuzX6S0lLepE
O1GlC48osOzsNr62fFw5vYBMCQp6HyQz2E+TSuPyrhh7G9V5rGg=
-----END RSA PRIVATE KEY-----
"""

BOOT_ID = "0123456789abcdef0123456789abcdef"
COMMAND_ID = "fedcba9876543210fedcba9876543210"
SECRET = bytes(range(32))
with open(os.path.join(os.path.dirname(__file__), "..", "..", "contracts", "petcare-jetson-wire-v1.json"), encoding="utf-8") as _fixture:
    JPEG = base64.b64decode(json.load(_fixture)["observation"]["preview"]["body_base64"])


class Clock(object):
    wall = 1784520000.0
    mono = 1000.0

    def __init__(self):
        self.started = time.monotonic()

    def time(self):
        return self.wall + time.monotonic() - self.started

    def monotonic(self):
        return self.mono + time.monotonic() - self.started

    def monotonic_ns(self):
        return int(self.monotonic() * 1000000000)


class FakeProbe(object):
    def __init__(self):
        self.clock_synchronized = True
        self.temperature_c = 54.5
        self.throttled = False

    def __call__(self):
        return {
            "clock_synchronized": self.clock_synchronized,
            "temperature_c": self.temperature_c,
            "throttled": self.throttled,
        }


class FakeWriter(object):
    def __init__(self, media_path):
        self.media_path = media_path
        self.receipts = {}
        self.put_calls = []
        self.state = "idle"
        self.shutdown_calls = []

    def lookup_receipt(self, command_id, digest):
        value = self.receipts.get(command_id)
        if value is None:
            return None
        if value[0] != digest:
            raise CommandConflict("command_conflict")
        if value[2]:
            raise ClipGone("clip_gone")
        return dict(value[1])

    def put(self, command_id, digest, committed_at, event_type, event_id, occurred_at,
            received_wall_at, received_monotonic_ns):
        self.put_calls.append((received_wall_at, received_monotonic_ns))
        receipt = {
            "accepted_boot_id": BOOT_ID,
            "command_id": command_id,
            "state": "recording",
            "accepted_at": "2026-07-20T04:00:00.000000Z",
        }
        self.receipts[command_id] = (digest, receipt, False)
        self.state = "recording"
        return dict(receipt)

    def get(self, command_id):
        if command_id not in self.receipts:
            raise ClipGone("clip_gone")
        if self.receipts[command_id][2]:
            raise ClipGone("clip_gone")
        if self.state != "ready":
            raise ClipNotReady("clip_not_ready")
        return {
            "path": self.media_path,
            "content_length": 9,
            "content_sha256": hashlib.sha256(b"mp4-bytes").hexdigest(),
            "started_at": "2026-07-20T03:59:50.000000Z",
            "ended_at": "2026-07-20T04:00:20.000000Z",
            "events": "eating:41",
            "frame_count": 300,
            "width": 640,
            "height": 480,
            "frame_rate": "10/1",
            "duration_seconds": 30.0,
            "video_codec": "h264",
            "pixel_format": "yuv420p",
        }

    def delete(self, command_id):
        if command_id not in self.receipts:
            raise ClipGone("clip_gone")
        digest, receipt, unused = self.receipts[command_id]
        self.receipts[command_id] = (digest, receipt, True)

    def push(self, unused_bucket, unused_jpeg):
        return None

    def shutdown(self, timeout):
        self.shutdown_calls.append(timeout)


class VisionNodeHttpsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        cert = os.path.join(self.directory.name, "cert.pem")
        key = os.path.join(self.directory.name, "key.pem")
        media = os.path.join(self.directory.name, "clip.mp4")
        with open(cert, "w", encoding="ascii") as handle:
            handle.write(CERTIFICATE)
        with open(key, "w", encoding="ascii") as handle:
            handle.write(PRIVATE_KEY)
        with open(media, "wb") as handle:
            handle.write(b"mp4-bytes")
        self.cert = cert
        self.clock = Clock()
        self.probe = FakeProbe()
        self.writer = FakeWriter(media)
        self.node = VisionNode(
            SECRET,
            BOOT_ID,
            self.writer,
            self.probe,
            wall_clock=self.clock.time,
            monotonic_clock=self.clock.monotonic,
            monotonic_ns=self.clock.monotonic_ns,
        )
        self.node.set_camera_state(True)
        self.server = create_https_server(self.node, ("127.0.0.1", 0), cert, key)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        context = ssl.create_default_context(cafile=cert)
        context.check_hostname = True
        self.connection = http.client.HTTPSConnection(
            "127.0.0.1", self.server.server_address[1], context=context, timeout=2
        )
        self.nonce = 0

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.node.shutdown(timeout=1)
        self.directory.cleanup()

    def signed_headers(self, method, target, body=b"", boot_id=BOOT_ID):
        self.nonce += 1
        nonce = base64.urlsafe_b64encode(self.nonce.to_bytes(16, "big")).rstrip(b"=").decode("ascii")
        digest = hashlib.sha256(body).hexdigest()
        timestamp = str(int(self.clock.wall))
        canonical = "PETCARE-JETSON-V1\n{}\n{}\n{}\n{}\n{}\n{}\n".format(
            method, target, boot_id, timestamp, nonce, digest
        ).encode("utf-8")
        signature = base64.urlsafe_b64encode(hmac.new(SECRET, canonical, hashlib.sha256).digest()).rstrip(b"=").decode("ascii")
        return {
            "X-PetCare-Jetson-Version": "PETCARE-JETSON-V1",
            "X-PetCare-Jetson-Boot-Id": boot_id,
            "X-PetCare-Jetson-Timestamp": timestamp,
            "X-PetCare-Jetson-Nonce": nonce,
            "X-PetCare-Jetson-Content-SHA256": digest,
            "X-PetCare-Jetson-Signature": signature,
        }

    def request(self, method, target, body=b"", boot_id=BOOT_ID, headers=None):
        values = self.signed_headers(method, target, body, boot_id) if headers is None else headers
        self.connection.request(method, target, body=body, headers=values)
        response = self.connection.getresponse()
        content = response.read()
        return response, content

    def status_calibration(self):
        response, content = self.request("GET", "/v1/status", boot_id="bootstrap")
        self.assertEqual(response.status, 200)
        return json.loads(content.decode("utf-8"))

    def test_status_is_authenticated_strict_and_has_no_server_header(self):
        status = self.status_calibration()
        self.assertEqual(
            list(status),
            ["boot_id", "server_time", "camera_state", "clip_state", "jetpack", "l4t", "tensorrt", "temperature_c", "throttled"],
        )
        self.assertRegex(status["server_time"], r"^2026-07-20T04:00:00\.\d{6}Z$")
        response, content = self.request("GET", "/v1/status", headers={})
        self.assertEqual((response.status, json.loads(content)), (401, {"code": "unauthorized", "message": "Unauthorized"}))
        self.assertIsNone(response.getheader("Server"))

    def test_observation_long_poll_and_preview_exact_headers_are_bounded_to_two_fps(self):
        self.status_calibration()
        target = "/v1/observations?after=41&wait_ms=1000"
        response, content = self.request("GET", target)
        self.assertEqual((response.status, content), (204, b""))
        observation = {
            "boot_id": BOOT_ID,
            "sequence": 42,
            "observed_at": "2026-07-20T04:00:00.100000Z",
            "width": 640,
            "height": 480,
            "fps": 4.8,
            "inference_ms": 191.2,
            "detections": [{"detected_type": "dog", "confidence": 0.94, "bbox_x": 100, "bbox_y": 80, "bbox_width": 220, "bbox_height": 260}],
        }
        self.node.publish(observation, JPEG)
        response, content = self.request("GET", target)
        self.assertEqual((response.status, json.loads(content)), (200, observation))
        response, content = self.request("GET", "/v1/preview.jpg")
        self.assertEqual((response.status, content), (200, JPEG))
        self.assertEqual(
            set(dict(response.getheaders())),
            {"Date", "Connection", "Content-Type", "Content-Length", "Cache-Control", "X-PetCare-Jetson-Boot-Id", "X-PetCare-Jetson-Sequence", "X-PetCare-Jetson-Observed-At", "X-PetCare-Jetson-Content-SHA256"},
        )
        self.assertEqual(response.getheader("X-PetCare-Jetson-Content-SHA256"), hashlib.sha256(JPEG).hexdigest())
        response, content = self.request("GET", "/v1/preview.jpg")
        self.assertEqual((response.status, json.loads(content)["code"]), (503, "camera_unavailable"))
        self.clock.mono += 0.5
        response, unused = self.request("GET", "/v1/preview.jpg")
        self.assertEqual(response.status, 200)

    def test_first_put_requires_fresh_status_and_replay_bypasses_degraded_gate(self):
        body = b'{"committed_at":"2026-07-20T04:00:00.000000Z","event_id":41,"event_type":"eating","occurred_at":"2026-07-20T03:59:30.000000Z"}'
        target = "/v1/clips/" + COMMAND_ID
        response, content = self.request("PUT", target, body)
        self.assertEqual((response.status, json.loads(content)["code"]), (503, "camera_unavailable"))
        self.status_calibration()
        response, content = self.request("PUT", target, body)
        self.assertEqual((response.status, json.loads(content)["command_id"]), (201, COMMAND_ID))
        self.assertGreaterEqual(self.writer.put_calls[0][0], self.clock.wall)
        self.assertGreater(self.writer.put_calls[0][1], 0)
        self.probe.clock_synchronized = False
        self.probe.temperature_c = 90.0
        response, content = self.request("PUT", target, body)
        self.assertEqual((response.status, json.loads(content)["accepted_at"]), (200, "2026-07-20T04:00:00.000000Z"))
        self.assertEqual(len(self.writer.put_calls), 1)

    def test_wall_clock_step_at_first_put_blocks_admission_immediately(self):
        body = b'{"committed_at":"2026-07-20T04:00:00.000000Z","event_id":41,"event_type":"eating","occurred_at":"2026-07-20T03:59:30.000000Z"}'
        target = "/v1/clips/" + COMMAND_ID
        self.status_calibration()
        self.clock.wall += 0.2
        self.clock.mono += 0.1
        response, content = self.request("PUT", target, body)
        self.assertEqual((response.status, json.loads(content)["code"]), (503, "camera_unavailable"))
        self.assertEqual(self.writer.put_calls, [])

    def test_clip_get_delete_and_error_mappings_are_exact(self):
        body = b'{"committed_at":"2026-07-20T04:00:00.000000Z","event_id":41,"event_type":"eating","occurred_at":"2026-07-20T03:59:30.000000Z"}'
        target = "/v1/clips/" + COMMAND_ID
        self.status_calibration()
        self.request("PUT", target, body)
        response, content = self.request("GET", target)
        self.assertEqual((response.status, json.loads(content)["code"]), (425, "clip_not_ready"))
        self.writer.state = "ready"
        response, content = self.request("GET", target)
        self.assertEqual((response.status, content), (200, b"mp4-bytes"))
        self.assertIsNone(response.getheader("Server"))
        self.assertEqual(response.getheader("X-PetCare-Jetson-Events"), "eating:41")
        response, content = self.request("DELETE", target)
        self.assertEqual((response.status, content), (204, b""))
        response, content = self.request("GET", target)
        self.assertEqual((response.status, json.loads(content)["code"]), (410, "clip_gone"))

    def test_unknown_path_method_and_malformed_requests_are_bounded(self):
        response, content = self.request("GET", "/v1/nope")
        self.assertEqual((response.status, json.loads(content)["code"]), (404, "invalid_request"))
        response, content = self.request("GET", "/v1/nope", headers={})
        self.assertEqual((response.status, json.loads(content)["code"]), (401, "unauthorized"))
        response, content = self.request("POST", "/v1/status", b"{}")
        self.assertEqual((response.status, json.loads(content)["code"]), (405, "invalid_request"))
        response, content = self.request("PUT", "/v1/clips/" + COMMAND_ID, b"x" * 4097)
        self.assertEqual((response.status, json.loads(content)["code"]), (400, "invalid_request"))

    def test_arbitrary_unsupported_method_and_oversized_header_never_use_default_server_errors(self):
        unsupported = self.signed_headers("FOO", "/v1/status")
        response, content = self.request("FOO", "/v1/status", headers=unsupported)
        self.assertEqual((response.status, json.loads(content)["code"]), (405, "invalid_request"))
        self.assertIsNone(response.getheader("Server"))
        response, content = self.request("FOO", "/v1/status", headers=unsupported)
        self.assertEqual((response.status, json.loads(content)["code"]), (401, "unauthorized"))
        headers = self.signed_headers("GET", "/v1/status", boot_id="bootstrap")
        headers["X-Junk"] = "x" * 4097
        response, content = self.request("GET", "/v1/status", headers=headers)
        self.assertEqual((response.status, json.loads(content)["code"]), (400, "invalid_request"))
        self.assertIsNone(response.getheader("Server"))

    def test_server_close_aborts_an_idle_client_without_waiting_for_socket_timeout(self):
        context = ssl.create_default_context(cafile=self.cert)
        idle = context.wrap_socket(
            socket.create_connection(self.server.server_address, timeout=2),
            server_hostname="127.0.0.1",
        )
        started = time.monotonic()
        self.server.shutdown()
        self.server.server_close()
        elapsed = time.monotonic() - started
        idle.close()
        self.assertLess(elapsed, 2.0)
        self.assertEqual(self.server.active_request_count, 0)


class CameraRecoveryTests(unittest.TestCase):
    def test_failed_read_reopens_the_same_webcam_for_unplug_replug(self):
        captures = []

        class Capture(object):
            def __init__(self, succeeds):
                self.succeeds = succeeds
                self.released = False

            def set(self, unused_name, unused_value):
                return True

            def isOpened(self):
                return True

            def read(self):
                if self.succeeds:
                    return True, type("Frame", (), {"shape": (480, 640, 3)})()
                return False, None

            def release(self):
                self.released = True

        class Cv2(object):
            CAP_PROP_FRAME_WIDTH = 1
            CAP_PROP_FRAME_HEIGHT = 2

            @staticmethod
            def VideoCapture(unused_device):
                capture = Capture(bool(captures))
                captures.append(capture)
                return capture

        previous = sys.modules.get("cv2")
        sys.modules["cv2"] = Cv2
        try:
            camera = OpenCvCamera("/dev/video0")
            with self.assertRaisesRegex(RuntimeError, "camera_unavailable"):
                camera.read()
            frame = camera.read()
            self.assertEqual(frame.shape, (480, 640, 3))
            self.assertEqual(len(captures), 2)
            self.assertTrue(captures[0].released)
        finally:
            if previous is None:
                del sys.modules["cv2"]
            else:
                sys.modules["cv2"] = previous

    def test_observed_at_is_captured_before_slow_inference(self):
        clock = Clock()
        clock.time = lambda: clock.wall
        closed = threading.Event()

        class Camera(object):
            calls = 0

            def read(self):
                self.calls += 1
                if self.calls == 1:
                    return object()
                closed.wait(1)
                raise RuntimeError("camera_unavailable")

            def close(self):
                closed.set()

        class Detector(object):
            def infer(self, unused_frame):
                clock.wall += 2.0
                return []

            def close(self):
                return None

        writer = FakeWriter("")
        node = VisionNode(
            SECRET,
            BOOT_ID,
            writer,
            FakeProbe(),
            wall_clock=clock.time,
            monotonic_clock=clock.monotonic,
            monotonic_ns=clock.monotonic_ns,
            camera=Camera(),
            detector=Detector(),
            renderer=lambda unused_frame, unused_detections: JPEG,
        )
        node.start()
        observation = node.observation(-1, 1000)
        node.shutdown(timeout=1)
        self.assertEqual(observation["observed_at"], "2026-07-20T04:00:00.000000Z")


class ConfigurationTests(unittest.TestCase):
    def test_runtime_configuration_rejects_non_rfc1918_bind(self):
        value = {
            "bind_ip": "0.0.0.0",
            "port": 9443,
            "webcam": "/dev/video0",
            "certificate_path": "/var/lib/petcare-vision/device.crt",
            "private_key_path": "/var/lib/petcare-vision/device.key",
            "psk_path": "/var/lib/petcare-vision/psk.bin",
            "engine_path": "/opt/petcare-vision/model.engine",
            "engine_metadata_path": "/opt/petcare-vision/model.engine.json",
            "state_dir": "/var/lib/petcare-vision",
            "temperature_path": "/sys/devices/virtual/thermal/thermal_zone0/temp",
            "max_temperature_c": 80.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(value, handle)
            with self.assertRaisesRegex(ValueError, "invalid_configuration"):
                _configuration(path)


if __name__ == "__main__":
    unittest.main()
