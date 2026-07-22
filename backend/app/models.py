from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    literal_column,
    text,
)
from sqlalchemy.orm import DeclarativeBase


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        CheckConstraint("device_id IN ('entrance-01','petzone-01')", name="device_id"),
        CheckConstraint("status IN ('online','offline','unknown')", name="status"),
    )

    device_id = Column(String(32), primary_key=True, nullable=False)
    status = Column(String(8), nullable=False, server_default=text("'unknown'"))
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    __table_args__ = (
        CheckConstraint("(value_number IS NULL) <> (value_boolean IS NULL)", name="one_value"),
        CheckConstraint("value_number IS NULL OR (value_number > '-Infinity'::DOUBLE PRECISION AND value_number < 'Infinity'::DOUBLE PRECISION)", name="finite_number"),
        CheckConstraint(
            "(sensor_type='temperature' AND unit='C' AND value_number IS NOT NULL AND value_boolean IS NULL) OR "
            "(sensor_type='humidity' AND unit='%' AND value_number IS NOT NULL AND value_boolean IS NULL) OR "
            "(sensor_type IN ('presence_moving','presence_stationary') AND unit='bool' AND value_number IS NULL AND value_boolean IS NOT NULL) OR "
            "(sensor_type IN ('food_weight','water_weight') AND device_id='petzone-01' AND unit='g' AND value_number IS NOT NULL AND value_boolean IS NULL) OR "
            "(sensor_type IN ('bed_pressure_left','bed_pressure_center','bed_pressure_right') AND device_id='petzone-01' AND unit='adc' AND value_number IS NOT NULL AND value_boolean IS NULL)",
            name="type_unit_value",
        ),
        CheckConstraint("device_id='petzone-01' OR sensor_type IN ('temperature','humidity','presence_moving','presence_stationary')", name="device_profile"),
        CheckConstraint("sensor_type NOT IN ('bed_pressure_left','bed_pressure_center','bed_pressure_right') OR (value_number BETWEEN 0 AND 4095 AND value_number=trunc(value_number))", name="pressure_range"),
        UniqueConstraint("device_id", "sensor_type", "observed_at", name="uq_sensor_readings_device_type_observed"),
        Index("ix_sensor_readings_device_type_time", "device_id", "sensor_type", text("observed_at DESC"), text("id DESC")),
        Index("ix_sensor_readings_type_time", "sensor_type", text("observed_at DESC"), text("id DESC")),
    )

    id = Column(BigInteger, Identity(), primary_key=True)
    device_id = Column(String(32), ForeignKey("devices.device_id", ondelete="RESTRICT"), nullable=False)
    sensor_type = Column(String(32), nullable=False)
    value_number = Column(Double, nullable=True)
    value_boolean = Column(Boolean, nullable=True)
    unit = Column(String(8), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class Camera(Base):
    __tablename__ = "cameras"
    __table_args__ = (
        CheckConstraint("camera_id='pc-webcam-01'", name="camera_id"),
        CheckConstraint("status IN ('online','offline')", name="status"),
    )

    camera_id = Column(String(32), primary_key=True, nullable=False)
    status = Column(String(8), nullable=False, server_default=text("'offline'"))
    last_frame_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (
        CheckConstraint("zone_name IN ('food_bowl','pet_bed')", name="zone_name"),
        CheckConstraint("0<=x1 AND x1<x2 AND x2<=640", name="x_bounds"),
        CheckConstraint("0<=y1 AND y1<y2 AND y2<=480", name="y_bounds"),
    )

    zone_name = Column(String(32), primary_key=True, nullable=False)
    x1 = Column(Integer, nullable=False)
    y1 = Column(Integer, nullable=False)
    x2 = Column(Integer, nullable=False)
    y2 = Column(Integer, nullable=False)
    enabled = Column(Boolean, nullable=False, server_default=text("TRUE"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class CameraEvent(Base):
    __tablename__ = "camera_events"
    __table_args__ = (
        CheckConstraint("detected_type IN ('person','dog','cat')", name="detected_type"),
        CheckConstraint("((detected_type='person' AND subject_id IS NULL) OR (detected_type='dog' AND subject_id='dog_001') OR (detected_type='cat' AND subject_id='cat_001')) IS TRUE", name="subject_type"),
        CheckConstraint("confidence > '-Infinity'::DOUBLE PRECISION AND confidence < 'Infinity'::DOUBLE PRECISION AND confidence BETWEEN 0 AND 1", name="confidence"),
        CheckConstraint("0<=bbox_x AND bbox_x<bbox_x+bbox_width AND bbox_x+bbox_width<=640", name="bbox_x"),
        CheckConstraint("0<=bbox_y AND bbox_y<bbox_y+bbox_height AND bbox_y+bbox_height<=480", name="bbox_y"),
        CheckConstraint("bbox_x<=center_x AND center_x<bbox_x+bbox_width AND bbox_y<=center_y AND center_y<bbox_y+bbox_height", name="center"),
        UniqueConstraint("camera_id", "detected_type", "observed_at", name="uq_camera_events_camera_type_observed"),
        Index("ix_camera_events_camera_time", "camera_id", text("observed_at DESC"), text("id DESC")),
        Index("ix_camera_events_zone_type_time", "zone_name", "detected_type", text("observed_at DESC"), text("id DESC")),
    )

    id = Column(BigInteger, Identity(), primary_key=True)
    camera_id = Column(String(32), ForeignKey("cameras.camera_id", ondelete="RESTRICT"), nullable=False)
    subject_id = Column(String(16), nullable=True)
    detected_type = Column(String(16), nullable=False)
    confidence = Column(Double, nullable=False)
    bbox_x = Column(Integer, nullable=False)
    bbox_y = Column(Integer, nullable=False)
    bbox_width = Column(Integer, nullable=False)
    bbox_height = Column(Integer, nullable=False)
    center_x = Column(Integer, nullable=False)
    center_y = Column(Integer, nullable=False)
    zone_name = Column(String(32), ForeignKey("zones.zone_name", ondelete="SET NULL"), nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class BehaviorEvent(Base):
    __tablename__ = "behavior_events"
    __table_args__ = (
        CheckConstraint("subject_id IN ('dog_001','cat_001')", name="subject_id"),
        CheckConstraint("behavior_type IN ('eating','resting')", name="behavior_type"),
        CheckConstraint("(ended_at IS NULL AND duration_seconds IS NULL) OR (ended_at IS NOT NULL AND duration_seconds IS NOT NULL AND ended_at>=started_at AND duration_seconds>=0)", name="open_closed"),
        UniqueConstraint("source_key", name="uq_behavior_events_source_key"),
        Index("uq_behavior_events_one_open_per_type", "behavior_type", unique=True, postgresql_where=text("ended_at IS NULL")),
        Index("ix_behavior_events_subject_type_time", "subject_id", "behavior_type", text("started_at DESC"), text("id DESC")),
        Index("ix_behavior_events_time", text("started_at DESC"), text("id DESC")),
    )

    id = Column(BigInteger, Identity(), primary_key=True)
    subject_id = Column(String(16), nullable=False)
    behavior_type = Column(String(16), nullable=False)
    source_camera_event_id = Column(BigInteger, ForeignKey("camera_events.id", ondelete="RESTRICT"), nullable=False)
    source_sensor_reading_id = Column(BigInteger, ForeignKey("sensor_readings.id", ondelete="RESTRICT"), nullable=False)
    source_key = Column(String(160), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class AnomalyEvent(Base):
    __tablename__ = "anomaly_events"
    __table_args__ = (
        CheckConstraint("anomaly_type IN ('no_meal_12h','bed_sensor_mismatch')", name="anomaly_type"),
        CheckConstraint("severity='warning'", name="severity"),
        CheckConstraint(
            "((anomaly_type='no_meal_12h' AND subject_id IN ('dog_001','cat_001') AND mismatch_kind IS NULL AND source_behavior_event_id IS NOT NULL) OR "
            "(anomaly_type='bed_sensor_mismatch' AND mismatch_kind='sensor_check' AND subject_id IN ('dog_001','cat_001') AND source_behavior_event_id IS NULL) OR "
            "(anomaly_type='bed_sensor_mismatch' AND mismatch_kind='unconfirmed_pressure' AND subject_id IS NULL AND source_behavior_event_id IS NULL)) IS TRUE",
            name="relation",
        ),
        CheckConstraint("length(message)>0", name="message"),
        UniqueConstraint("source_key", name="uq_anomaly_events_source_key"),
        Index("ix_anomaly_events_time", text("occurred_at DESC"), text("id DESC")),
        Index("ix_anomaly_events_subject_type_time", "subject_id", "anomaly_type", text("occurred_at DESC"), text("id DESC")),
    )

    id = Column(BigInteger, Identity(), primary_key=True)
    subject_id = Column(String(16), nullable=True)
    anomaly_type = Column(String(32), nullable=False)
    severity = Column(String(8), nullable=False, server_default=text("'warning'"))
    mismatch_kind = Column(String(32), nullable=True)
    source_behavior_event_id = Column(BigInteger, ForeignKey("behavior_events.id", ondelete="RESTRICT"), nullable=True)
    source_key = Column(String(160), nullable=False)
    message = Column(Text, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ClipTriggerOutbox(Base):
    __tablename__ = "clip_trigger_outbox"
    __table_args__ = (
        CheckConstraint("event_type IN ('eating','resting','bed_sensor_mismatch')", name="event_type"),
        CheckConstraint("event_id > 0", name="event_id"),
        CheckConstraint("deadline_at=created_at+INTERVAL '3 seconds'", name="deadline"),
        CheckConstraint("attempts >= 0", name="attempts"),
        CheckConstraint("remote_boot_id IS NULL OR remote_boot_id ~ '^[0-9a-f]{32}$'", name="remote_boot_id"),
        CheckConstraint("remote_command_id IS NULL OR remote_command_id ~ '^[0-9a-f]{32}$'", name="remote_command_id"),
        CheckConstraint("(remote_boot_id IS NULL)=(accepted_at IS NULL) AND (accepted_at IS NULL OR remote_command_id IS NOT NULL)", name="accepted_identity"),
        CheckConstraint("accepted_at IS NULL OR (accepted_at>=created_at-INTERVAL '200 milliseconds' AND accepted_at<=deadline_at)", name="accepted_window"),
        CheckConstraint("accepted_at IS NULL OR put_started_at IS NOT NULL", name="accepted_put"),
        CheckConstraint("last_error IS NULL OR last_error ~ '^[a-z0-9_]{1,64}$'", name="last_error"),
        CheckConstraint("put_started_at IS NULL OR remote_command_id IS NOT NULL", name="put_command"),
        CheckConstraint("put_started_at IS NULL OR (put_started_at>=created_at AND put_started_at<deadline_at)", name="put_window"),
        CheckConstraint("terminal_reason IS NULL OR terminal_reason='clip_missed'", name="terminal_reason"),
        CheckConstraint("accepted_at IS NULL OR terminal_reason IS NULL OR last_error='clip_gone'", name="accepted_terminal"),
        CheckConstraint("terminal_reason IS NULL OR processed_at IS NOT NULL", name="terminal_processed"),
        CheckConstraint("processed_at IS NULL OR accepted_at IS NOT NULL OR terminal_reason IS NOT NULL", name="processed_state"),
        UniqueConstraint("event_type", "event_id", name="uq_clip_trigger_outbox_event_type_event_id"),
        Index(
            "ix_clip_trigger_outbox_due",
            "next_attempt_at",
            "id",
            postgresql_where=text("processed_at IS NULL"),
        ),
        Index(
            "uq_clip_trigger_outbox_remote_command_id",
            "remote_command_id",
            unique=True,
            postgresql_where=text("remote_command_id IS NOT NULL"),
        ),
    )

    id = Column(BigInteger, Identity(), primary_key=True)
    event_type = Column(String(32), nullable=False)
    event_id = Column(BigInteger, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    deadline_at = Column(DateTime(timezone=True), nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(String(64), nullable=True)
    remote_boot_id = Column(String(32), nullable=True)
    remote_command_id = Column(String(32), nullable=True)
    put_started_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    terminal_reason = Column(String(32), nullable=True)


class BedCalibration(Base):
    __tablename__ = "bed_calibrations"
    __table_args__ = (
        CheckConstraint("device_id='petzone-01'", name="device_id"),
        CheckConstraint("window_end=window_start+INTERVAL '60 seconds' AND calibrated_at>=window_end", name="window"),
        CheckConstraint("left_sample_count>=45 AND center_sample_count>=45 AND right_sample_count>=45", name="sample_counts"),
        CheckConstraint("left_baseline BETWEEN 0 AND 4095 AND left_baseline > '-Infinity'::DOUBLE PRECISION AND left_baseline < 'Infinity'::DOUBLE PRECISION AND center_baseline BETWEEN 0 AND 4095 AND center_baseline > '-Infinity'::DOUBLE PRECISION AND center_baseline < 'Infinity'::DOUBLE PRECISION AND right_baseline BETWEEN 0 AND 4095 AND right_baseline > '-Infinity'::DOUBLE PRECISION AND right_baseline < 'Infinity'::DOUBLE PRECISION", name="baselines"),
        CheckConstraint("left_polarity IN (-1,1) AND center_polarity IN (-1,1) AND right_polarity IN (-1,1)", name="polarities"),
        CheckConstraint("left_stability_limit BETWEEN 0 AND 4095 AND center_stability_limit BETWEEN 0 AND 4095 AND right_stability_limit BETWEEN 0 AND 4095", name="stability_limits"),
        CheckConstraint("0<=exit_threshold AND exit_threshold<entry_threshold AND entry_threshold<=12285", name="thresholds"),
        UniqueConstraint("device_id", "calibrated_at", name="uq_bed_calibrations_device_calibrated"),
        Index("ix_bed_calibrations_device_time", "device_id", text("calibrated_at DESC"), text("id DESC")),
    )

    id = Column(BigInteger, Identity(), primary_key=True)
    device_id = Column(String(32), ForeignKey("devices.device_id", ondelete="RESTRICT"), nullable=False)
    calibrated_at = Column(DateTime(timezone=True), nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    left_sample_count = Column(SmallInteger, nullable=False)
    left_baseline = Column(Double, nullable=False)
    left_polarity = Column(SmallInteger, nullable=False)
    left_stability_limit = Column(SmallInteger, nullable=False)
    center_sample_count = Column(SmallInteger, nullable=False)
    center_baseline = Column(Double, nullable=False)
    center_polarity = Column(SmallInteger, nullable=False)
    center_stability_limit = Column(SmallInteger, nullable=False)
    right_sample_count = Column(SmallInteger, nullable=False)
    right_baseline = Column(Double, nullable=False)
    right_polarity = Column(SmallInteger, nullable=False)
    right_stability_limit = Column(SmallInteger, nullable=False)
    entry_threshold = Column(Integer, nullable=False)
    exit_threshold = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class RestSession(Base):
    __tablename__ = "rest_sessions"
    __table_args__ = (
        CheckConstraint("subject_id IN ('dog_001','cat_001')", name="subject_id"),
        CheckConstraint("last_confirmed_at>=started_at", name="last_confirmed"),
        CheckConstraint(
            "(ended_at IS NULL AND duration_seconds IS NULL AND close_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND duration_seconds IS NOT NULL AND close_reason IS NOT NULL AND ended_at>=started_at AND duration_seconds>=0 AND close_reason IN ('pressure_exit','camera_exit','sensor_loss','camera_loss','shutdown','restart'))",
            name="open_closed",
        ),
        UniqueConstraint("behavior_event_id", name="uq_rest_sessions_behavior_event_id"),
        Index("uq_rest_sessions_one_open", literal_column("(1)"), unique=True, postgresql_where=text("ended_at IS NULL")),
        Index("ix_rest_sessions_subject_time", "subject_id", text("started_at DESC"), text("id DESC")),
        Index("ix_rest_sessions_end_time", text("ended_at DESC"), text("id DESC")),
    )

    id = Column(BigInteger, Identity(), primary_key=True)
    subject_id = Column(String(16), nullable=False)
    behavior_event_id = Column(BigInteger, ForeignKey("behavior_events.id", ondelete="RESTRICT"), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    last_confirmed_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    close_reason = Column(String(24), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
