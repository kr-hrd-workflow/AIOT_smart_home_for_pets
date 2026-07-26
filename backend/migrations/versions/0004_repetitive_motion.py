"""Add repetitive-motion anomalies."""

from __future__ import annotations

from alembic import op


revision = "0004_repetitive_motion"
down_revision = "0003_activity_observations"
branch_labels = None
depends_on = None


NEW_TYPE_CHECK = "anomaly_type IN ('no_meal_12h','bed_sensor_mismatch','repetitive_motion')"
OLD_TYPE_CHECK = "anomaly_type IN ('no_meal_12h','bed_sensor_mismatch')"
NEW_RELATION_CHECK = (
    "((anomaly_type='no_meal_12h' AND subject_id IN ('dog_001','cat_001') AND mismatch_kind IS NULL AND source_behavior_event_id IS NOT NULL) OR "
    "(anomaly_type='bed_sensor_mismatch' AND mismatch_kind='sensor_check' AND subject_id IN ('dog_001','cat_001') AND source_behavior_event_id IS NULL) OR "
    "(anomaly_type='bed_sensor_mismatch' AND mismatch_kind='unconfirmed_pressure' AND subject_id IS NULL AND source_behavior_event_id IS NULL) OR "
    "(anomaly_type='repetitive_motion' AND subject_id IN ('dog_001','cat_001') AND mismatch_kind IS NULL AND source_behavior_event_id IS NULL)) IS TRUE"
)
OLD_RELATION_CHECK = (
    "((anomaly_type='no_meal_12h' AND subject_id IN ('dog_001','cat_001') AND mismatch_kind IS NULL AND source_behavior_event_id IS NOT NULL) OR "
    "(anomaly_type='bed_sensor_mismatch' AND mismatch_kind='sensor_check' AND subject_id IN ('dog_001','cat_001') AND source_behavior_event_id IS NULL) OR "
    "(anomaly_type='bed_sensor_mismatch' AND mismatch_kind='unconfirmed_pressure' AND subject_id IS NULL AND source_behavior_event_id IS NULL)) IS TRUE"
)


def upgrade() -> None:
    op.execute("ALTER TABLE anomaly_events DROP CONSTRAINT ck_anomaly_events_anomaly_type")
    op.execute("ALTER TABLE anomaly_events DROP CONSTRAINT ck_anomaly_events_relation")
    op.execute(f"ALTER TABLE anomaly_events ADD CONSTRAINT ck_anomaly_events_anomaly_type CHECK ({NEW_TYPE_CHECK})")
    op.execute(f"ALTER TABLE anomaly_events ADD CONSTRAINT ck_anomaly_events_relation CHECK ({NEW_RELATION_CHECK})")


def downgrade() -> None:
    op.execute("DELETE FROM anomaly_events WHERE anomaly_type='repetitive_motion'")
    op.execute("SET CONSTRAINTS ck_anomaly_source_behavior IMMEDIATE")
    op.execute("ALTER TABLE anomaly_events DROP CONSTRAINT ck_anomaly_events_anomaly_type")
    op.execute("ALTER TABLE anomaly_events DROP CONSTRAINT ck_anomaly_events_relation")
    op.execute(f"ALTER TABLE anomaly_events ADD CONSTRAINT ck_anomaly_events_anomaly_type CHECK ({OLD_TYPE_CHECK})")
    op.execute(f"ALTER TABLE anomaly_events ADD CONSTRAINT ck_anomaly_events_relation CHECK ({OLD_RELATION_CHECK})")
