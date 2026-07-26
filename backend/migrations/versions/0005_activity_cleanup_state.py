"""Add local activity-cleanup state."""

from __future__ import annotations

from alembic import op


revision = "0005_activity_cleanup_state"
down_revision = "0004_repetitive_motion"
branch_labels = None
depends_on = None


CREATE_TABLE = """
CREATE TABLE activity_cleanup_state (
    singleton SMALLINT NOT NULL,
    agent_id VARCHAR(128),
    activity_enabled BOOLEAN DEFAULT TRUE NOT NULL,
    command_id VARCHAR(36),
    applied_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_activity_cleanup_state PRIMARY KEY (singleton),
    CONSTRAINT ck_activity_cleanup_state_singleton CHECK (singleton=1),
    CONSTRAINT ck_activity_cleanup_state_agent_id CHECK (
        agent_id IS NULL OR (length(agent_id) BETWEEN 1 AND 128 AND agent_id ~ '^[A-Za-z0-9._:-]+$')
    ),
    CONSTRAINT ck_activity_cleanup_state_command_id CHECK (
        command_id IS NULL OR command_id ~ '^clc_[0-9a-f]{32}$'
    ),
    CONSTRAINT ck_activity_cleanup_state_state CHECK (
        (activity_enabled AND command_id IS NULL AND applied_at IS NULL) OR
        (NOT activity_enabled AND agent_id IS NOT NULL AND command_id IS NOT NULL AND applied_at IS NOT NULL)
    )
)
"""


def upgrade() -> None:
    op.execute(CREATE_TABLE)
    op.execute(
        "INSERT INTO activity_cleanup_state "
        "(singleton, agent_id, activity_enabled, command_id, applied_at) "
        "VALUES (1, NULL, TRUE, NULL, NULL)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE activity_cleanup_state")
