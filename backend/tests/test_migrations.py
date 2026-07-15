from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from conftest import validate_test_database_url


APP_TABLES = {
    "devices",
    "sensor_readings",
    "cameras",
    "zones",
    "camera_events",
    "behavior_events",
    "anomaly_events",
    "bed_calibrations",
    "rest_sessions",
}
EXPECTED_COLUMNS = {
    "devices": ("device_id", "status", "last_seen_at", "created_at", "updated_at"),
    "sensor_readings": ("id", "device_id", "sensor_type", "value_number", "value_boolean", "unit", "observed_at", "received_at"),
    "cameras": ("camera_id", "status", "last_frame_at", "created_at", "updated_at"),
    "zones": ("zone_name", "x1", "y1", "x2", "y2", "enabled", "created_at", "updated_at"),
    "camera_events": ("id", "camera_id", "subject_id", "detected_type", "confidence", "bbox_x", "bbox_y", "bbox_width", "bbox_height", "center_x", "center_y", "zone_name", "observed_at", "created_at"),
    "behavior_events": ("id", "subject_id", "behavior_type", "source_camera_event_id", "source_sensor_reading_id", "source_key", "started_at", "ended_at", "duration_seconds", "created_at", "updated_at"),
    "anomaly_events": ("id", "subject_id", "anomaly_type", "severity", "mismatch_kind", "source_behavior_event_id", "source_key", "message", "occurred_at", "created_at"),
    "bed_calibrations": ("id", "device_id", "calibrated_at", "window_start", "window_end", "left_sample_count", "left_baseline", "left_polarity", "left_stability_limit", "center_sample_count", "center_baseline", "center_polarity", "center_stability_limit", "right_sample_count", "right_baseline", "right_polarity", "right_stability_limit", "entry_threshold", "exit_threshold", "created_at"),
    "rest_sessions": ("id", "subject_id", "behavior_event_id", "started_at", "last_confirmed_at", "ended_at", "duration_seconds", "close_reason", "created_at", "updated_at"),
}

