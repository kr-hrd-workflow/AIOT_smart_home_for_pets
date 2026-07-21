import base64
import hashlib
import hmac
import json
import os
import unittest

from jetson.protocol import ProtocolError, ReplayGuard, verify_request


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "contracts", "petcare-jetson-wire-v1.json")


def b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE, "r", encoding="utf-8") as handle:
            self.fixture = json.load(handle)["auth"]
        self.request = self.fixture["request"]
        self.secret = base64.urlsafe_b64decode(self.fixture["secret_base64url"] + "=")

    def signed_headers(self, method, target, body=b"", boot_id=None, timestamp="1000", nonce=None):
        boot_id = boot_id or self.request["boot_id"]
        nonce = nonce or b64url(b"0123456789abcdef")
        digest = hashlib.sha256(body).hexdigest()
        canonical = "{}\n{}\n{}\n{}\n{}\n{}\n{}\n".format(
            self.fixture["version"], method, target, boot_id, timestamp, nonce, digest
        ).encode("utf-8")
        return {
            "X-PetCare-Jetson-Version": self.fixture["version"],
            "X-PetCare-Jetson-Boot-Id": boot_id,
            "X-PetCare-Jetson-Timestamp": timestamp,
            "X-PetCare-Jetson-Nonce": nonce,
            "X-PetCare-Jetson-Content-SHA256": digest,
            "X-PetCare-Jetson-Signature": b64url(hmac.new(self.secret, canonical, hashlib.sha256).digest()),
        }

    def assert_code(self, code, method, target, headers, body=b"", now=1000):
        with self.assertRaises(ProtocolError) as raised:
            argument = list(headers.items()) if type(headers) is dict else headers
            verify_request(method, target, argument, body, self.secret, self.request["boot_id"], now, ReplayGuard())
        self.assertEqual(str(raised.exception), code)

    def test_golden_vector_and_canonical_query_are_accepted(self):
        verify_request(
            self.request["method"], self.request["target"], list(self.request["headers"].items()),
            self.request["body_json"].encode("utf-8"), self.secret, self.request["boot_id"],
            int(self.request["timestamp"]), ReplayGuard(),
        )
        query = self.fixture["canonical_query"]
        headers = self.signed_headers("GET", query["target"], timestamp=self.request["timestamp"])
        headers["X-PetCare-Jetson-Nonce"] = self.request["nonce"]
        headers["X-PetCare-Jetson-Signature"] = query["signature"]
        verify_request("GET", query["target"], list(headers.items()), b"", self.secret, self.request["boot_id"],
                       int(self.request["timestamp"]), ReplayGuard())

    def test_missing_duplicate_and_malformed_auth_headers_are_unauthorized(self):
        headers = self.signed_headers("GET", "/v1/preview.jpg")
        with self.assertRaisesRegex(ProtocolError, "^unauthorized$"):
            verify_request("GET", "/v1/preview.jpg", headers, b"", self.secret,
                           self.request["boot_id"], 1000, ReplayGuard())
        missing = dict(headers)
        del missing["X-PetCare-Jetson-Nonce"]
        self.assert_code("unauthorized", "GET", "/v1/preview.jpg", missing)

        pairs = list(headers.items()) + [("x-petcare-jetson-nonce", headers["X-PetCare-Jetson-Nonce"])]
        self.assert_code("unauthorized", "GET", "/v1/preview.jpg", pairs)

        for name, value in (
            ("X-PetCare-Jetson-Boot-Id", "A" * 32),
            ("X-PetCare-Jetson-Timestamp", "+1000"),
            ("X-PetCare-Jetson-Nonce", "A" * 21 + "="),
            ("X-PetCare-Jetson-Content-SHA256", "A" * 64),
            ("X-PetCare-Jetson-Signature", "A" * 42 + "="),
        ):
            malformed = dict(headers)
            malformed[name] = value
            self.assert_code("unauthorized", "GET", "/v1/preview.jpg", malformed)

    def test_digest_signature_boot_and_time_failures_do_not_leak_credentials(self):
        headers = self.signed_headers("GET", "/v1/preview.jpg")
        cases = []
        bad_digest = dict(headers)
        bad_digest["X-PetCare-Jetson-Content-SHA256"] = "0" * 64
        cases.append(("unauthorized", bad_digest, 1000))
        bad_signature = dict(headers)
        bad_signature["X-PetCare-Jetson-Signature"] = "A" * 43
        cases.append(("unauthorized", bad_signature, 1000))
        wrong_boot = self.signed_headers("GET", "/v1/preview.jpg", boot_id="f" * 32)
        cases.append(("wrong_boot", wrong_boot, 1000))
        cases.append(("stale_request", headers, 1031))
        for code, candidate, now in cases:
            with self.assertRaises(ProtocolError) as raised:
                verify_request("GET", "/v1/preview.jpg", list(candidate.items()), b"", self.secret,
                               self.request["boot_id"], now, ReplayGuard())
            self.assertEqual(str(raised.exception), code)
            rendered = repr(raised.exception)
            self.assertNotIn(candidate["X-PetCare-Jetson-Signature"], rendered)
            self.assertNotIn(self.fixture["secret_base64url"], rendered)

    def test_time_boundary_bootstrap_and_replay_boundaries(self):
        for now in (970, 1030):
            verify_request("GET", "/v1/preview.jpg", list(self.signed_headers("GET", "/v1/preview.jpg").items()),
                           b"", self.secret, self.request["boot_id"], now, ReplayGuard())
        for now in (969, 1031):
            self.assert_code("stale_request", "GET", "/v1/preview.jpg",
                             self.signed_headers("GET", "/v1/preview.jpg"), now=now)

        status = self.signed_headers("GET", "/v1/status", boot_id="bootstrap")
        verify_request("GET", "/v1/status", list(status.items()), b"", self.secret, self.request["boot_id"], 1000, ReplayGuard())
        self.assert_code("wrong_boot", "GET", "/v1/preview.jpg",
                         self.signed_headers("GET", "/v1/preview.jpg", boot_id="bootstrap"))

        class Clock(object):
            value = 0.0

            def __call__(self):
                return self.value

        clock = Clock()
        guard = ReplayGuard(clock)
        first = self.signed_headers("GET", "/v1/preview.jpg", timestamp="1000")
        verify_request("GET", "/v1/preview.jpg", list(first.items()), b"", self.secret, self.request["boot_id"], 1000, guard)
        clock.value = 120.0
        boundary = self.signed_headers("GET", "/v1/preview.jpg", timestamp="1120")
        with self.assertRaisesRegex(ProtocolError, "^replayed_request$"):
            verify_request("GET", "/v1/preview.jpg", list(boundary.items()), b"", self.secret, self.request["boot_id"], 1120, guard)
        clock.value = 120.000001
        later = self.signed_headers("GET", "/v1/preview.jpg", timestamp="1121")
        verify_request("GET", "/v1/preview.jpg", list(later.items()), b"", self.secret, self.request["boot_id"], 1121, guard)

    def test_invalid_signature_does_not_consume_nonce_and_boot_change_resets_guard(self):
        guard = ReplayGuard()
        valid = self.signed_headers("GET", "/v1/preview.jpg")
        invalid = dict(valid)
        invalid["X-PetCare-Jetson-Signature"] = "A" * 43
        with self.assertRaisesRegex(ProtocolError, "^unauthorized$"):
            verify_request("GET", "/v1/preview.jpg", list(invalid.items()), b"", self.secret,
                           self.request["boot_id"], 1000, guard)
        verify_request("GET", "/v1/preview.jpg", list(valid.items()), b"", self.secret,
                       self.request["boot_id"], 1000, guard)
        new_boot = "f" * 32
        changed = self.signed_headers("GET", "/v1/preview.jpg", boot_id=new_boot)
        verify_request("GET", "/v1/preview.jpg", list(changed.items()), b"", self.secret, new_boot, 1000, guard)

    def test_replay_ttl_uses_monotonic_time_across_wall_clock_steps(self):
        class Clock(object):
            value = 10.0

            def __call__(self):
                return self.value

        clock = Clock()
        guard = ReplayGuard(clock)
        nonce = b64url(b"0123456789abcdef")
        first = self.signed_headers("GET", "/v1/preview.jpg", timestamp="100", nonce=nonce)
        verify_request("GET", "/v1/preview.jpg", list(first.items()), b"", self.secret,
                       self.request["boot_id"], 100, guard)
        clock.value = 20.0
        forward = self.signed_headers("GET", "/v1/preview.jpg", timestamp="1000", nonce=b64url(b"fedcba9876543210"))
        verify_request("GET", "/v1/preview.jpg", list(forward.items()), b"", self.secret,
                       self.request["boot_id"], 1000, guard)
        replay = self.signed_headers("GET", "/v1/preview.jpg", timestamp="100", nonce=nonce)
        with self.assertRaisesRegex(ProtocolError, "^replayed_request$"):
            verify_request("GET", "/v1/preview.jpg", list(replay.items()), b"", self.secret,
                           self.request["boot_id"], 100, guard)
        clock.value = 130.000001
        verify_request("GET", "/v1/preview.jpg", list(replay.items()), b"", self.secret,
                       self.request["boot_id"], 100, guard)

    def test_noncanonical_targets_and_strict_clip_json_are_rejected(self):
        for target in (
            "/v1/observations?z=+",
            "/v1/observations?z=%2f",
            "/v1/observations?z=%7E",
            "/v1/observations?b=1&a=1",
            "/v1/observations?a=%ZZ",
            "https://127.0.0.1/v1/status",
            "/v1/status#fragment",
        ):
            self.assert_code("invalid_request", "GET", target, self.signed_headers("GET", target))

        target = "/v1/clips/" + "a" * 32
        valid = {
            "committed_at": "2026-07-20T04:00:00.000000Z",
            "event_id": 41,
            "event_type": "eating",
            "occurred_at": "2026-07-20T03:59:30.000000Z",
        }
        for body in (
            b"{" + b" " * 4095 + b"}",
            b'{"committed_at":"2026-07-20T04:00:00.000000Z","event_id":true,"event_type":"eating","occurred_at":"2026-07-20T03:59:30.000000Z"}',
            b'{"committed_at":"2026-07-20T04:00:00.000000Z","event_id":41,"event_id":42,"event_type":"eating","occurred_at":"2026-07-20T03:59:30.000000Z"}',
            json.dumps(dict(valid, extra=1), sort_keys=True, separators=(",", ":")).encode("utf-8"),
            json.dumps(dict(valid, event_type="no_meal_12h"), sort_keys=True, separators=(",", ":")).encode("utf-8"),
        ):
            self.assert_code("invalid_request", "PUT", target, self.signed_headers("PUT", target, body), body)


if __name__ == "__main__":
    unittest.main()
