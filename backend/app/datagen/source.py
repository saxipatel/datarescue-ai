"""Deterministic per-sequence batches for synthetic sources.

A source's `settings` holds `base_seed` and `records_per_batch`. Batch N of a source is
always generated with the same seed, so a "redelivery" of a missing batch reproduces the
exact events (and event IDs) the producer would have sent.
"""

from datetime import datetime

from app.datagen.generator import GenerationSpec
from app.models import SourceSystem

DEFAULT_RECORDS = 1000
DEFAULT_SPAN_MINUTES = 60


def sequence_seed(source: SourceSystem, sequence: int) -> int:
    base = int(source.settings.get("base_seed", 42))
    return base * 10_000 + sequence


def spec_for_sequence(
    source: SourceSystem,
    sequence: int,
    anchor: datetime,
    *,
    records: int | None = None,
    scenario: str = "clean",
    corruption_pct: float = 0.0,
    prior_payloads: tuple = (),
) -> GenerationSpec:
    return GenerationSpec(
        source_name=source.name,
        records=records or int(source.settings.get("records_per_batch", DEFAULT_RECORDS)),
        seed=sequence_seed(source, sequence),
        scenario=scenario,
        corruption_pct=corruption_pct,
        anchor=anchor,
        span_minutes=int(source.settings.get("span_minutes", DEFAULT_SPAN_MINUTES)),
        schema_version=source.contract_version,
        prior_payloads=prior_payloads,
    )
