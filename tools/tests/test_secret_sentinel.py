import base64
from urllib.parse import quote, quote_plus

import pytest

from tools.secret_sentinel import (
    assert_no_sentinels,
    encoded_forms,
    require_independent_sentinels,
)


def sentinels() -> tuple[str, str, str]:
    return (
        "db-" + "sentinel-A",
        "mqtt-" + "sentinel-B",
        "sites-" + "sentinel-C",
    )


def test_encoded_forms_cover_every_required_encoding_and_service_url() -> None:
    raw = "secret +/ÿ"
    payload = raw.encode("utf-8")
    standard = base64.b64encode(payload).decode("ascii")
    urlsafe = base64.urlsafe_b64encode(payload).decode("ascii")

    assert {
        raw,
        quote(raw, safe=""),
        quote_plus(raw, safe=""),
        standard,
        standard.rstrip("="),
        urlsafe,
        urlsafe.rstrip("="),
        payload.hex(),
        payload.hex().upper(),
        f"postgresql://petcare:{quote(raw, safe='')}@127.0.0.1:55432/petcare",
        f"postgresql+psycopg://petcare:{quote(raw, safe='')}@127.0.0.1:55432/petcare",
        f"mqtt://petcare:{quote(raw, safe='')}@127.0.0.1:18883",
    } <= encoded_forms(raw)


def test_every_form_of_three_independent_sentinels_is_detected() -> None:
    values = sentinels()
    for sentinel_index, sentinel in enumerate(values, start=1):
        for form in encoded_forms(sentinel):
            with pytest.raises(ValueError, match=rf"sentinel #{sentinel_index}"):
                assert_no_sentinels(form.encode(), values)


def test_failure_message_never_echoes_sensitive_value() -> None:
    values = sentinels()
    with pytest.raises(ValueError) as caught:
        assert_no_sentinels(values[0].encode(), values)
    assert all(value not in str(caught.value) for value in values)


@pytest.mark.parametrize(
    "values",
    [(), ("one",), ("one", "two"), ("one", "two", "one"), ("one", "two", "")],
)
def test_three_distinct_nonempty_sentinels_are_required(values: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="three independent"):
        require_independent_sentinels(values)
