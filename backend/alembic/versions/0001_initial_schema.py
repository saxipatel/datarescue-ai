"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-10 20:56:32.818411
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('audit_events',
    sa.Column('actor_id', sa.String(length=80), nullable=False),
    sa.Column('actor_role', sa.String(length=20), nullable=True),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('entity_type', sa.String(length=40), nullable=False),
    sa.Column('entity_id', sa.String(length=80), nullable=False),
    sa.Column('before_hash', sa.String(length=64), nullable=True),
    sa.Column('after_hash', sa.String(length=64), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('correlation_id', sa.String(length=64), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events'))
    )
    op.create_index('ix_audit_entity', 'audit_events', ['entity_type', 'entity_id'], unique=False)
    op.create_index('ix_audit_occurred_at', 'audit_events', ['occurred_at'], unique=False)
    op.create_table('lineage_nodes',
    sa.Column('node_key', sa.String(length=80), nullable=False),
    sa.Column('node_type', sa.String(length=30), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=False),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lineage_nodes')),
    sa.UniqueConstraint('node_key', name=op.f('uq_lineage_nodes_node_key'))
    )
    op.create_table('quality_rules',
    sa.Column('rule_key', sa.String(length=40), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('configuration', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("severity IN ('low', 'medium', 'high', 'critical')", name=op.f('ck_quality_rules_severity_valid')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_quality_rules_version_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_quality_rules')),
    sa.UniqueConstraint('rule_key', name=op.f('uq_quality_rules_rule_key'))
    )
    op.create_table('source_systems',
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('contract_version', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('lateness_grace_minutes', sa.Integer(), nullable=False),
    sa.Column('freshness_max_age_minutes', sa.Integer(), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('contract_version > 0', name=op.f('ck_source_systems_contract_version_positive')),
    sa.CheckConstraint('lateness_grace_minutes >= 0', name=op.f('ck_source_systems_grace_nonnegative')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_systems')),
    sa.UniqueConstraint('name', name=op.f('uq_source_systems_name'))
    )
    op.create_table('users',
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('full_name', sa.String(length=120), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('analyst', 'engineer', 'steward', 'admin')", name=op.f('ck_users_role_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('datasets',
    sa.Column('scenario', sa.String(length=40), nullable=False),
    sa.Column('seed', sa.Integer(), nullable=False),
    sa.Column('record_count', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('file_name', sa.Text(), nullable=False),
    sa.Column('manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_datasets_created_by_users')),
    sa.ForeignKeyConstraint(['source_id'], ['source_systems.id'], name=op.f('fk_datasets_source_id_source_systems')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_datasets'))
    )
    op.create_table('lineage_edges',
    sa.Column('upstream_node_id', sa.UUID(), nullable=False),
    sa.Column('downstream_node_id', sa.UUID(), nullable=False),
    sa.Column('relation_type', sa.String(length=30), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint('upstream_node_id <> downstream_node_id', name=op.f('ck_lineage_edges_no_self_loop')),
    sa.ForeignKeyConstraint(['downstream_node_id'], ['lineage_nodes.id'], name=op.f('fk_lineage_edges_downstream_node_id_lineage_nodes')),
    sa.ForeignKeyConstraint(['upstream_node_id'], ['lineage_nodes.id'], name=op.f('fk_lineage_edges_upstream_node_id_lineage_nodes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lineage_edges')),
    sa.UniqueConstraint('upstream_node_id', 'downstream_node_id', 'relation_type', name='uq_lineage_edges')
    )
    op.create_index(op.f('ix_lineage_edges_downstream_node_id'), 'lineage_edges', ['downstream_node_id'], unique=False)
    op.create_index(op.f('ix_lineage_edges_upstream_node_id'), 'lineage_edges', ['upstream_node_id'], unique=False)
    op.create_table('ingestion_batches',
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('dataset_id', sa.UUID(), nullable=True),
    sa.Column('request_key', sa.String(length=200), nullable=True),
    sa.Column('sequence_number', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('input_format', sa.String(length=10), nullable=False),
    sa.Column('file_name', sa.Text(), nullable=False),
    sa.Column('expected_count', sa.Integer(), nullable=True),
    sa.Column('received_count', sa.Integer(), nullable=False),
    sa.Column('processed_count', sa.Integer(), nullable=False),
    sa.Column('accepted_count', sa.Integer(), nullable=False),
    sa.Column('rejected_count', sa.Integer(), nullable=False),
    sa.Column('skipped_count', sa.Integer(), nullable=False),
    sa.Column('warning_count', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('checksum', sa.String(length=64), nullable=True),
    sa.Column('correlation_id', sa.String(length=64), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('PENDING', 'PROCESSING', 'COMPLETE', 'PARTIAL', 'FAILED')", name=op.f('ck_ingestion_batches_status_valid')),
    sa.CheckConstraint('accepted_count + rejected_count + skipped_count = processed_count', name=op.f('ck_ingestion_batches_counts_reconcile')),
    sa.CheckConstraint('expected_count IS NULL OR expected_count >= 0', name=op.f('ck_ingestion_batches_expected_nonneg')),
    sa.CheckConstraint('received_count >= 0 AND processed_count >= 0 AND accepted_count >= 0 AND rejected_count >= 0 AND skipped_count >= 0 AND warning_count >= 0', name=op.f('ck_ingestion_batches_counts_nonnegative')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_ingestion_batches_created_by_users')),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], name=op.f('fk_ingestion_batches_dataset_id_datasets')),
    sa.ForeignKeyConstraint(['source_id'], ['source_systems.id'], name=op.f('fk_ingestion_batches_source_id_source_systems')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ingestion_batches')),
    sa.UniqueConstraint('request_key', name=op.f('uq_ingestion_batches_request_key')),
    sa.UniqueConstraint('source_id', 'sequence_number', name='uq_batches_source_sequence')
    )
    op.create_index('ix_ingestion_batches_source_started', 'ingestion_batches', ['source_id', 'started_at'], unique=False)
    op.create_index(op.f('ix_ingestion_batches_status'), 'ingestion_batches', ['status'], unique=False)
    op.create_table('incidents',
    sa.Column('incident_key', sa.String(length=160), nullable=False),
    sa.Column('category', sa.String(length=30), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=True),
    sa.Column('source_id', sa.UUID(), nullable=True),
    sa.Column('origin_node_key', sa.String(length=80), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('failed_rules', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('diagnosis', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint("severity IN ('low', 'medium', 'high', 'critical')", name=op.f('ck_incidents_severity_valid')),
    sa.CheckConstraint("state IN ('OPEN', 'INVESTIGATING', 'ACTION_REQUIRED', 'ACKNOWLEDGED', 'RECOVERING', 'RESOLVED', 'CLOSED_AS_FALSE_POSITIVE')", name=op.f('ck_incidents_state_valid')),
    sa.ForeignKeyConstraint(['batch_id'], ['ingestion_batches.id'], name=op.f('fk_incidents_batch_id_ingestion_batches')),
    sa.ForeignKeyConstraint(['source_id'], ['source_systems.id'], name=op.f('fk_incidents_source_id_source_systems')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_incidents')),
    sa.UniqueConstraint('incident_key', name=op.f('uq_incidents_incident_key'))
    )
    op.create_index(op.f('ix_incidents_batch_id'), 'incidents', ['batch_id'], unique=False)
    op.create_index('ix_incidents_detected_at', 'incidents', ['detected_at'], unique=False)
    op.create_index('ix_incidents_state_severity', 'incidents', ['state', 'severity'], unique=False)
    op.create_table('pipeline_runs',
    sa.Column('pipeline_name', sa.String(length=40), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=True),
    sa.Column('subject_id', sa.UUID(), nullable=True),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('correlation_id', sa.String(length=64), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("state IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')", name=op.f('ck_pipeline_runs_state_valid')),
    sa.CheckConstraint('attempts >= 0', name=op.f('ck_pipeline_runs_attempts_nonnegative')),
    sa.ForeignKeyConstraint(['batch_id'], ['ingestion_batches.id'], name=op.f('fk_pipeline_runs_batch_id_ingestion_batches')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_pipeline_runs_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pipeline_runs'))
    )
    op.create_index(op.f('ix_pipeline_runs_batch_id'), 'pipeline_runs', ['batch_id'], unique=False)
    op.create_index(op.f('ix_pipeline_runs_correlation_id'), 'pipeline_runs', ['correlation_id'], unique=False)
    op.create_index('ix_pipeline_runs_name_created', 'pipeline_runs', ['pipeline_name', 'created_at'], unique=False)
    op.create_index(op.f('ix_pipeline_runs_subject_id'), 'pipeline_runs', ['subject_id'], unique=False)
    op.create_table('quality_results',
    sa.Column('batch_id', sa.UUID(), nullable=True),
    sa.Column('source_id', sa.UUID(), nullable=True),
    sa.Column('rule_id', sa.UUID(), nullable=False),
    sa.Column('rule_version', sa.Integer(), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('failed_count', sa.Integer(), nullable=False),
    sa.Column('checked_count', sa.Integer(), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint('failed_count >= 0 AND checked_count >= 0', name=op.f('ck_quality_results_counts_nonnegative')),
    sa.ForeignKeyConstraint(['batch_id'], ['ingestion_batches.id'], name=op.f('fk_quality_results_batch_id_ingestion_batches')),
    sa.ForeignKeyConstraint(['rule_id'], ['quality_rules.id'], name=op.f('fk_quality_results_rule_id_quality_rules')),
    sa.ForeignKeyConstraint(['source_id'], ['source_systems.id'], name=op.f('fk_quality_results_source_id_source_systems')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_quality_results'))
    )
    op.create_index('ix_quality_results_checked_at', 'quality_results', ['checked_at'], unique=False)
    op.create_index(op.f('ix_quality_results_rule_id'), 'quality_results', ['rule_id'], unique=False)
    op.create_index('uq_quality_results_batch_rule', 'quality_results', ['batch_id', 'rule_id'], unique=True, postgresql_where='batch_id IS NOT NULL')
    op.create_table('raw_events',
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('row_number', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.String(length=100), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.Column('occurrences', sa.Integer(), nullable=False),
    sa.Column('disposition', sa.String(length=24), nullable=False),
    sa.Column('duplicate_confirmed', sa.Boolean(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint("disposition IN ('PENDING', 'ACCEPTED', 'QUARANTINED', 'DUPLICATE_SKIPPED')", name=op.f('ck_raw_events_disposition_valid')),
    sa.CheckConstraint('occurrences >= 1', name=op.f('ck_raw_events_occurrences_positive')),
    sa.ForeignKeyConstraint(['batch_id'], ['ingestion_batches.id'], name=op.f('fk_raw_events_batch_id_ingestion_batches')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_raw_events')),
    sa.UniqueConstraint('batch_id', 'event_id', 'payload_hash', name='uq_raw_events_identity')
    )
    op.create_index('ix_raw_events_batch_disposition', 'raw_events', ['batch_id', 'disposition'], unique=False)
    op.create_index(op.f('ix_raw_events_event_id'), 'raw_events', ['event_id'], unique=False)
    op.create_table('incident_evidence',
    sa.Column('incident_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name=op.f('fk_incident_evidence_incident_id_incidents')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_incident_evidence'))
    )
    op.create_index(op.f('ix_incident_evidence_incident_id'), 'incident_evidence', ['incident_id'], unique=False)
    op.create_table('pipeline_run_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('level', sa.String(length=10), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['pipeline_runs.id'], name=op.f('fk_pipeline_run_events_run_id_pipeline_runs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pipeline_run_events'))
    )
    op.create_index(op.f('ix_pipeline_run_events_run_id'), 'pipeline_run_events', ['run_id'], unique=False)
    op.create_table('repair_proposals',
    sa.Column('incident_id', sa.UUID(), nullable=False),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('target', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('target_count', sa.Integer(), nullable=False),
    sa.Column('proposed_patch', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('justification', sa.Text(), nullable=False),
    sa.Column('risk_level', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('dry_run_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('dry_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("NOT (risk_level IN ('high','critical') AND reviewed_by = created_by)", name=op.f('ck_repair_proposals_no_self_approval_high_risk')),
    sa.CheckConstraint("action IN ('SKIP_IDENTICAL_DUPLICATE', 'REPLAY_QUARANTINED', 'NORMALIZE_CURRENCY_CASE', 'RETRY_MISSING_BATCH', 'MANUAL_REVIEW_REQUIRED')", name=op.f('ck_repair_proposals_action_valid')),
    sa.CheckConstraint("risk_level IN ('low', 'medium', 'high', 'critical')", name=op.f('ck_repair_proposals_risk_valid')),
    sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXECUTED')", name=op.f('ck_repair_proposals_status_valid')),
    sa.CheckConstraint('target_count >= 0', name=op.f('ck_repair_proposals_target_count_nonneg')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_repair_proposals_created_by_users')),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name=op.f('fk_repair_proposals_incident_id_incidents')),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_repair_proposals_reviewed_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_repair_proposals'))
    )
    op.create_index(op.f('ix_repair_proposals_incident_id'), 'repair_proposals', ['incident_id'], unique=False)
    op.create_index(op.f('ix_repair_proposals_status'), 'repair_proposals', ['status'], unique=False)
    op.create_table('simulation_runs',
    sa.Column('scenario', sa.String(length=40), nullable=False),
    sa.Column('seed', sa.Integer(), nullable=False),
    sa.Column('record_count', sa.Integer(), nullable=False),
    sa.Column('corruption_pct', sa.Float(), nullable=False),
    sa.Column('run_label', sa.String(length=80), nullable=True),
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('dataset_id', sa.UUID(), nullable=True),
    sa.Column('batch_id', sa.UUID(), nullable=True),
    sa.Column('pipeline_run_id', sa.UUID(), nullable=True),
    sa.Column('labels', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('evaluation', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('corruption_pct >= 0 AND corruption_pct <= 100', name=op.f('ck_simulation_runs_corruption_pct_range')),
    sa.ForeignKeyConstraint(['batch_id'], ['ingestion_batches.id'], name=op.f('fk_simulation_runs_batch_id_ingestion_batches')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_simulation_runs_created_by_users')),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], name=op.f('fk_simulation_runs_dataset_id_datasets')),
    sa.ForeignKeyConstraint(['pipeline_run_id'], ['pipeline_runs.id'], name=op.f('fk_simulation_runs_pipeline_run_id_pipeline_runs')),
    sa.ForeignKeyConstraint(['source_id'], ['source_systems.id'], name=op.f('fk_simulation_runs_source_id_source_systems')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_simulation_runs'))
    )
    op.create_table('recovery_jobs',
    sa.Column('incident_id', sa.UUID(), nullable=False),
    sa.Column('proposal_id', sa.UUID(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('state', sa.String(length=12), nullable=False),
    sa.Column('target_batch_id', sa.UUID(), nullable=True),
    sa.Column('dry_run_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('verification', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('correlation_id', sa.String(length=64), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("state IN ('QUEUED', 'RUNNING', 'VERIFYING', 'SUCCEEDED', 'FAILED')", name=op.f('ck_recovery_jobs_state_valid')),
    sa.CheckConstraint('attempts >= 0', name=op.f('ck_recovery_jobs_attempts_nonnegative')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_recovery_jobs_created_by_users')),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name=op.f('fk_recovery_jobs_incident_id_incidents')),
    sa.ForeignKeyConstraint(['proposal_id'], ['repair_proposals.id'], name=op.f('fk_recovery_jobs_proposal_id_repair_proposals')),
    sa.ForeignKeyConstraint(['target_batch_id'], ['ingestion_batches.id'], name=op.f('fk_recovery_jobs_target_batch_id_ingestion_batches')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recovery_jobs')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_recovery_jobs_idempotency_key'))
    )
    op.create_index(op.f('ix_recovery_jobs_incident_id'), 'recovery_jobs', ['incident_id'], unique=False)
    op.create_index(op.f('ix_recovery_jobs_proposal_id'), 'recovery_jobs', ['proposal_id'], unique=False)
    op.create_table('accepted_transactions',
    sa.Column('event_id', sa.String(length=100), nullable=False),
    sa.Column('transaction_id', sa.String(length=100), nullable=False),
    sa.Column('customer_id', sa.String(length=100), nullable=False),
    sa.Column('merchant_id', sa.String(length=100), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('event_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('raw_event_id', sa.UUID(), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.Column('warning_codes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('recovery_job_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f('ck_accepted_transactions_currency_format')),
    sa.CheckConstraint("status IN ('PENDING', 'SETTLED', 'FAILED', 'REFUNDED')", name=op.f('ck_accepted_transactions_status_valid')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_accepted_transactions_amount_positive')),
    sa.ForeignKeyConstraint(['batch_id'], ['ingestion_batches.id'], name=op.f('fk_accepted_transactions_batch_id_ingestion_batches')),
    sa.ForeignKeyConstraint(['raw_event_id'], ['raw_events.id'], name=op.f('fk_accepted_transactions_raw_event_id_raw_events')),
    sa.ForeignKeyConstraint(['recovery_job_id'], ['recovery_jobs.id'], name=op.f('fk_accepted_transactions_recovery_job_id_recovery_jobs')),
    sa.ForeignKeyConstraint(['source_id'], ['source_systems.id'], name=op.f('fk_accepted_transactions_source_id_source_systems')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_accepted_transactions')),
    sa.UniqueConstraint('event_id', name=op.f('uq_accepted_transactions_event_id'))
    )
    op.create_index(op.f('ix_accepted_transactions_batch_id'), 'accepted_transactions', ['batch_id'], unique=False)
    op.create_index('ix_accepted_transactions_event_time', 'accepted_transactions', ['event_time'], unique=False)
    op.create_index(op.f('ix_accepted_transactions_merchant_id'), 'accepted_transactions', ['merchant_id'], unique=False)
    op.create_index(op.f('ix_accepted_transactions_source_id'), 'accepted_transactions', ['source_id'], unique=False)
    op.create_index(op.f('ix_accepted_transactions_transaction_id'), 'accepted_transactions', ['transaction_id'], unique=False)
    op.create_table('quarantine_records',
    sa.Column('raw_event_id', sa.UUID(), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('event_id', sa.String(length=100), nullable=False),
    sa.Column('error_codes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('error_details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('raw_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('repair_candidate', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('recovery_job_id', sa.UUID(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("state IN ('OPEN', 'REPLAYED', 'REPAIRED', 'MANUAL_REVIEW')", name=op.f('ck_quarantine_records_state_valid')),
    sa.ForeignKeyConstraint(['batch_id'], ['ingestion_batches.id'], name=op.f('fk_quarantine_records_batch_id_ingestion_batches')),
    sa.ForeignKeyConstraint(['raw_event_id'], ['raw_events.id'], name=op.f('fk_quarantine_records_raw_event_id_raw_events')),
    sa.ForeignKeyConstraint(['recovery_job_id'], ['recovery_jobs.id'], name=op.f('fk_quarantine_records_recovery_job_id_recovery_jobs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_quarantine_records')),
    sa.UniqueConstraint('raw_event_id', name=op.f('uq_quarantine_records_raw_event_id'))
    )
    op.create_index('ix_quarantine_batch_state', 'quarantine_records', ['batch_id', 'state'], unique=False)
    op.create_index('ix_quarantine_error_codes', 'quarantine_records', ['error_codes'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_quarantine_records_event_id'), 'quarantine_records', ['event_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_quarantine_records_event_id'), table_name='quarantine_records')
    op.drop_index('ix_quarantine_error_codes', table_name='quarantine_records', postgresql_using='gin')
    op.drop_index('ix_quarantine_batch_state', table_name='quarantine_records')
    op.drop_table('quarantine_records')
    op.drop_index(op.f('ix_accepted_transactions_transaction_id'), table_name='accepted_transactions')
    op.drop_index(op.f('ix_accepted_transactions_source_id'), table_name='accepted_transactions')
    op.drop_index(op.f('ix_accepted_transactions_merchant_id'), table_name='accepted_transactions')
    op.drop_index('ix_accepted_transactions_event_time', table_name='accepted_transactions')
    op.drop_index(op.f('ix_accepted_transactions_batch_id'), table_name='accepted_transactions')
    op.drop_table('accepted_transactions')
    op.drop_index(op.f('ix_recovery_jobs_proposal_id'), table_name='recovery_jobs')
    op.drop_index(op.f('ix_recovery_jobs_incident_id'), table_name='recovery_jobs')
    op.drop_table('recovery_jobs')
    op.drop_table('simulation_runs')
    op.drop_index(op.f('ix_repair_proposals_status'), table_name='repair_proposals')
    op.drop_index(op.f('ix_repair_proposals_incident_id'), table_name='repair_proposals')
    op.drop_table('repair_proposals')
    op.drop_index(op.f('ix_pipeline_run_events_run_id'), table_name='pipeline_run_events')
    op.drop_table('pipeline_run_events')
    op.drop_index(op.f('ix_incident_evidence_incident_id'), table_name='incident_evidence')
    op.drop_table('incident_evidence')
    op.drop_index(op.f('ix_raw_events_event_id'), table_name='raw_events')
    op.drop_index('ix_raw_events_batch_disposition', table_name='raw_events')
    op.drop_table('raw_events')
    op.drop_index('uq_quality_results_batch_rule', table_name='quality_results', postgresql_where='batch_id IS NOT NULL')
    op.drop_index(op.f('ix_quality_results_rule_id'), table_name='quality_results')
    op.drop_index('ix_quality_results_checked_at', table_name='quality_results')
    op.drop_table('quality_results')
    op.drop_index(op.f('ix_pipeline_runs_subject_id'), table_name='pipeline_runs')
    op.drop_index('ix_pipeline_runs_name_created', table_name='pipeline_runs')
    op.drop_index(op.f('ix_pipeline_runs_correlation_id'), table_name='pipeline_runs')
    op.drop_index(op.f('ix_pipeline_runs_batch_id'), table_name='pipeline_runs')
    op.drop_table('pipeline_runs')
    op.drop_index('ix_incidents_state_severity', table_name='incidents')
    op.drop_index('ix_incidents_detected_at', table_name='incidents')
    op.drop_index(op.f('ix_incidents_batch_id'), table_name='incidents')
    op.drop_table('incidents')
    op.drop_index(op.f('ix_ingestion_batches_status'), table_name='ingestion_batches')
    op.drop_index('ix_ingestion_batches_source_started', table_name='ingestion_batches')
    op.drop_table('ingestion_batches')
    op.drop_index(op.f('ix_lineage_edges_upstream_node_id'), table_name='lineage_edges')
    op.drop_index(op.f('ix_lineage_edges_downstream_node_id'), table_name='lineage_edges')
    op.drop_table('lineage_edges')
    op.drop_table('datasets')
    op.drop_table('users')
    op.drop_table('source_systems')
    op.drop_table('quality_rules')
    op.drop_table('lineage_nodes')
    op.drop_index('ix_audit_occurred_at', table_name='audit_events')
    op.drop_index('ix_audit_entity', table_name='audit_events')
    op.drop_table('audit_events')
