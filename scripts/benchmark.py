"""Reproducible performance and correctness measurements.

    python scripts/benchmark.py --records 1000 10000 100000 --seed 42

Uses its own database (BENCH_DATABASE_URL, default .../datarescue_bench), reset before
each size. Each size runs in a fresh subprocess so peak RSS is per size. Results are
printed as a table and written to data/benchmarks/results-<timestamp>.json.

Measured per size, on a mixed_faults dataset with 10% faulty rows:
  generation       rows/s to write the JSONL file
  validation       rows/s through validate_row only (no database)
  ingestion        rows/s end to end (read, hash, raw evidence, validate, write, finalize)
  detection        seconds from batch start to incident creation
  correctness      ingestion counts == generator ground truth; labeled vs quarantined rows
  duplicates       re-sending the same file adds zero accepted rows
  recovery         NORMALIZE_CURRENCY_CASE duration and verification result
  replay           crash mid-recovery, retry with same key; rows written == dry run, no dupes
"""

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_BENCH_DB = "postgresql+psycopg://datarescue:datarescue@postgres:5432/datarescue_bench"


def _setup_env() -> None:
    os.environ["DATABASE_URL"] = os.environ.get("BENCH_DATABASE_URL", DEFAULT_BENCH_DB)
    os.environ["TASK_MODE"] = "sync"
    os.environ.setdefault("DATA_DIR", "/tmp/datarescue-bench")  # noqa: S108 - throwaway data


