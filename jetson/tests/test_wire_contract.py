import base64
import hashlib
import hmac
import json
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "contracts", "petcare-jetson-wire-v1.json")
AGENT_FIXTURE = os.path.join(ROOT, "contracts", "petcare-agent-wire-v1.json")
UNRESERVED = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"


def load_fixture():
    with open(FIXTURE, "r", encoding="utf-8") as handle:
        return json.load(handle)


def b64url_decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def quote(value):
    return "".join(chr(byte) if byte in UNRESERVED else "%{:02X}".format(byte) for byte in value.encode("utf-8"))


def canonical_query(pairs):
    encoded = sorted((quote(key), quote(value)) for key, value in pairs)
    return "&".join("{}={}".format(key, value) for key, value in encoded)


def first_put_admitted(admission, calibration_age, offset_ms, half_rtt_ms, wall_age, discontinuity=False):
    uncertainty = abs(offset_ms) + half_rtt_ms + admission["drift_budget_ms"]
    return (
        not discontinuity
        and calibration_age <= admission["calibration_max_age_seconds"]
        and uncertainty <= admission["uncertainty_max_ms"]
        and admission["wall_age_min_seconds"] <= wall_age <= admission["wall_age_max_seconds"]
    )


def replay_status(replay, state, same_digest):
    if not same_digest:
        return replay["changed_digest_status"]
    if state in replay["identical_active_states"]:
        return replay["identical_status"]
    if state in replay["gone_states"]:
        return replay["gone_status"]
    raise AssertionError("unknown replay state")


