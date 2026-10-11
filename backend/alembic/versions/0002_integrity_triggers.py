"""integrity triggers: append-only audit, terminal states

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10

Audit rows reject UPDATE and DELETE. This guards against application bugs, not against a
database superuser, who can still disable the trigger (see docs/security.md).

Incidents in RESOLVED / CLOSED_AS_FALSE_POSITIVE and proposals in REJECTED / EXECUTED
are terminal; the application enforces the full state machine, the database guarantees
terminal states stay terminal.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION audit_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% rejected)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER audit_events_no_update_delete
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

        CREATE FUNCTION incidents_terminal_guard() RETURNS trigger AS $$
        BEGIN
            IF OLD.state IN ('RESOLVED', 'CLOSED_AS_FALSE_POSITIVE')
               AND NEW.state IS DISTINCT FROM OLD.state THEN
                RAISE EXCEPTION 'incident % is terminal (%)', OLD.id, OLD.state
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER incidents_terminal_state
            BEFORE UPDATE OF state ON incidents
            FOR EACH ROW EXECUTE FUNCTION incidents_terminal_guard();

        CREATE FUNCTION proposals_terminal_guard() RETURNS trigger AS $$
        BEGIN
            IF OLD.status IN ('REJECTED', 'EXECUTED')
               AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'proposal % is terminal (%)', OLD.id, OLD.status
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER repair_proposals_terminal_status
            BEFORE UPDATE OF status ON repair_proposals
            FOR EACH ROW EXECUTE FUNCTION proposals_terminal_guard();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS repair_proposals_terminal_status ON repair_proposals;
        DROP FUNCTION IF EXISTS proposals_terminal_guard();
        DROP TRIGGER IF EXISTS incidents_terminal_state ON incidents;
        DROP FUNCTION IF EXISTS incidents_terminal_guard();
        DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events;
        DROP FUNCTION IF EXISTS audit_events_append_only();
        """
    )
