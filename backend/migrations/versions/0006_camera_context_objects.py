"""Store static pet-home context objects alongside pet detections."""

from __future__ import annotations

from alembic import op


revision = "0006_camera_context_objects"
down_revision = "0005_activity_cleanup_state"
branch_labels = None
depends_on = None


NEW_TYPE_CHECK = "detected_type IN ('person','dog','cat','bowl','bed','couch')"
OLD_TYPE_CHECK = "detected_type IN ('person','dog','cat')"
NEW_SUBJECT_CHECK = (
    "((detected_type IN ('person','bowl','bed','couch') AND subject_id IS NULL) OR "
    "(detected_type='dog' AND subject_id='dog_001') OR "
    "(detected_type='cat' AND subject_id='cat_001')) IS TRUE"
)
OLD_SUBJECT_CHECK = (
    "((detected_type='person' AND subject_id IS NULL) OR "
    "(detected_type='dog' AND subject_id='dog_001') OR "
    "(detected_type='cat' AND subject_id='cat_001')) IS TRUE"
)


def _replace(type_check: str, subject_check: str) -> None:
    op.execute("ALTER TABLE camera_events DROP CONSTRAINT ck_camera_events_detected_type")
    op.execute("ALTER TABLE camera_events DROP CONSTRAINT ck_camera_events_subject_type")
    op.execute(
        "ALTER TABLE camera_events ADD CONSTRAINT ck_camera_events_detected_type "
        f"CHECK ({type_check})"
    )
    op.execute(
        "ALTER TABLE camera_events ADD CONSTRAINT ck_camera_events_subject_type "
        f"CHECK ({subject_check})"
    )


def upgrade() -> None:
    _replace(NEW_TYPE_CHECK, NEW_SUBJECT_CHECK)


def downgrade() -> None:
    op.execute("DELETE FROM camera_events WHERE detected_type IN ('bowl','bed','couch')")
    _replace(OLD_TYPE_CHECK, OLD_SUBJECT_CHECK)
