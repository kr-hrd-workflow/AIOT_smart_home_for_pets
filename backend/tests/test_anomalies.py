from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def test_mismatch_attempts_once_at_exact_thirty_second_boundary() -> None:
    rules = importlib.import_module("app.rules")
    started = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)
    state = rules.MismatchState()
    state.observe("unconfirmed_pressure", None, started, 10.0)

    assert state.evaluate(started + timedelta(seconds=29, milliseconds=999), 39.999) is None
    attempt = state.evaluate(started + timedelta(seconds=30), 40.0)
    assert attempt == rules.MismatchAttempt(
        mismatch_kind="unconfirmed_pressure",
        subject_id=None,
        episode_started_at=started,
        occurred_at=started + timedelta(seconds=30),
        source_key=f"petzone-01:bed_sensor_mismatch:unconfirmed_pressure:{rules.utc_key(started)}",
    )
    assert state.evaluate(started + timedelta(minutes=20), 1210.0) is None


def test_local_day_metrics_use_complete_seoul_days_and_half_up_rounding() -> None:
    rules = importlib.import_module("app.rules")
    seoul = ZoneInfo("Asia/Seoul")
    now = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    local_midnight = datetime(2026, 7, 20, tzinfo=seoul)
    history_start = (local_midnight - timedelta(days=7)).astimezone(UTC)
    sessions = [
        rules.MetricSession(history_start, history_start + timedelta(seconds=100), "pressure_exit"),
        rules.MetricSession(now - timedelta(seconds=60), None, None),
        rules.MetricSession(
            datetime(2026, 7, 19, 16, 0, tzinfo=UTC),
            datetime(2026, 7, 19, 16, 0, tzinfo=UTC),
            "pressure_exit",
        ),
    ]

    metrics = rules.local_day_metrics(sessions, earliest_pressure_at=history_start, now=now, timezone=seoul)

    assert metrics.today_seconds == 60
    assert metrics.nighttime_exit_count == 1
    assert metrics.seven_day.status == "ready"
    assert (metrics.seven_day.baseline_seconds, metrics.seven_day.difference_seconds) == (14, 46)
    assert metrics.seven_day.percent_change == 328.6