def peak_rss_mb() -> float:
    # ru_maxrss is KiB on Linux, bytes on macOS.
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(raw / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def reset_database() -> None:
    import _bootstrap  # noqa: F401
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.db import get_engine, session_scope
    from app.services.reference import load_reference_data

    backend = Path(__import__("app").__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(cfg, "head")
    with get_engine().begin() as conn:
        tables = conn.scalars(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename <> 'alembic_version'"
            )
        ).all()
        conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    with session_scope() as session:
        load_reference_data(session)


def run_single(records: int, seed: int) -> dict:
    import _bootstrap  # noqa: F401
    from sqlalchemy import func, select, text

    from app.config import get_settings
    from app.datagen.files import write_dataset
    from app.datagen.generator import GenerationSpec
    from app.db import get_sessionmaker
    from app.diagnosis.service import diagnose_incident
    from app.models import AcceptedTransaction, Dataset, QuarantineRecord, SourceSystem, User
    from app.models.enums import RepairAction
    from app.quality.validator import RuleSet, Watermark, validate_row
    from app.recovery.executor import SimulatedCrash, run_recovery, start_recovery
    from app.recovery.proposals import create_proposal, review, run_dry_run
    from app.services.audit import Actor
    from app.services.ingestion import load_ruleset
    from app.services.pipeline import create_batch, data_path, run_batch
    from app.services.readers import iter_jsonl

    session = get_sessionmaker()()
    source = session.scalars(select(SourceSystem).where(SourceSystem.name == "payments_core")).one()
    out: dict = {"records": records, "seed": seed, "scenario": "mixed_faults", "fault_pct": 10}

    # generation
    spec = GenerationSpec(
        source.name, records, seed, "mixed_faults", 10, datetime.now(UTC).replace(microsecond=0)
    )
    relative = f"bench/mixed_{records}_{seed}.jsonl"
    path = data_path(relative)
    t0 = time.perf_counter()
    manifest = write_dataset(spec, path, "jsonl")
    gen_s = time.perf_counter() - t0
    out["generation"] = {"seconds": round(gen_s, 3), "rows_per_sec": round(records / gen_s)}
    out["file_mb"] = round(path.stat().st_size / 1e6, 2)

    # validation only
    rules: RuleSet = load_ruleset(session, source)
    wm = Watermark()
    t0 = time.perf_counter()
    for payload in iter_jsonl(path):
        validate_row(payload, rules, wm)
    val_s = time.perf_counter() - t0
    out["validation"] = {"seconds": round(val_s, 3), "rows_per_sec": round(records / val_s)}

    # ingestion
    dataset = Dataset(
        scenario="mixed_faults",
        seed=seed,
        record_count=records,
        source_id=source.id,
        file_name=relative,
        manifest=manifest,
    )
    session.add(dataset)
    session.flush()
    batch, _ = create_batch(
        session,
        source=source,
        file_name=relative,
        input_format="jsonl",
        correlation_id=f"bench-{records}",
        request_key=f"bench:{records}",
        expected_count=records,
        dataset_id=dataset.id,
        details={"generation": spec.as_params()},
    )
    session.commit()
    t0 = time.perf_counter()
    incident = run_batch(session, batch)
    ing_s = time.perf_counter() - t0
    session.refresh(batch)
    expected = manifest["expected"]
    out["ingestion"] = {
        "seconds": round(ing_s, 3),
        "rows_per_sec": round(records / ing_s),
        "chunk_size": get_settings().ingestion_chunk_size,
        "status": batch.status,
        "accepted": batch.accepted_count,
        "quarantined": batch.rejected_count,
        "skipped": batch.skipped_count,
        "late_warnings": batch.warning_count,
    }
    labeled = {lab["event_id"] for lab in manifest["labels"] if lab["expected"] == "quarantined"}
    quarantined = set(
        session.scalars(
            select(QuarantineRecord.event_id).where(QuarantineRecord.batch_id == batch.id)
        )
    )
    out["correctness"] = {
        "counts_match_ground_truth": (
            batch.accepted_count,
            batch.rejected_count,
            batch.skipped_count,
            batch.warning_count,
        )
        == (
            expected["accepted"],
            expected["quarantined"],
            expected["skipped"],
            expected["late_warnings"],
        ),
        "labels_inline": not manifest["labels_truncated"],
        "quarantine_true_positives": len(quarantined & labeled),
        "quarantine_false_positives": len(quarantined - labeled),
        "quarantine_false_negatives": len(labeled - quarantined),
        "processing_success_rate": round(
            (batch.accepted_count + batch.rejected_count + batch.skipped_count)
            / batch.received_count,
            4,
        ),
    }
    out["detection_latency_seconds"] = (
        round((incident.detected_at - batch.started_at).total_seconds(), 3) if incident else None
    )

    # duplicate prevention: the same file again under a new request key
    accepted_before = session.scalar(select(func.count()).select_from(AcceptedTransaction))
    again, _ = create_batch(
        session,
        source=source,
        file_name=relative,
        input_format="jsonl",
        correlation_id="bench-dup",
        request_key=f"bench:{records}:again",
    )
    session.commit()
    t0 = time.perf_counter()
    run_batch(session, again)
    session.refresh(again)
    accepted_after = session.scalar(select(func.count()).select_from(AcceptedTransaction))
    out["duplicate_prevention"] = {
        "resent_rows": again.received_count,
        "accepted_added": accepted_after - accepted_before,
        "skipped": again.skipped_count,
        "seconds": round(time.perf_counter() - t0, 3),
    }

    # recovery: crash halfway through a currency repair, then retry with the same key
    engineer = session.scalars(select(User).where(User.email == "engineer@example.test")).one()
    steward = session.scalars(select(User).where(User.email == "steward@example.test")).one()
    diagnose_incident(session, incident.id, Actor.system("bench"))
    session.commit()
    proposal = create_proposal(
        session,
        engineer,
        incident.id,
        RepairAction.NORMALIZE_CURRENCY_CASE,
        "benchmark currency repair",
    )
    run_dry_run(session, engineer, proposal)
    review(session, steward, proposal, approve=True, note="bench")
    session.commit()
    would_insert = proposal.dry_run_summary["counts"]["would_insert"]
    job, _ = start_recovery(session, steward, proposal.id, f"bench-{records}")
    session.commit()
    crash_after = max(1, proposal.target_count // 2)
    t0 = time.perf_counter()
    try:
        run_recovery(job.id, crash_after=crash_after)
        crashed = False
    except SimulatedCrash:
        crashed = True
    session.expire_all()
    start_recovery(session, steward, proposal.id, f"bench-{records}")
    session.commit()
    state = run_recovery(job.id)
    rec_s = time.perf_counter() - t0
    session.expire_all()
    session.refresh(job)
    written = session.scalar(
        select(func.count())
        .select_from(AcceptedTransaction)
        .where(AcceptedTransaction.recovery_job_id == job.id)
    )
    dupes = session.execute(
        text(
            "SELECT count(*) FROM (SELECT event_id FROM accepted_transactions GROUP BY event_id "
            "HAVING count(*) > 1) d"
        )
    ).scalar()
    _, outcome = start_recovery(session, steward, proposal.id, f"bench-{records}")
    out["recovery"] = {
        "action": "NORMALIZE_CURRENCY_CASE",
        "target_rows": proposal.target_count,
        "seconds_including_crash_and_retry": round(rec_s, 3),
        "crash_injected_after": crash_after if crashed else None,
        "attempts": job.attempts,
        "state": state.value,
        "verification_passed": bool(job.verification and job.verification["passed"]),
    }
    out["replay_correctness"] = {
        "dry_run_would_insert": would_insert,
        "rows_written_by_job": written,
        "duplicate_event_ids": dupes,
        "repeat_same_key_outcome": outcome,
    }
    out["peak_rss_mb"] = peak_rss_mb()
    session.close()
    return out


def environment() -> dict:
    import _bootstrap  # noqa: F401
    from sqlalchemy import text

    from app.db import get_engine

    mem = None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        first = meminfo.read_text().splitlines()[0].split()
        mem = round(int(first[1]) / 1024 / 1024, 1)
    with get_engine().connect() as conn:
        pg = conn.execute(text("SHOW server_version")).scalar()
    return {
        "measured_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory_gb_visible": mem,
        "postgres": pg,
        "note": "Single process, synchronous ingestion; Postgres in a local container.",
    }


def table(results: list[dict]) -> str:
    lines = [
        "| records | gen rows/s | validate rows/s | ingest rows/s | ingest s | detect s "
        "| counts match | FP | FN | dup rows added | recovery s | verified | peak RSS MB |",
        "|---:|---:|---:|---:|---:|---:|:-:|---:|---:|---:|---:|:-:|---:|",
    ]
    for r in results:
        c = r["correctness"]
        lines.append(
            f"| {r['records']:,} | {r['generation']['rows_per_sec']:,} "
            f"| {r['validation']['rows_per_sec']:,} | {r['ingestion']['rows_per_sec']:,} "
            f"| {r['ingestion']['seconds']} | {r['detection_latency_seconds']} "
            f"| {'yes' if c['counts_match_ground_truth'] else 'NO'} "
            f"| {c['quarantine_false_positives']} | {c['quarantine_false_negatives']} "
            f"| {r['duplicate_prevention']['accepted_added']} "
            f"| {r['recovery']['seconds_including_crash_and_retry']} "
            f"| {'yes' if r['recovery']['verification_passed'] else 'NO'} | {r['peak_rss_mb']} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="DataRescue benchmark")
    parser.add_argument("--records", type=int, nargs="+", default=[1000, 10000, 100000])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out", type=Path, default=Path(os.environ.get("BENCH_OUT", REPO / "data" / "benchmarks"))
    )
    parser.add_argument("--single", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    _setup_env()

    if args.single:
        print(json.dumps(run_single(args.single, args.seed), default=str))
        return 0

    results = []
    for n in args.records:
        # Each size starts from an empty database. Datasets with the same seed share some
        # event IDs (with different payloads), which ingestion would rightly quarantine as
        # conflicting duplicates if earlier runs were still present.
        reset_database()
        print(f"running {n:,} records ...", flush=True)
        proc = subprocess.run(  # noqa: S603 - re-invokes this script with fixed args
            [sys.executable, __file__, "--single", str(n), "--seed", str(args.seed)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            print(proc.stderr[-3000:], file=sys.stderr)
            return proc.returncode
        results.append(json.loads(proc.stdout.strip().splitlines()[-1]))
    report = {"environment": environment(), "results": results}
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / f"results-{stamp}.json"
    target.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report["environment"], indent=2))
    print(table(results))
    print(f"\nwritten to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