EXPECTED_COLUMN_SIGNATURES = {
    "anomaly_events": [
        [
            "id",
            "BIGINT",
            False,
            None,
            True
        ],
        [
            "subject_id",
            "VARCHAR(16)",
            True,
            None,
            False
        ],
        [
            "anomaly_type",
            "VARCHAR(32)",
            False,
            None,
            False
        ],
        [
            "severity",
            "VARCHAR(8)",
            False,
            "'warning'::character varying",
            False
        ],
        [
            "mismatch_kind",
            "VARCHAR(32)",
            True,
            None,
            False
        ],
        [
            "source_behavior_event_id",
            "BIGINT",
            True,
            None,
            False
        ],
        [
            "source_key",
            "VARCHAR(160)",
            False,
            None,
            False
        ],
        [
            "message",
            "TEXT",
            False,
            None,
            False
        ],
        [
            "occurred_at",
            "TIMESTAMP_TZ=True",
            False,
            None,
            False
        ],
        [
            "created_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ]
    ],
    "bed_calibrations": [
        [
            "id",
            "BIGINT",
            False,
            None,
            True
        ],
        [
            "device_id",
            "VARCHAR(32)",
            False,
            None,
            False
        ],
        [
            "calibrated_at",
            "TIMESTAMP_TZ=True",
            False,
            None,
            False
        ],
        [
            "window_start",
            "TIMESTAMP_TZ=True",
            False,
            None,
            False
        ],
        [
            "window_end",
            "TIMESTAMP_TZ=True",
            False,
            None,
            False
        ],
        [
            "left_sample_count",
            "SMALLINT",
            False,
            None,
            False
        ],
        [
            "left_baseline",
            "DOUBLE PRECISION",
            False,
            None,
            False
        ],
        [
            "left_polarity",
            "SMALLINT",
            False,
            None,
            False
        ],
        [
            "left_stability_limit",
            "SMALLINT",
            False,
            None,
            False
        ],
        [
            "center_sample_count",
            "SMALLINT",
            False,
            None,
            False
        ],
        [
            "center_baseline",
            "DOUBLE PRECISION",
            False,
            None,
            False
        ],
        [
            "center_polarity",
            "SMALLINT",
            False,
            None,
            False
        ],
        [
            "center_stability_limit",
            "SMALLINT",
            False,
            None,
            False
        ],
        [
            "right_sample_count",
            "SMALLINT",
            False,
            None,
            False
        ],
        [
            "right_baseline",
            "DOUBLE PRECISION",
            False,
            None,
            False
        ],
        [
            "right_polarity",
            "SMALLINT",
            False,
            None,
            False
        ],
        [
            "right_stability_limit",
            "SMALLINT",
            False,
            None,
            False
        ],
        [
            "entry_threshold",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "exit_threshold",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "created_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ]
    ],
    "behavior_events": [
        [
            "id",
            "BIGINT",
            False,
            None,
            True
        ],
        [
            "subject_id",
            "VARCHAR(16)",
            False,
            None,
            False
        ],
        [
            "behavior_type",
            "VARCHAR(16)",
            False,
            None,
            False
        ],
        [
            "source_camera_event_id",
            "BIGINT",
            False,
            None,
            False
        ],
        [
            "source_sensor_reading_id",
            "BIGINT",
            False,
            None,
            False
        ],
        [
            "source_key",
            "VARCHAR(160)",
            False,
            None,
            False
        ],
        [
            "started_at",
            "TIMESTAMP_TZ=True",
            False,
            None,
            False
        ],
        [
            "ended_at",
            "TIMESTAMP_TZ=True",
            True,
            None,
            False
        ],
        [
            "duration_seconds",
            "INTEGER",
            True,
            None,
            False
        ],
        [
            "created_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ],
        [
            "updated_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ]
    ],
    "camera_events": [
        [
            "id",
            "BIGINT",
            False,
            None,
            True
        ],
        [
            "camera_id",
            "VARCHAR(32)",
            False,
            None,
            False
        ],
        [
            "subject_id",
            "VARCHAR(16)",
            True,
            None,
            False
        ],
        [
            "detected_type",
            "VARCHAR(16)",
            False,
            None,
            False
        ],
        [
            "confidence",
            "DOUBLE PRECISION",
            False,
            None,
            False
        ],
        [
            "bbox_x",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "bbox_y",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "bbox_width",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "bbox_height",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "center_x",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "center_y",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "zone_name",
            "VARCHAR(32)",
            True,
            None,
            False
        ],
        [
            "observed_at",
            "TIMESTAMP_TZ=True",
            False,
            None,
            False
        ],
        [
            "created_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ]
    ],
    "cameras": [
        [
            "camera_id",
            "VARCHAR(32)",
            False,
            None,
            False
        ],
        [
            "status",
            "VARCHAR(8)",
            False,
            "'offline'::character varying",
            False
        ],
        [
            "last_frame_at",
            "TIMESTAMP_TZ=True",
            True,
            None,
            False
        ],
        [
            "created_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ],
        [
            "updated_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ]
    ],
    "devices": [
        [
            "device_id",
            "VARCHAR(32)",
            False,
            None,
            False
        ],
        [
            "status",
            "VARCHAR(8)",
            False,
            "'unknown'::character varying",
            False
        ],
        [
            "last_seen_at",
            "TIMESTAMP_TZ=True",
            True,
            None,
            False
        ],
        [
            "created_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ],
        [
            "updated_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ]
    ],
    "rest_sessions": [
        [
            "id",
            "BIGINT",
            False,
            None,
            True
        ],
        [
            "subject_id",
            "VARCHAR(16)",
            False,
            None,
            False
        ],
        [
            "behavior_event_id",
            "BIGINT",
            False,
            None,
            False
        ],
        [
            "started_at",
            "TIMESTAMP_TZ=True",
            False,
            None,
            False
        ],
        [
            "last_confirmed_at",
            "TIMESTAMP_TZ=True",
            False,
            None,
            False
        ],
        [
            "ended_at",
            "TIMESTAMP_TZ=True",
            True,
            None,
            False
        ],
        [
            "duration_seconds",
            "INTEGER",
            True,
            None,
            False
        ],
        [
            "close_reason",
            "VARCHAR(24)",
            True,
            None,
            False
        ],
        [
            "created_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ],
        [
            "updated_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ]
    ],
    "sensor_readings": [
        [
            "id",
            "BIGINT",
            False,
            None,
            True
        ],
        [
            "device_id",
            "VARCHAR(32)",
            False,
            None,
            False
        ],
        [
            "sensor_type",
            "VARCHAR(32)",
            False,
            None,
            False
        ],
        [
            "value_number",
            "DOUBLE PRECISION",
            True,
            None,
            False
        ],
        [
            "value_boolean",
            "BOOLEAN",
            True,
            None,
            False
        ],
        [
            "unit",
            "VARCHAR(8)",
            False,
            None,
            False
        ],
        [
            "observed_at",
            "TIMESTAMP_TZ=True",
            False,
            None,
            False
        ],
        [
            "received_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ]
    ],
    "zones": [
        [
            "zone_name",
            "VARCHAR(32)",
            False,
            None,
            False
        ],
        [
            "x1",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "y1",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "x2",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "y2",
            "INTEGER",
            False,
            None,
            False
        ],
        [
            "enabled",
            "BOOLEAN",
            False,
            "true",
            False
        ],
        [
            "created_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ],
        [
            "updated_at",
            "TIMESTAMP_TZ=True",
            False,
            "CURRENT_TIMESTAMP",
            False
        ]
    ]
}

EXPECTED_CHECK_NAMES = {
    "devices": {"ck_devices_device_id", "ck_devices_status"},
    "sensor_readings": {"ck_sensor_readings_one_value", "ck_sensor_readings_finite_number", "ck_sensor_readings_type_unit_value", "ck_sensor_readings_device_profile", "ck_sensor_readings_pressure_range"},
    "cameras": {"ck_cameras_camera_id", "ck_cameras_status"},
    "zones": {"ck_zones_zone_name", "ck_zones_x_bounds", "ck_zones_y_bounds"},
    "camera_events": {"ck_camera_events_detected_type", "ck_camera_events_subject_type", "ck_camera_events_confidence", "ck_camera_events_bbox_x", "ck_camera_events_bbox_y", "ck_camera_events_center"},
    "behavior_events": {"ck_behavior_events_subject_id", "ck_behavior_events_behavior_type", "ck_behavior_events_open_closed"},
    "anomaly_events": {"ck_anomaly_events_anomaly_type", "ck_anomaly_events_severity", "ck_anomaly_events_relation", "ck_anomaly_events_message"},
    "bed_calibrations": {"ck_bed_calibrations_device_id", "ck_bed_calibrations_window", "ck_bed_calibrations_sample_counts", "ck_bed_calibrations_baselines", "ck_bed_calibrations_polarities", "ck_bed_calibrations_stability_limits", "ck_bed_calibrations_thresholds"},
    "rest_sessions": {"ck_rest_sessions_subject_id", "ck_rest_sessions_last_confirmed", "ck_rest_sessions_open_closed"},
}
EXPECTED_CHECK_DEFINITIONS = {
    ("anomaly_events", "ck_anomaly_events_anomaly_type", "CHECK (anomaly_type::text = ANY (ARRAY['no_meal_12h'::character varying, 'bed_sensor_mismatch'::character varying]::text[]))"),
    ("anomaly_events", "ck_anomaly_events_message", "CHECK (length(message) > 0)"),
    ("anomaly_events", "ck_anomaly_events_relation", "CHECK ((anomaly_type::text = 'no_meal_12h'::text AND (subject_id::text = ANY (ARRAY['dog_001'::character varying, 'cat_001'::character varying]::text[])) AND mismatch_kind IS NULL AND source_behavior_event_id IS NOT NULL OR anomaly_type::text = 'bed_sensor_mismatch'::text AND mismatch_kind::text = 'sensor_check'::text AND (subject_id::text = ANY (ARRAY['dog_001'::character varying, 'cat_001'::character varying]::text[])) AND source_behavior_event_id IS NULL OR anomaly_type::text = 'bed_sensor_mismatch'::text AND mismatch_kind::text = 'unconfirmed_pressure'::text AND subject_id IS NULL AND source_behavior_event_id IS NULL) IS TRUE)"),
    ("anomaly_events", "ck_anomaly_events_severity", "CHECK (severity::text = 'warning'::text)"),
    ("bed_calibrations", "ck_bed_calibrations_baselines", "CHECK (left_baseline >= 0::double precision AND left_baseline <= 4095::double precision AND left_baseline > '-Infinity'::double precision AND left_baseline < 'Infinity'::double precision AND center_baseline >= 0::double precision AND center_baseline <= 4095::double precision AND center_baseline > '-Infinity'::double precision AND center_baseline < 'Infinity'::double precision AND right_baseline >= 0::double precision AND right_baseline <= 4095::double precision AND right_baseline > '-Infinity'::double precision AND right_baseline < 'Infinity'::double precision)"),
    ("bed_calibrations", "ck_bed_calibrations_device_id", "CHECK (device_id::text = 'petzone-01'::text)"),
    ("bed_calibrations", "ck_bed_calibrations_polarities", "CHECK ((left_polarity = ANY (ARRAY['-1'::integer, 1])) AND (center_polarity = ANY (ARRAY['-1'::integer, 1])) AND (right_polarity = ANY (ARRAY['-1'::integer, 1])))"),
    ("bed_calibrations", "ck_bed_calibrations_sample_counts", "CHECK (left_sample_count >= 45 AND center_sample_count >= 45 AND right_sample_count >= 45)"),
    ("bed_calibrations", "ck_bed_calibrations_stability_limits", "CHECK (left_stability_limit >= 0 AND left_stability_limit <= 4095 AND center_stability_limit >= 0 AND center_stability_limit <= 4095 AND right_stability_limit >= 0 AND right_stability_limit <= 4095)"),
    ("bed_calibrations", "ck_bed_calibrations_thresholds", "CHECK (0 <= exit_threshold AND exit_threshold < entry_threshold AND entry_threshold <= 12285)"),
    ("bed_calibrations", "ck_bed_calibrations_window", "CHECK (window_end = (window_start + '00:01:00'::interval) AND calibrated_at >= window_end)"),
    ("behavior_events", "ck_behavior_events_behavior_type", "CHECK (behavior_type::text = ANY (ARRAY['eating'::character varying, 'resting'::character varying]::text[]))"),
    ("behavior_events", "ck_behavior_events_open_closed", "CHECK (ended_at IS NULL AND duration_seconds IS NULL OR ended_at IS NOT NULL AND duration_seconds IS NOT NULL AND ended_at >= started_at AND duration_seconds >= 0)"),
    ("behavior_events", "ck_behavior_events_subject_id", "CHECK (subject_id::text = ANY (ARRAY['dog_001'::character varying, 'cat_001'::character varying]::text[]))"),
    ("camera_events", "ck_camera_events_bbox_x", "CHECK (0 <= bbox_x AND bbox_x < (bbox_x + bbox_width) AND (bbox_x + bbox_width) <= 640)"),
    ("camera_events", "ck_camera_events_bbox_y", "CHECK (0 <= bbox_y AND bbox_y < (bbox_y + bbox_height) AND (bbox_y + bbox_height) <= 480)"),
    ("camera_events", "ck_camera_events_center", "CHECK (bbox_x <= center_x AND center_x < (bbox_x + bbox_width) AND bbox_y <= center_y AND center_y < (bbox_y + bbox_height))"),
    ("camera_events", "ck_camera_events_confidence", "CHECK (confidence > '-Infinity'::double precision AND confidence < 'Infinity'::double precision AND confidence >= 0::double precision AND confidence <= 1::double precision)"),
    ("camera_events", "ck_camera_events_detected_type", "CHECK (detected_type::text = ANY (ARRAY['person'::character varying, 'dog'::character varying, 'cat'::character varying]::text[]))"),
    ("camera_events", "ck_camera_events_subject_type", "CHECK ((detected_type::text = 'person'::text AND subject_id IS NULL OR detected_type::text = 'dog'::text AND subject_id::text = 'dog_001'::text OR detected_type::text = 'cat'::text AND subject_id::text = 'cat_001'::text) IS TRUE)"),
    ("cameras", "ck_cameras_camera_id", "CHECK (camera_id::text = 'pc-webcam-01'::text)"),
    ("cameras", "ck_cameras_status", "CHECK (status::text = ANY (ARRAY['online'::character varying, 'offline'::character varying]::text[]))"),
    ("devices", "ck_devices_device_id", "CHECK (device_id::text = ANY (ARRAY['entrance-01'::character varying, 'petzone-01'::character varying]::text[]))"),
    ("devices", "ck_devices_status", "CHECK (status::text = ANY (ARRAY['online'::character varying, 'offline'::character varying, 'unknown'::character varying]::text[]))"),
    ("rest_sessions", "ck_rest_sessions_last_confirmed", "CHECK (last_confirmed_at >= started_at)"),
    ("rest_sessions", "ck_rest_sessions_open_closed", "CHECK (ended_at IS NULL AND duration_seconds IS NULL AND close_reason IS NULL OR ended_at IS NOT NULL AND duration_seconds IS NOT NULL AND close_reason IS NOT NULL AND ended_at >= started_at AND duration_seconds >= 0 AND (close_reason::text = ANY (ARRAY['pressure_exit'::character varying, 'camera_exit'::character varying, 'sensor_loss'::character varying, 'camera_loss'::character varying, 'shutdown'::character varying, 'restart'::character varying]::text[])))"),
    ("rest_sessions", "ck_rest_sessions_subject_id", "CHECK (subject_id::text = ANY (ARRAY['dog_001'::character varying, 'cat_001'::character varying]::text[]))"),
    ("sensor_readings", "ck_sensor_readings_device_profile", "CHECK (device_id::text = 'petzone-01'::text OR (sensor_type::text = ANY (ARRAY['temperature'::character varying, 'humidity'::character varying, 'presence_moving'::character varying, 'presence_stationary'::character varying]::text[])))"),
    ("sensor_readings", "ck_sensor_readings_finite_number", "CHECK (value_number IS NULL OR value_number > '-Infinity'::double precision AND value_number < 'Infinity'::double precision)"),
    ("sensor_readings", "ck_sensor_readings_one_value", "CHECK ((value_number IS NULL) <> (value_boolean IS NULL))"),
    ("sensor_readings", "ck_sensor_readings_pressure_range", "CHECK ((sensor_type::text <> ALL (ARRAY['bed_pressure_left'::character varying, 'bed_pressure_center'::character varying, 'bed_pressure_right'::character varying]::text[])) OR value_number >= 0::double precision AND value_number <= 4095::double precision AND value_number = trunc(value_number))"),
    ("sensor_readings", "ck_sensor_readings_type_unit_value", "CHECK (sensor_type::text = 'temperature'::text AND unit::text = 'C'::text AND value_number IS NOT NULL AND value_boolean IS NULL OR sensor_type::text = 'humidity'::text AND unit::text = '%'::text AND value_number IS NOT NULL AND value_boolean IS NULL OR (sensor_type::text = ANY (ARRAY['presence_moving'::character varying, 'presence_stationary'::character varying]::text[])) AND unit::text = 'bool'::text AND value_number IS NULL AND value_boolean IS NOT NULL OR (sensor_type::text = ANY (ARRAY['food_weight'::character varying, 'water_weight'::character varying]::text[])) AND device_id::text = 'petzone-01'::text AND unit::text = 'g'::text AND value_number IS NOT NULL AND value_boolean IS NULL OR (sensor_type::text = ANY (ARRAY['bed_pressure_left'::character varying, 'bed_pressure_center'::character varying, 'bed_pressure_right'::character varying]::text[])) AND device_id::text = 'petzone-01'::text AND unit::text = 'adc'::text AND value_number IS NOT NULL AND value_boolean IS NULL)"),
    ("zones", "ck_zones_x_bounds", "CHECK (0 <= x1 AND x1 < x2 AND x2 <= 640)"),
    ("zones", "ck_zones_y_bounds", "CHECK (0 <= y1 AND y1 < y2 AND y2 <= 480)"),
    ("zones", "ck_zones_zone_name", "CHECK (zone_name::text = ANY (ARRAY['food_bowl'::character varying, 'pet_bed'::character varying]::text[]))"),
}
EXPECTED_UNIQUES = {
    "devices": set(), "cameras": set(), "zones": set(),
    "sensor_readings": {("device_id", "sensor_type", "observed_at")},
    "camera_events": {("camera_id", "detected_type", "observed_at")},
    "behavior_events": {("source_key",)},
    "anomaly_events": {("source_key",)},
    "bed_calibrations": {("device_id", "calibrated_at")},
    "rest_sessions": {("behavior_event_id",)},
}
EXPECTED_PRIMARY_KEYS = {
    "devices": ("device_id",), "cameras": ("camera_id",), "zones": ("zone_name",),
    "sensor_readings": ("id",), "camera_events": ("id",), "behavior_events": ("id",),
    "anomaly_events": ("id",), "bed_calibrations": ("id",), "rest_sessions": ("id",),
}
EXPECTED_FOREIGN_KEYS = {
    ("sensor_readings", ("device_id",), "devices", ("device_id",), "RESTRICT"),
    ("camera_events", ("camera_id",), "cameras", ("camera_id",), "RESTRICT"),
    ("camera_events", ("zone_name",), "zones", ("zone_name",), "SET NULL"),
    ("behavior_events", ("source_camera_event_id",), "camera_events", ("id",), "RESTRICT"),
    ("behavior_events", ("source_sensor_reading_id",), "sensor_readings", ("id",), "RESTRICT"),
    ("anomaly_events", ("source_behavior_event_id",), "behavior_events", ("id",), "RESTRICT"),
    ("bed_calibrations", ("device_id",), "devices", ("device_id",), "RESTRICT"),
    ("rest_sessions", ("behavior_event_id",), "behavior_events", ("id",), "RESTRICT"),
}
EXPECTED_INDEX_DEFINITIONS = {
    "CREATE INDEX ix_sensor_readings_device_type_time ON sensor_readings USING btree (device_id, sensor_type, observed_at DESC, id DESC)",
    "CREATE INDEX ix_sensor_readings_type_time ON sensor_readings USING btree (sensor_type, observed_at DESC, id DESC)",
    "CREATE INDEX ix_camera_events_camera_time ON camera_events USING btree (camera_id, observed_at DESC, id DESC)",
    "CREATE INDEX ix_camera_events_zone_type_time ON camera_events USING btree (zone_name, detected_type, observed_at DESC, id DESC)",
    "CREATE UNIQUE INDEX uq_behavior_events_one_open_per_type ON behavior_events USING btree (behavior_type) WHERE (ended_at IS NULL)",
    "CREATE INDEX ix_behavior_events_subject_type_time ON behavior_events USING btree (subject_id, behavior_type, started_at DESC, id DESC)",
    "CREATE INDEX ix_behavior_events_time ON behavior_events USING btree (started_at DESC, id DESC)",
    "CREATE INDEX ix_anomaly_events_time ON anomaly_events USING btree (occurred_at DESC, id DESC)",
    "CREATE INDEX ix_anomaly_events_subject_type_time ON anomaly_events USING btree (subject_id, anomaly_type, occurred_at DESC, id DESC)",
    "CREATE INDEX ix_bed_calibrations_device_time ON bed_calibrations USING btree (device_id, calibrated_at DESC, id DESC)",
    "CREATE UNIQUE INDEX uq_rest_sessions_one_open ON rest_sessions USING btree ((1)) WHERE (ended_at IS NULL)",
    "CREATE INDEX ix_rest_sessions_subject_time ON rest_sessions USING btree (subject_id, started_at DESC, id DESC)",
    "CREATE INDEX ix_rest_sessions_end_time ON rest_sessions USING btree (ended_at DESC, id DESC)",
}


def alembic_config(database_url: str) -> Config:
    os.environ["DATABASE_URL"] = database_url
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["configure_logger"] = False
    return config


def sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://petcare:secret@127.0.0.1:55432/postgres",
        "postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare",
        "postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare_todo3_test",
        "postgresql+psycopg://petcare:secret@127.0.0.1:5432/petcare_test",
        "sqlite:///petcare_test",
    ],
)
def test_destructive_migration_guard_accepts_only_dedicated_database(url: str) -> None:
    with pytest.raises(ValueError):
        validate_test_database_url(url)
    assert validate_test_database_url(
        "postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare_test"
    ).endswith("/petcare_test")


def test_initial_revision_is_self_contained() -> None:
    source = (Path(__file__).parents[1] / "migrations" / "versions" / "0001_initial.py").read_text(encoding="utf-8")
    assert "app.models" not in source


def test_upgrade_downgrade_upgrade_restores_exact_schema(database_url: str) -> None:
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(database_url)

    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == APP_TABLES | {"alembic_version"}
    foreign_keys = set()
    for table_name, column_names in EXPECTED_COLUMNS.items():
        assert tuple(column["name"] for column in inspector.get_columns(table_name)) == column_names
        column_signature = []
        for column in inspector.get_columns(table_name):
            type_name = str(column["type"])
            if type_name == "TIMESTAMP":
                type_name = f"TIMESTAMP_TZ={getattr(column['type'], 'timezone', None)}"
            column_signature.append([column["name"], type_name, column["nullable"], column["default"], bool(column.get("identity"))])
        assert column_signature == EXPECTED_COLUMN_SIGNATURES[table_name]
        assert tuple(inspector.get_pk_constraint(table_name)["constrained_columns"]) == EXPECTED_PRIMARY_KEYS[table_name]
        assert {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table_name)} == EXPECTED_UNIQUES[table_name]
        assert {item["name"] for item in inspector.get_check_constraints(table_name)} == EXPECTED_CHECK_NAMES[table_name]
        for item in inspector.get_foreign_keys(table_name):
            foreign_keys.add((table_name, tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]), item.get("options", {}).get("ondelete")))
    assert foreign_keys == EXPECTED_FOREIGN_KEYS
    alembic_columns = inspector.get_columns("alembic_version")
    assert [
        [column["name"], str(column["type"]), column["nullable"], column["default"], bool(column.get("identity"))]
        for column in alembic_columns
    ] == [["version_num", "VARCHAR(32)", False, None, False]]
    assert tuple(inspector.get_pk_constraint("alembic_version")["constrained_columns"]) == ("version_num",)
    with engine.connect() as connection:
        zones = connection.execute(text("SELECT zone_name,x1,y1,x2,y2,enabled FROM zones ORDER BY zone_name")).all()
        assert zones == [
            ("food_bowl", 40, 260, 260, 470, True),
            ("pet_bed", 320, 180, 630, 470, True),
        ]
        functions = connection.execute(text("SELECT proname FROM pg_proc JOIN pg_namespace n ON n.oid=pronamespace WHERE n.nspname=current_schema() AND prokind='f' ORDER BY proname")).scalars().all()
        assert functions == ["ck_anomaly_source_behavior", "ck_rest_session_behavior"]
        triggers = connection.execute(
            text("SELECT tgname,relname,pg_get_triggerdef(pg_trigger.oid) FROM pg_trigger JOIN pg_class ON pg_class.oid=tgrelid JOIN pg_namespace n ON n.oid=pg_class.relnamespace WHERE n.nspname=current_schema() AND NOT tgisinternal ORDER BY tgname,relname")
        ).all()
        assert [(row[0], row[1]) for row in triggers] == [
            ("ck_anomaly_source_behavior", "anomaly_events"),
            ("ck_anomaly_source_behavior", "behavior_events"),
            ("ck_rest_session_behavior", "behavior_events"),
            ("ck_rest_session_behavior", "rest_sessions"),
        ]
        assert all("INSERT OR DELETE OR UPDATE" in row[2] and "DEFERRABLE INITIALLY DEFERRED" in row[2] for row in triggers)
        assert all(row[0] and row[1] for row in connection.execute(text("SELECT tgdeferrable,tginitdeferred FROM pg_trigger WHERE NOT tgisinternal")).all())
        check_definitions = {
            tuple(row)
            for row in connection.execute(
                text("SELECT rel.relname,con.conname,pg_get_constraintdef(con.oid,true) FROM pg_constraint con JOIN pg_class rel ON rel.oid=con.conrelid JOIN pg_namespace n ON n.oid=rel.relnamespace WHERE n.nspname=current_schema() AND con.contype='c' ORDER BY rel.relname,con.conname")
            ).all()
            if row[0] in APP_TABLES
        }
        assert check_definitions == EXPECTED_CHECK_DEFINITIONS
        index_definitions = {
            definition.replace("public.", "")
            for table_name, definition in connection.execute(
                text("SELECT tbl.relname,pg_get_indexdef(idx.indexrelid) FROM pg_index idx JOIN pg_class tbl ON tbl.oid=idx.indrelid JOIN pg_namespace n ON n.oid=tbl.relnamespace LEFT JOIN pg_constraint con ON con.conindid=idx.indexrelid WHERE n.nspname=current_schema() AND con.oid IS NULL")
            ).all()
            if table_name in APP_TABLES
        }
        assert index_definitions == EXPECTED_INDEX_DEFINITIONS

    command.downgrade(config, "base")
    assert not (set(inspect(engine).get_table_names()) & APP_TABLES)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM pg_proc WHERE proname IN ('ck_anomaly_source_behavior','ck_rest_session_behavior')")).scalar_one() == 0
    command.upgrade(config, "head")
    assert set(inspect(engine).get_table_names()) == APP_TABLES | {"alembic_version"}
    engine.dispose()


def reset_data(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "TRUNCATE anomaly_events,rest_sessions,bed_calibrations,behavior_events,camera_events,sensor_readings,cameras,devices RESTART IDENTITY CASCADE"
        )


def seed_sources(engine) -> tuple[int, int]:
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO devices(device_id) VALUES ('petzone-01')")
        connection.exec_driver_sql("INSERT INTO cameras(camera_id) VALUES ('pc-webcam-01')")
        sensor_id = connection.exec_driver_sql(
            "INSERT INTO sensor_readings(device_id,sensor_type,value_number,unit,observed_at) VALUES ('petzone-01','bed_pressure_left',100,'adc','2026-07-15T00:00:00Z') RETURNING id"
        ).scalar_one()
        camera_id = connection.exec_driver_sql(
            "INSERT INTO camera_events(camera_id,subject_id,detected_type,confidence,bbox_x,bbox_y,bbox_width,bbox_height,center_x,center_y,zone_name,observed_at) VALUES ('pc-webcam-01','dog_001','dog',0.9,320,180,100,100,370,230,'pet_bed','2026-07-15T00:00:00Z') RETURNING id"
        ).scalar_one()
    return sensor_id, camera_id


def insert_behavior(connection, sensor_id: int, camera_id: int, behavior_type: str = "eating", subject: str = "dog_001") -> int:
    return connection.exec_driver_sql(
        "INSERT INTO behavior_events(subject_id,behavior_type,source_camera_event_id,source_sensor_reading_id,source_key,started_at) VALUES (%s,%s,%s,%s,%s,'2026-07-15T00:00:00Z') RETURNING id",
        (subject, behavior_type, camera_id, sensor_id, f"{behavior_type}:{subject}:{camera_id}:{sensor_id}"),
    ).scalar_one()


def insert_rest(connection, behavior_id: int, subject: str = "dog_001") -> int:
    return connection.exec_driver_sql(
        "INSERT INTO rest_sessions(subject_id,behavior_event_id,started_at,last_confirmed_at) VALUES (%s,%s,'2026-07-15T00:00:00Z','2026-07-15T00:00:00Z') RETURNING id",
        (subject, behavior_id),
    ).scalar_one()


def assert_deferred_23514(engine, statement: str, parameters: tuple[object, ...] = ()) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.exec_driver_sql(statement, parameters)
        with pytest.raises(IntegrityError) as caught:
            transaction.commit()
        assert sqlstate(caught.value) == "23514"


def test_row_checks_global_open_and_deferred_relationship_matrix(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")
    engine = create_engine(database_url)
    reset_data(engine)
    sensor_id, camera_id = seed_sources(engine)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO sensor_readings(device_id,sensor_type,value_number,unit,observed_at) VALUES ('petzone-01','humidity',50.0,%s,'2026-07-15T00:00:01Z')",
            ("%",),
        )

    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO sensor_readings(device_id,sensor_type,value_number,unit,observed_at) VALUES ('petzone-01','bed_pressure_left',4096,'adc','2026-07-15T00:00:01Z')"
            )
        assert sqlstate(caught.value) == "23514"

    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO camera_events(camera_id,subject_id,detected_type,confidence,bbox_x,bbox_y,bbox_width,bbox_height,center_x,center_y,zone_name,observed_at) VALUES ('pc-webcam-01','dog_001','dog',0.9,639,0,2,1,639,0,'pet_bed','2026-07-15T00:00:01Z')"
            )
        assert sqlstate(caught.value) == "23514"

    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO camera_events(camera_id,subject_id,detected_type,confidence,bbox_x,bbox_y,bbox_width,bbox_height,center_x,center_y,zone_name,observed_at) VALUES ('pc-webcam-01',NULL,'dog',0.9,320,180,100,100,370,230,'pet_bed','2026-07-15T00:00:02Z')"
            )
        assert sqlstate(caught.value) == "23514"

    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO anomaly_events(subject_id,anomaly_type,mismatch_kind,source_behavior_event_id,source_key,message,occurred_at) VALUES ('dog_001','no_meal_12h',NULL,NULL,'bad-row','late meal','2026-07-15T12:00:00Z')"
            )
        assert sqlstate(caught.value) == "23514"

    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO anomaly_events(subject_id,anomaly_type,mismatch_kind,source_behavior_event_id,source_key,message,occurred_at) VALUES (NULL,'bed_sensor_mismatch',NULL,NULL,'null-mismatch','sensor mismatch','2026-07-15T12:00:01Z')"
            )
        assert sqlstate(caught.value) == "23514"

    with engine.begin() as connection:
        nullable_source_id = insert_behavior(connection, sensor_id, camera_id, "eating")
    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO anomaly_events(subject_id,anomaly_type,mismatch_kind,source_behavior_event_id,source_key,message,occurred_at) VALUES (NULL,'no_meal_12h',NULL,%s,'null-subject','late meal','2026-07-15T12:00:02Z')",
                (nullable_source_id,),
            )
        assert sqlstate(caught.value) == "23514"

    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO rest_sessions(subject_id,behavior_event_id,started_at,last_confirmed_at,ended_at,duration_seconds,close_reason) VALUES ('dog_001',999,'2026-07-15T00:00:00Z','2026-07-15T00:00:00Z','2026-07-15T00:01:00Z',60,'invalid')"
            )
        assert sqlstate(caught.value) == "23514"

    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO behavior_events(subject_id,behavior_type,source_camera_event_id,source_sensor_reading_id,source_key,started_at,ended_at,duration_seconds) VALUES ('dog_001','eating',%s,%s,'partial-close','2026-07-15T00:00:00Z','2026-07-15T00:01:00Z',NULL)",
                (camera_id, sensor_id),
            )
        assert sqlstate(caught.value) == "23514"

    with engine.connect() as connection:
        transaction = connection.begin()
        resting_id = insert_behavior(connection, sensor_id, camera_id, "resting")
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO rest_sessions(subject_id,behavior_event_id,started_at,last_confirmed_at,ended_at,duration_seconds,close_reason) VALUES ('dog_001',%s,'2026-07-15T00:00:00Z','2026-07-15T00:00:00Z',NULL,NULL,'pressure_exit')",
                (resting_id,),
            )
        assert sqlstate(caught.value) == "23514"
        transaction.rollback()

    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO anomaly_events(subject_id,anomaly_type,mismatch_kind,source_behavior_event_id,source_key,message,occurred_at) VALUES ('dog_001','no_meal_12h',NULL,999,'missing','late meal','2026-07-15T12:00:00Z')"
            )
        assert sqlstate(caught.value) == "23503"

    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO rest_sessions(subject_id,behavior_event_id,started_at,last_confirmed_at) VALUES ('dog_001',999,'2026-07-15T00:00:00Z','2026-07-15T00:00:00Z')"
            )
        assert sqlstate(caught.value) == "23503"

    reset_data(engine)
    sensor_id, camera_id = seed_sources(engine)
    with engine.begin() as connection:
        eating_id = insert_behavior(connection, sensor_id, camera_id)
        connection.exec_driver_sql(
            "INSERT INTO anomaly_events(subject_id,anomaly_type,source_behavior_event_id,source_key,message,occurred_at) VALUES ('dog_001','no_meal_12h',%s,'dog:no-meal','meal interval exceeded','2026-07-15T12:00:00Z')",
            (eating_id,),
        )
    with engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM anomaly_events")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM anomaly_events")).scalar_one() == 0

    with engine.connect() as connection:
        transaction = connection.begin()
        resting_id = insert_behavior(connection, sensor_id, camera_id, "resting")
        insert_rest(connection, resting_id)
        transaction.commit()

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.exec_driver_sql("DELETE FROM rest_sessions WHERE behavior_event_id=%s", (resting_id,))
        with pytest.raises(IntegrityError) as caught:
            transaction.commit()
        assert sqlstate(caught.value) == "23514"

    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql("DELETE FROM behavior_events WHERE id=%s", (resting_id,))
        assert sqlstate(caught.value) == "23503"

    reset_data(engine)
    sensor_id, camera_id = seed_sources(engine)
    with engine.begin() as connection:
        eating_id = insert_behavior(connection, sensor_id, camera_id)
        connection.exec_driver_sql(
            "INSERT INTO anomaly_events(subject_id,anomaly_type,source_behavior_event_id,source_key,message,occurred_at) VALUES ('dog_001','no_meal_12h',%s,'valid-anomaly','late meal','2026-07-15T12:00:00Z')",
            (eating_id,),
        )
    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql("UPDATE anomaly_events SET source_behavior_event_id=999 WHERE source_key='valid-anomaly'")
        assert sqlstate(caught.value) == "23503"

    assert_deferred_23514(engine, "UPDATE anomaly_events SET subject_id='cat_001' WHERE source_key='valid-anomaly'")
    assert_deferred_23514(engine, "UPDATE behavior_events SET subject_id='cat_001' WHERE id=%s", (eating_id,))
    assert_deferred_23514(engine, "UPDATE behavior_events SET behavior_type='resting' WHERE id=%s", (eating_id,))
    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql("DELETE FROM behavior_events WHERE id=%s", (eating_id,))
        assert sqlstate(caught.value) == "23503"

    reset_data(engine)
    sensor_id, camera_id = seed_sources(engine)
    with engine.connect() as connection:
        transaction = connection.begin()
        wrong_id = insert_behavior(connection, sensor_id, camera_id, "eating")
        connection.exec_driver_sql(
            "INSERT INTO rest_sessions(subject_id,behavior_event_id,started_at,last_confirmed_at) VALUES ('dog_001',%s,'2026-07-15T00:00:00Z','2026-07-15T00:00:00Z')",
            (wrong_id,),
        )
        with pytest.raises(IntegrityError) as caught:
            transaction.commit()
        assert sqlstate(caught.value) == "23514"

    reset_data(engine)
    sensor_id, camera_id = seed_sources(engine)
    with engine.begin() as connection:
        resting_id = insert_behavior(connection, sensor_id, camera_id, "resting")
        insert_rest(connection, resting_id)
    with engine.connect() as connection, connection.begin():
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql("UPDATE rest_sessions SET behavior_event_id=999 WHERE behavior_event_id=%s", (resting_id,))
        assert sqlstate(caught.value) == "23503"
    for statement in (
        "UPDATE rest_sessions SET subject_id='cat_001' WHERE behavior_event_id=%s",
        "UPDATE rest_sessions SET started_at='2026-07-14T23:59:59Z' WHERE behavior_event_id=%s",
        "UPDATE rest_sessions SET ended_at='2026-07-15T00:01:00Z',duration_seconds=60,close_reason='shutdown' WHERE behavior_event_id=%s",
        "UPDATE behavior_events SET subject_id='cat_001' WHERE id=%s",
        "UPDATE behavior_events SET started_at='2026-07-14T23:59:59Z' WHERE id=%s",
        "UPDATE behavior_events SET ended_at='2026-07-15T00:01:00Z',duration_seconds=60 WHERE id=%s",
    ):
        assert_deferred_23514(engine, statement, (resting_id,))

    reset_data(engine)
    sensor_id, camera_id = seed_sources(engine)
    with engine.connect() as connection:
        transaction = connection.begin()
        insert_behavior(connection, sensor_id, camera_id, "resting")
        with pytest.raises(IntegrityError) as caught:
            transaction.commit()
        assert sqlstate(caught.value) == "23514"

    reset_data(engine)
    sensor_id, camera_id = seed_sources(engine)
    with engine.connect() as connection:
        transaction = connection.begin()
        resting_id = insert_behavior(connection, sensor_id, camera_id, "resting")
        insert_rest(connection, resting_id)
        connection.exec_driver_sql(
            "INSERT INTO anomaly_events(subject_id,anomaly_type,source_behavior_event_id,source_key,message,occurred_at) VALUES ('dog_001','no_meal_12h',%s,'wrong-type','late meal','2026-07-15T12:00:00Z')",
            (resting_id,),
        )
        with pytest.raises(IntegrityError) as caught:
            transaction.commit()
        assert sqlstate(caught.value) == "23514"

    reset_data(engine)
    sensor_id, camera_id = seed_sources(engine)
    with engine.connect() as connection:
        transaction = connection.begin()
        first = insert_behavior(connection, sensor_id, camera_id, "eating")
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO behavior_events(subject_id,behavior_type,source_camera_event_id,source_sensor_reading_id,source_key,started_at) VALUES ('cat_001','eating',%s,%s,'second-open','2026-07-15T00:00:01Z')",
                (camera_id, sensor_id),
            )
        assert sqlstate(caught.value) == "23505"
        transaction.rollback()

    reset_data(engine)
    sensor_id, camera_id = seed_sources(engine)
    with engine.begin() as connection:
        open_behavior = insert_behavior(connection, sensor_id, camera_id, "resting")
        insert_rest(connection, open_behavior)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE behavior_events SET ended_at='2026-07-15T00:10:00Z',duration_seconds=600 WHERE id=%s",
            (open_behavior,),
        )
        connection.exec_driver_sql(
            "UPDATE rest_sessions SET last_confirmed_at='2026-07-15T00:10:00Z',ended_at='2026-07-15T00:10:00Z',duration_seconds=600,close_reason='camera_exit' WHERE behavior_event_id=%s",
            (open_behavior,),
        )
    with engine.begin() as connection:
        second_behavior = insert_behavior(connection, sensor_id, camera_id, "resting", "cat_001")
        insert_rest(connection, second_behavior, "cat_001")
    with engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "UPDATE rest_sessions SET ended_at=NULL,duration_seconds=NULL,close_reason=NULL WHERE behavior_event_id=%s",
                (open_behavior,),
            )
        assert sqlstate(caught.value) == "23505"
        transaction.rollback()

    reset_data(engine)
    sensor_id, camera_id = seed_sources(engine)
    with engine.begin() as connection:
        eating_id = insert_behavior(connection, sensor_id, camera_id, "eating")
        resting_id = insert_behavior(connection, sensor_id, camera_id, "resting")
        insert_rest(connection, resting_id)
        assert eating_id != resting_id
    with engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(IntegrityError) as caught:
            connection.exec_driver_sql(
                "INSERT INTO behavior_events(subject_id,behavior_type,source_camera_event_id,source_sensor_reading_id,source_key,started_at) VALUES ('cat_001','resting',%s,%s,'second-rest','2026-07-15T00:00:01Z')",
                (camera_id, sensor_id),
            )
        assert sqlstate(caught.value) == "23505"
        transaction.rollback()

    reset_data(engine)
    sensor_id, camera_id = seed_sources(engine)
    with engine.begin() as connection:
        resting_id = insert_behavior(connection, sensor_id, camera_id, "resting")
        insert_rest(connection, resting_id)
        connection.exec_driver_sql(
            "UPDATE behavior_events SET ended_at='2026-07-15T01:00:00Z',duration_seconds=3600 WHERE id=%s",
            (resting_id,),
        )
        connection.exec_driver_sql(
            "UPDATE rest_sessions SET last_confirmed_at='2026-07-15T01:00:00Z',ended_at='2026-07-15T01:00:00Z',duration_seconds=3600,close_reason='pressure_exit' WHERE behavior_event_id=%s",
            (resting_id,),
        )

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO bed_calibrations(device_id,calibrated_at,window_start,window_end,left_sample_count,left_baseline,left_polarity,left_stability_limit,center_sample_count,center_baseline,center_polarity,center_stability_limit,right_sample_count,right_baseline,right_polarity,right_stability_limit,entry_threshold,exit_threshold) VALUES ('petzone-01','2026-07-15T00:01:00Z','2026-07-15T00:00:00Z','2026-07-15T00:01:00Z',45,100,1,40,45,101,1,40,45,102,1,40,450,250)"
        )
    engine.dispose()
