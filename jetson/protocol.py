import base64
import binascii
import datetime
import hashlib
import hmac
import json
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import parse_qsl, quote, urlparse


VERSION = "PETCARE-JETSON-V1"
MAX_BODY_BYTES = 4096
REPLAY_SECONDS = 120
HEADER_NAMES = (
    "X-PetCare-Jetson-Version",
    "X-PetCare-Jetson-Boot-Id",
    "X-PetCare-Jetson-Timestamp",
    "X-PetCare-Jetson-Nonce",
    "X-PetCare-Jetson-Content-SHA256",
    "X-PetCare-Jetson-Signature",
)
HEADER_LOOKUP = dict((name.lower(), name) for name in HEADER_NAMES)
HEX_32 = re.compile(r"^[0-9a-f]{32}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
BASE64URL_22 = re.compile(r"^[A-Za-z0-9_-]{22}$")
BASE64URL_43 = re.compile(r"^[A-Za-z0-9_-]{43}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
CLIP_TARGET = re.compile(r"^/v1/clips/[0-9a-f]{32}$")
ALLOWED_EVENTS = frozenset(("eating", "resting", "bed_sensor_mismatch"))


class ProtocolError(Exception):
    def __init__(self, code):
        self.code = code
        Exception.__init__(self, code)


class ReplayGuard(object):
    def __init__(self, monotonic_clock=None):
        self._boot_id = None
        self._nonces = OrderedDict()
        self._lock = threading.Lock()
        self._clock = monotonic_clock or time.monotonic

    def check_and_add(self, boot_id, nonce, unused_now_unix=None):
        with self._lock:
            now_monotonic = float(self._clock())
            if boot_id != self._boot_id:
                self._boot_id = boot_id
                self._nonces.clear()
            cutoff = now_monotonic - REPLAY_SECONDS
            while self._nonces:
                unused_nonce, accepted_at = next(iter(self._nonces.items()))
                if accepted_at >= cutoff:
                    break
                self._nonces.popitem(last=False)
            if nonce in self._nonces:
                raise ProtocolError("replayed_request")
            self._nonces[nonce] = now_monotonic

    @property
    def size(self):
        with self._lock:
            return len(self._nonces)


def _header_pairs(headers):
    if type(headers) is dict:
        raise ProtocolError("unauthorized")
    if hasattr(headers, "raw_items"):
        return list(headers.raw_items())
    if hasattr(headers, "get_all"):
        pairs = []
        for name in HEADER_NAMES:
            for value in headers.get_all(name, []):
                pairs.append((name, value))
        return pairs
    if hasattr(headers, "items"):
        return list(headers.items())
    try:
        return list(headers)
    except TypeError:
        raise ProtocolError("unauthorized")


def _required_headers(headers):
    values = {}
    for pair in _header_pairs(headers):
        if type(pair) not in (tuple, list) or len(pair) != 2:
            raise ProtocolError("unauthorized")
        name, value = pair
        if type(name) is not str or type(value) is not str:
            raise ProtocolError("unauthorized")
        canonical = HEADER_LOOKUP.get(name.lower())
        if canonical is None:
            if name.lower().startswith("x-petcare-jetson-"):
                raise ProtocolError("unauthorized")
            continue
        if canonical in values:
            raise ProtocolError("unauthorized")
        values[canonical] = value
    if set(values) != set(HEADER_NAMES):
        raise ProtocolError("unauthorized")
    return values


def _decode_base64url(value, pattern, expected_length):
    if pattern.match(value) is None:
        raise ProtocolError("unauthorized")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (TypeError, ValueError, binascii.Error):
        raise ProtocolError("unauthorized")
    if len(decoded) != expected_length or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise ProtocolError("unauthorized")
    return decoded


def _canonical_target(target):
    if type(target) is not str or not target.startswith("/") or any(ord(char) < 32 or ord(char) == 127 for char in target):
        raise ProtocolError("invalid_request")
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or parsed.params or parsed.fragment:
        raise ProtocolError("invalid_request")
    if not parsed.query:
        if "?" in target:
            raise ProtocolError("invalid_request")
        return parsed.path
    index = 0
    while index < len(parsed.query):
        if parsed.query[index] == "%":
            if index + 2 >= len(parsed.query) or re.match(r"^[0-9A-F]{2}$", parsed.query[index + 1:index + 3]) is None:
                raise ProtocolError("invalid_request")
            index += 3
        else:
            index += 1
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True,
                          encoding="utf-8", errors="strict")
    except (UnicodeError, ValueError):
        raise ProtocolError("invalid_request")
    encoded = sorted((quote(key, safe="-._~"), quote(value, safe="-._~")) for key, value in pairs)
    query = "&".join("{}={}".format(key, value) for key, value in encoded)
    canonical = parsed.path + "?" + query
    if canonical != target:
        raise ProtocolError("invalid_request")
    return canonical


def _load_clip_body(body):
    duplicate = [False]

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                duplicate[0] = True
            result[key] = value
        return result

    def invalid_constant(unused_value):
        raise ValueError("nonfinite")

    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=object_pairs,
                           parse_constant=invalid_constant)
    except (UnicodeError, ValueError):
        raise ProtocolError("invalid_request")
    if duplicate[0] or type(value) is not dict or set(value) != {
            "committed_at", "event_id", "event_type", "occurred_at"}:
        raise ProtocolError("invalid_request")
    if type(value["event_id"]) is not int or value["event_id"] < 0:
        raise ProtocolError("invalid_request")
    if type(value["event_type"]) is not str or value["event_type"] not in ALLOWED_EVENTS:
        raise ProtocolError("invalid_request")
    for name in ("committed_at", "occurred_at"):
        timestamp = value[name]
        if type(timestamp) is not str or UTC_TIMESTAMP.match(timestamp) is None:
            raise ProtocolError("invalid_request")
        try:
            datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            raise ProtocolError("invalid_request")
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if canonical != body:
        raise ProtocolError("invalid_request")