def jpeg_shape(data):
    assert data[:2] == b"\xff\xd8"
    offset = 2
    while offset < len(data):
        assert data[offset] == 0xFF
        marker = data[offset + 1]
        offset += 2
        if marker in (0xD8, 0xD9):
            continue
        length = int.from_bytes(data[offset : offset + 2], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            return (
                int.from_bytes(data[offset + 3 : offset + 5], "big"),
                int.from_bytes(data[offset + 5 : offset + 7], "big"),
                data[offset + 7],
            )
        offset += length
    raise AssertionError("JPEG has no SOF marker")


class WireContractTest(unittest.TestCase):
    def test_auth_vector_and_exact_six_operations(self):
        fixture = load_fixture()
        self.assertEqual(list(fixture), ["auth", "status", "observation", "command", "clip", "errors"])
        auth = fixture["auth"]
        self.assertEqual(list(auth), ["version", "secret_base64url", "request", "canonical_query", "operations"])
        request = auth["request"]
        self.assertEqual(list(request), [
            "method", "target", "boot_id", "timestamp", "nonce", "body", "body_json",
            "body_sha256", "canonical", "signature", "headers",
        ])
        self.assertEqual(list(request["body"]), ["committed_at", "event_id", "event_type", "occurred_at"])
        body = json.dumps(request["body"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        digest = hashlib.sha256(body).hexdigest()
        self.assertEqual(body.decode(), request["body_json"])
        self.assertEqual(digest, request["body_sha256"])
        canonical = "{}\n{}\n{}\n{}\n{}\n{}\n{}\n".format(
            auth["version"],
            request["method"],
            request["target"],
            request["boot_id"],
            request["timestamp"],
            request["nonce"],
            digest,
        ).encode()
        self.assertEqual(canonical.decode(), request["canonical"])
        signature = b64url_encode(hmac.new(b64url_decode(auth["secret_base64url"]), canonical, hashlib.sha256).digest())
        self.assertEqual(signature, request["signature"])
        self.assertEqual(signature, "jRDgkQ3q6mrGL7rQxGtI1QANRKamx9ieVseiURXrnzE")
        self.assertEqual(list(request["headers"]), [
            "X-PetCare-Jetson-Version",
            "X-PetCare-Jetson-Boot-Id",
            "X-PetCare-Jetson-Timestamp",
            "X-PetCare-Jetson-Nonce",
            "X-PetCare-Jetson-Content-SHA256",
            "X-PetCare-Jetson-Signature",
        ])
        headers = request["headers"]
        self.assertEqual(headers["X-PetCare-Jetson-Version"], auth["version"])
        self.assertEqual(headers["X-PetCare-Jetson-Boot-Id"], request["boot_id"])
        self.assertEqual(len(request["boot_id"]), 32)
        self.assertTrue(set(request["boot_id"]) <= set("0123456789abcdef"))
        self.assertEqual(headers["X-PetCare-Jetson-Timestamp"], request["timestamp"])
        self.assertTrue(headers["X-PetCare-Jetson-Timestamp"].isdigit())
        self.assertEqual(headers["X-PetCare-Jetson-Nonce"], request["nonce"])
        self.assertEqual(len(b64url_decode(headers["X-PetCare-Jetson-Nonce"])), 16)
        self.assertEqual(headers["X-PetCare-Jetson-Content-SHA256"], digest)
        self.assertEqual(headers["X-PetCare-Jetson-Content-SHA256"], request["body_sha256"])
        self.assertEqual(len(digest), 64)
        self.assertTrue(set(digest) <= set("0123456789abcdef"))
        self.assertEqual(headers["X-PetCare-Jetson-Signature"], request["signature"])
        self.assertEqual(len(b64url_decode(headers["X-PetCare-Jetson-Signature"])), 32)
        query = auth["canonical_query"]
        self.assertEqual(list(query), ["pairs", "value", "target", "empty_body_sha256", "canonical", "signature"])
        self.assertEqual(canonical_query(query["pairs"]), query["value"])
        self.assertEqual(query["target"], "/v1/observations?" + query["value"])
        self.assertEqual(query["empty_body_sha256"], hashlib.sha256(b"").hexdigest())
        query_canonical = "{}\nGET\n{}\n{}\n{}\n{}\n{}\n".format(
            auth["version"], query["target"], request["boot_id"], request["timestamp"], request["nonce"], query["empty_body_sha256"]
        ).encode()
        self.assertEqual(query_canonical.decode(), query["canonical"])
        query_signature = b64url_encode(hmac.new(b64url_decode(auth["secret_base64url"]), query_canonical, hashlib.sha256).digest())
        self.assertEqual(query_signature, query["signature"])
        self.assertEqual(auth["operations"], [
            {"method": "GET", "target": "/v1/status", "success": [200]},
            {"method": "GET", "target": "/v1/observations?after=42&wait_ms=1000", "success": [200, 204]},
            {"method": "GET", "target": "/v1/preview.jpg", "success": [200]},
            {"method": "PUT", "target": "/v1/clips/fedcba9876543210fedcba9876543210", "success": [201, 200]},
            {"method": "GET", "target": "/v1/clips/fedcba9876543210fedcba9876543210", "success": [200]},
            {"method": "DELETE", "target": "/v1/clips/fedcba9876543210fedcba9876543210", "success": [204]},
        ])

    def test_strict_json_and_preview_contracts(self):
        fixture = load_fixture()
        self.assertEqual(list(fixture["status"]), ["boot_id", "server_time", "camera_state", "clip_state", "jetpack", "l4t", "tensorrt", "temperature_c", "throttled"])
        observation = fixture["observation"]
        self.assertEqual(list(observation), ["body", "preview"])
        body = observation["body"]
        self.assertEqual(list(body), ["boot_id", "sequence", "observed_at", "width", "height", "fps", "inference_ms", "detections"])
        self.assertEqual(list(body["detections"][0]), ["detected_type", "confidence", "bbox_x", "bbox_y", "bbox_width", "bbox_height"])
        preview = observation["preview"]
        self.assertEqual(list(preview), ["body_base64", "max_content_length", "max_fps", "headers", "ignored_transport_headers"])
        jpeg = base64.b64decode(preview["body_base64"])
        self.assertEqual(jpeg_shape(jpeg), (480, 640, 3))
        self.assertEqual(len(jpeg), 2097)
        self.assertEqual(hashlib.sha256(jpeg).hexdigest(), "e8f2ea5a7c0511f2e32a41933ef8af70a1b96fc3acb9523a89dfb9d8aeb63cfd")
        self.assertTrue(0 < len(jpeg) <= preview["max_content_length"] == 1048576)
        headers = preview["headers"]
        self.assertEqual(list(headers), [
            "Content-Type",
            "Content-Length",
            "Cache-Control",
            "X-PetCare-Jetson-Boot-Id",
            "X-PetCare-Jetson-Sequence",
            "X-PetCare-Jetson-Observed-At",
            "X-PetCare-Jetson-Content-SHA256",
        ])
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(int(headers["Content-Length"]), len(jpeg))
        self.assertEqual(headers["Cache-Control"], "private, no-store, no-transform")
        self.assertEqual(headers["X-PetCare-Jetson-Boot-Id"], body["boot_id"])
        self.assertEqual(headers["X-PetCare-Jetson-Sequence"], str(body["sequence"]))
        self.assertEqual(headers["X-PetCare-Jetson-Observed-At"], body["observed_at"])
        self.assertEqual(headers["X-PetCare-Jetson-Content-SHA256"], hashlib.sha256(jpeg).hexdigest())
        self.assertEqual(preview["ignored_transport_headers"], ["Date", "Connection"])
        self.assertEqual(preview["max_fps"], 2)

    def test_command_admission_and_idempotency_contract(self):
        command = load_fixture()["command"]
        self.assertEqual(list(command), ["request", "outbox_created_at", "allowed_event_types", "response", "first_status", "admission", "receipt_capture", "replay"])
        self.assertEqual(list(command["request"]), ["committed_at", "event_id", "event_type", "occurred_at"])
        self.assertEqual(command["request"]["committed_at"], command["outbox_created_at"])
        self.assertEqual(command["allowed_event_types"], ["eating", "resting", "bed_sensor_mismatch"])
        self.assertNotIn("no_meal_12h", command["allowed_event_types"])
        self.assertEqual(list(command["response"]), ["accepted_boot_id", "command_id", "state", "accepted_at"])
        self.assertEqual(command["first_status"], 201)
        admission = command["admission"]
        self.assertEqual(admission, {
            "first_put_requires_fresh_calibration": True,
            "calibration_max_age_seconds": 1.0,
            "midpoint_offset_ms": -100,
            "half_rtt_ms": 49,
            "drift_budget_ms": 50,
            "uncertainty_max_ms": 200,
            "guard_sample_ms": 100,
            "discontinuity_threshold_ms": 25,
            "discontinuity_disable_seconds": 60,
            "wall_age_min_seconds": -0.2,
            "wall_age_max_seconds": 2.8,
        })
        self.assertEqual(admission["calibration_max_age_seconds"], 1.0)
        self.assertLessEqual(abs(admission["midpoint_offset_ms"]) + admission["half_rtt_ms"] + admission["drift_budget_ms"], admission["uncertainty_max_ms"])
        self.assertEqual((admission["wall_age_min_seconds"], admission["wall_age_max_seconds"]), (-0.2, 2.8))
        self.assertEqual((admission["guard_sample_ms"], admission["discontinuity_threshold_ms"], admission["discontinuity_disable_seconds"]), (100, 25, 60))
        self.assertTrue(first_put_admitted(admission, 1.0, -100, 50, -0.2))
        self.assertTrue(first_put_admitted(admission, 1.0, 100, 50, 2.8))
        self.assertFalse(first_put_admitted(admission, 1.000001, 0, 0, 0))
        self.assertFalse(first_put_admitted(admission, 1.0, 100.001, 50, 0))
        self.assertFalse(first_put_admitted(admission, 1.0, 0, 0, -0.200001))
        self.assertFalse(first_put_admitted(admission, 1.0, 0, 0, 2.800001))
        self.assertFalse(first_put_admitted(admission, 1.0, 0, 0, 0, discontinuity=True))
        capture = command["receipt_capture"]
        self.assertEqual(list(capture), ["socket_received_at", "accepted_monotonic_ns", "sampler_period_ns", "trigger_bucket"])
        self.assertEqual(capture["socket_received_at"], command["response"]["accepted_at"])
        period = capture["sampler_period_ns"]
        self.assertEqual(capture["trigger_bucket"], (capture["accepted_monotonic_ns"] + period - 1) // period)
        replay = command["replay"]
        self.assertEqual(replay, {
            "identical_active_states": ["recording", "finalizing", "ready"],
            "identical_status": 200,
            "reruns_age_test": False,
            "receipt_fields": ["accepted_boot_id", "command_id", "state", "accepted_at"],
            "gone_states": ["delivered", "expired", "restart_gone"],
            "gone_status": 410,
            "changed_digest_status": 409,
        })
        for state in replay["identical_active_states"]:
            self.assertEqual(replay_status(replay, state, True), 200)
        for state in replay["gone_states"]:
            self.assertEqual(replay_status(replay, state, True), 410)
        self.assertEqual(replay_status(replay, "recording", False), 409)

    def test_clip_error_and_cloud_contract_separation(self):
        fixture = load_fixture()
        clip = fixture["clip"]
        self.assertEqual(list(clip), ["body_base64", "body_sha256", "max_content_length", "headers", "ignored_transport_headers", "media", "wire_body_is_valid_mp4"])
        body = base64.b64decode(clip["body_base64"])
        self.assertEqual(body, b"mp4-bytes")
        self.assertEqual(hashlib.sha256(body).hexdigest(), clip["body_sha256"])
        headers = clip["headers"]
        self.assertEqual(list(headers), [
            "Content-Type",
            "Content-Length",
            "X-PetCare-Jetson-Boot-Id",
            "X-PetCare-Jetson-Command-Id",
            "X-PetCare-Jetson-Content-SHA256",
            "X-PetCare-Jetson-Started-At",
            "X-PetCare-Jetson-Ended-At",
            "X-PetCare-Jetson-Events",
            "X-PetCare-Jetson-Frame-Count",
            "X-PetCare-Jetson-Video-Codec",
            "X-PetCare-Jetson-Pixel-Format",
        ])
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertEqual(headers["X-PetCare-Jetson-Content-SHA256"], clip["body_sha256"])
        self.assertEqual(headers["X-PetCare-Jetson-Boot-Id"], fixture["command"]["response"]["accepted_boot_id"])
        self.assertEqual(headers["X-PetCare-Jetson-Command-Id"], fixture["command"]["response"]["command_id"])
        self.assertEqual(clip["max_content_length"], 268435456)
        self.assertEqual(clip["ignored_transport_headers"], ["Date", "Connection"])
        self.assertEqual(clip["media"], {
            "width": 640, "height": 480, "frame_count": 300, "frame_rate": "10/1",
            "duration_seconds": 30.0, "duration_tolerance_ms": 100, "video_codec": "h264",
            "pixel_format": "yuv420p", "started_at": "2026-07-20T03:59:50.000000Z",
            "ended_at": "2026-07-20T04:00:20.000000Z", "events": "eating:41",
        })
        self.assertEqual(headers["X-PetCare-Jetson-Started-At"], clip["media"]["started_at"])
        self.assertEqual(headers["X-PetCare-Jetson-Ended-At"], clip["media"]["ended_at"])
        self.assertEqual(headers["X-PetCare-Jetson-Events"], clip["media"]["events"])
        self.assertEqual(headers["X-PetCare-Jetson-Frame-Count"], str(clip["media"]["frame_count"]))
        self.assertEqual(headers["X-PetCare-Jetson-Video-Codec"], clip["media"]["video_codec"])
        self.assertEqual(headers["X-PetCare-Jetson-Pixel-Format"], clip["media"]["pixel_format"])
        self.assertFalse(clip["wire_body_is_valid_mp4"])
        errors = fixture["errors"]
        self.assertEqual(list(errors), ["external_codes", "auth_internal_reasons", "external_by_internal_reason", "unauthorized", "status_by_code"])
        self.assertEqual(errors["external_codes"], [
            "invalid_request", "unauthorized", "command_conflict", "command_expired", "camera_unavailable",
            "clip_busy", "clip_not_ready", "clip_gone", "internal_error",
        ])
        self.assertEqual(errors["auth_internal_reasons"], ["stale_request", "replayed_request", "wrong_boot"])
        self.assertEqual(errors["unauthorized"], {"status": 401, "body": {"code": "unauthorized", "message": "Unauthorized"}})
        self.assertEqual(list(errors["external_by_internal_reason"]), errors["auth_internal_reasons"])
        self.assertTrue(all(value == errors["unauthorized"] for value in errors["external_by_internal_reason"].values()))
        self.assertEqual(errors["status_by_code"], {
            "invalid_request": 400, "unauthorized": 401, "command_conflict": 409,
            "command_expired": 409, "camera_unavailable": 503, "clip_busy": 503,
            "clip_not_ready": 425, "clip_gone": 410, "internal_error": 500,
        })
        with open(AGENT_FIXTURE, "rb") as handle:
            agent_bytes = handle.read()
        self.assertEqual(hashlib.sha256(agent_bytes).hexdigest(), "d9849424f38a2f99b844c4705eb0652bf245b74ece6173c62e0271d1db7e2e4b")
        agent = json.loads(agent_bytes.decode("utf-8"))
        self.assertEqual(agent["clip"]["version"], "PETCARE-CLIP-V1")
        self.assertNotIn("PETCARE-JETSON-V1", agent_bytes.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
