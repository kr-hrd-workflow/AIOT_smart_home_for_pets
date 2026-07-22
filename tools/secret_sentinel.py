"""Detect injected secret sentinels without printing their values."""

import base64
from collections.abc import Sequence
from urllib.parse import quote, quote_plus


def encoded_forms(raw: str) -> frozenset[str]:
    if not isinstance(raw, str) or not raw:
        raise ValueError("sentinel must be a non-empty string")

    payload = raw.encode("utf-8")
    percent = quote(raw, safe="")
    standard = base64.b64encode(payload).decode("ascii")
    urlsafe = base64.urlsafe_b64encode(payload).decode("ascii")
    lower_hex = payload.hex()
    return frozenset(
        {
            raw,
            percent,
            quote_plus(raw, safe=""),
            standard,
            standard.rstrip("="),
            urlsafe,
            urlsafe.rstrip("="),
            lower_hex,
            lower_hex.upper(),
            f"postgresql://petcare:{percent}@127.0.0.1:55432/petcare",
            f"postgresql+psycopg://petcare:{percent}@127.0.0.1:55432/petcare",
            f"mqtt://petcare:{percent}@127.0.0.1:18883",
        }
    )


def require_independent_sentinels(sentinels: Sequence[str]) -> tuple[str, ...]:
    if isinstance(sentinels, (str, bytes)):
        raise ValueError("three independent sentinels are required")
    values = tuple(sentinels)
    if len(values) < 3 or any(not isinstance(value, str) or not value for value in values):
        raise ValueError("three independent sentinels are required")
    if len(set(values)) != len(values):
        raise ValueError("three independent sentinels are required")
    return values


def assert_no_sentinels(data: bytes | str, sentinels: Sequence[str]) -> None:
    values = require_independent_sentinels(sentinels)
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    for index, sentinel in enumerate(values, start=1):
        if any(form.encode("utf-8") in payload for form in encoded_forms(sentinel)):
            raise ValueError(f"sentinel #{index} leak")