def verify_request(method, target, headers, body, secret, boot_id, now_unix, replay_guard):
    if type(method) is not str or re.match(r"^[A-Z]+$", method) is None or type(body) is not bytes:
        raise ProtocolError("invalid_request")
    if len(body) > MAX_BODY_BYTES:
        raise ProtocolError("invalid_request")
    canonical_target = _canonical_target(target)
    values = _required_headers(headers)
    if type(secret) is not bytes or len(secret) != 32 or HEX_32.match(boot_id or "") is None:
        raise ProtocolError("unauthorized")
    if values[HEADER_NAMES[0]] != VERSION:
        raise ProtocolError("unauthorized")

    supplied_boot = values[HEADER_NAMES[1]]
    bootstrap = method == "GET" and canonical_target == "/v1/status" and supplied_boot == "bootstrap"
    if supplied_boot == "bootstrap" and not bootstrap:
        raise ProtocolError("wrong_boot")
    if not bootstrap and HEX_32.match(supplied_boot) is None:
        raise ProtocolError("unauthorized")
    if not bootstrap and supplied_boot != boot_id:
        raise ProtocolError("wrong_boot")

    timestamp_text = values[HEADER_NAMES[2]]
    if re.match(r"^(0|[1-9][0-9]{0,9})$", timestamp_text) is None:
        raise ProtocolError("unauthorized")
    timestamp = int(timestamp_text)
    if type(now_unix) not in (int, float) or isinstance(now_unix, bool) or abs(float(now_unix) - timestamp) > 30.0:
        raise ProtocolError("stale_request")

    nonce = values[HEADER_NAMES[3]]
    _decode_base64url(nonce, BASE64URL_22, 16)
    supplied_digest = values[HEADER_NAMES[4]]
    if HEX_64.match(supplied_digest) is None:
        raise ProtocolError("unauthorized")
    actual_digest = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(supplied_digest, actual_digest):
        raise ProtocolError("unauthorized")
    supplied_signature = values[HEADER_NAMES[5]]
    _decode_base64url(supplied_signature, BASE64URL_43, 32)
    canonical = "{}\n{}\n{}\n{}\n{}\n{}\n{}\n".format(
        VERSION, method, canonical_target, supplied_boot, timestamp_text, nonce, actual_digest
    ).encode("utf-8")
    expected_signature = base64.urlsafe_b64encode(
        hmac.new(secret, canonical, hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ProtocolError("unauthorized")

    if method == "PUT" and CLIP_TARGET.match(canonical_target):
        _load_clip_body(body)
    replay_guard.check_and_add(boot_id, nonce, float(now_unix))
    return None
