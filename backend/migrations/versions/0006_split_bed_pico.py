"""Split bed sensors from the pet-zone Pico."""

from __future__ import annotations

from alembic import op


revision = "0006_split_bed_pico"
down_revision = "0005_activity_cleanup_state"
branch_labels = None
depends_on = None


def _drop_profile_constraints() -> None:
    op.execute("ALTER TABLE sensor_readings DROP CONSTRAINT ck_sensor_readings_type_unit_value")
    op.execute("ALTER TABLE sensor_readings DROP CONSTRAINT ck_sensor_readings_device_profile")
    op.execute("ALTER TABLE bed_calibrations DROP CONSTRAINT ck_bed_calibrations_device_id")
    op.execute("ALTER TABLE devices DROP CONSTRAINT ck_devices_device_id")


def upgrade() -> None:
    _drop_profile_constraints()
    op.execute("ALTER TABLE devices ADD CONSTRAINT ck_devices_device_id CHECK (device_id IN ('entrance-01','petzone-01','bed-01'))")
    op.execute("INSERT INTO devices (device_id) VALUES ('bed-01') ON CONFLICT (device_id) DO NOTHING")
    op.execute("UPDATE sensor_readings SET device_id='bed-01' WHERE device_id='petzone-01' AND sensor_type IN ('temperature','humidity','bed_pressure_left','bed_pressure_center','bed_pressure_right')")
    op.execute("DELETE FROM sensor_readings WHERE device_id='petzone-01' AND sensor_type IN ('presence_moving','presence_stationary')")
    op.execute("DELETE FROM sensor_readings WHERE sensor_type IN ('food_weight','water_weight') AND (value_number < 0 OR value_number > 5000)")
    op.execute("UPDATE bed_calibrations SET device_id='bed-01'")
    op.execute("ALTER TABLE sensor_readings ADD CONSTRAINT ck_sensor_readings_type_unit_value CHECK ((sensor_type='temperature' AND unit='C' AND value_number IS NOT NULL AND value_boolean IS NULL) OR (sensor_type='humidity' AND unit='%' AND value_number IS NOT NULL AND value_boolean IS NULL) OR (sensor_type IN ('presence_moving','presence_stationary') AND unit='bool' AND value_number IS NULL AND value_boolean IS NOT NULL) OR (sensor_type IN ('food_weight','water_weight') AND device_id='petzone-01' AND unit='g' AND value_number IS NOT NULL AND value_boolean IS NULL) OR (sensor_type IN ('bed_pressure_left','bed_pressure_center','bed_pressure_right') AND device_id='bed-01' AND unit='adc' AND value_number IS NOT NULL AND value_boolean IS NULL))")
    op.execute("ALTER TABLE sensor_readings ADD CONSTRAINT ck_sensor_readings_device_profile CHECK ((device_id='entrance-01' AND sensor_type IN ('temperature','humidity','presence_moving','presence_stationary')) OR (device_id='petzone-01' AND sensor_type IN ('food_weight','water_weight')) OR (device_id='bed-01' AND sensor_type IN ('temperature','humidity','bed_pressure_left','bed_pressure_center','bed_pressure_right')))")
    op.execute("ALTER TABLE bed_calibrations ADD CONSTRAINT ck_bed_calibrations_device_id CHECK (device_id='bed-01')")
    op.execute("ALTER TABLE sensor_readings ADD CONSTRAINT ck_sensor_readings_weight_range CHECK (sensor_type NOT IN ('food_weight','water_weight') OR value_number BETWEEN 0 AND 5000)")


def downgrade() -> None:
    op.execute("ALTER TABLE sensor_readings DROP CONSTRAINT ck_sensor_readings_weight_range")
    _drop_profile_constraints()
    op.execute("UPDATE sensor_readings SET device_id='petzone-01' WHERE device_id='bed-01'")
    op.execute("UPDATE bed_calibrations SET device_id='petzone-01'")
    op.execute("DELETE FROM devices WHERE device_id='bed-01'")
    op.execute("ALTER TABLE devices ADD CONSTRAINT ck_devices_device_id CHECK (device_id IN ('entrance-01','petzone-01'))")
    op.execute("ALTER TABLE sensor_readings ADD CONSTRAINT ck_sensor_readings_type_unit_value CHECK ((sensor_type='temperature' AND unit='C' AND value_number IS NOT NULL AND value_boolean IS NULL) OR (sensor_type='humidity' AND unit='%' AND value_number IS NOT NULL AND value_boolean IS NULL) OR (sensor_type IN ('presence_moving','presence_stationary') AND unit='bool' AND value_number IS NULL AND value_boolean IS NOT NULL) OR (sensor_type IN ('food_weight','water_weight') AND device_id='petzone-01' AND unit='g' AND value_number IS NOT NULL AND value_boolean IS NULL) OR (sensor_type IN ('bed_pressure_left','bed_pressure_center','bed_pressure_right') AND device_id='petzone-01' AND unit='adc' AND value_number IS NOT NULL AND value_boolean IS NULL))")
    op.execute("ALTER TABLE sensor_readings ADD CONSTRAINT ck_sensor_readings_device_profile CHECK (device_id='petzone-01' OR sensor_type IN ('temperature','humidity','presence_moving','presence_stationary'))")
    op.execute("ALTER TABLE bed_calibrations ADD CONSTRAINT ck_bed_calibrations_device_id CHECK (device_id='petzone-01')")
