"""Generate labeled fixture datasets (no database needed).

    python scripts/generate_data.py                     # all standard fixtures
    python scripts/generate_data.py --scenario mixed_faults --records 100000 --seed 42

Each dataset is written as <name>.jsonl with <name>.labels.jsonl (one line per faulty
row) and <name>.manifest.json (parameters and expected outcome counts).
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from app.datagen.files import write_dataset
from app.datagen.generator import ROW_SCENARIOS, GenerationSpec

REPO = Path(__file__).resolve().parents[1]
ANCHOR = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)  # fixed so fixtures are byte-identical

# name -> (scenario, records, corruption %, seed)
FIXTURES = {
    "clean_1000": ("clean", 1000, 0, 42),
    "duplicate_1000": ("duplicate_event_ids", 1000, 10, 42),
    "invalid_amount_1000": ("invalid_amount", 1000, 5, 42),
    "schema_break_1000": ("schema_break", 1000, 20, 42),
    "late_events": ("late_events", 1000, 8, 42),
    "mixed_faults_10000": ("mixed_faults", 10000, 10, 42),
}


def write(out: Path, name: str, spec: GenerationSpec, fmt: str = "jsonl") -> dict:
    manifest = write_dataset(spec, out / f"{name}.{fmt}", fmt)
    manifest.pop("labels")  # full labels live in the .labels.jsonl file
    (out / f"{name}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def standard(out: Path) -> None:
    for name, (scenario, records, pct, seed) in FIXTURES.items():
        spec = GenerationSpec("payments_core", records, seed, scenario, pct, ANCHOR)
        m = write(out, name, spec)
        print(f"{name:22s} {m['expected']}")
    # missing_batch: sequences 1 and 3 delivered, 2 lost. Ingest both with sequence numbers.
    for seq in (1, 3):
        spec = GenerationSpec("payments_core", 500, 4200 + seq, "clean", 0, ANCHOR)
        write(out, f"missing_batch_seq{seq}", spec)
    (out / "missing_batch.manifest.json").write_text(
        json.dumps(
            {
                "description": "Ingest seq1 then seq3 with sequence_number set; seq 2 is missing.",
                "files": ["missing_batch_seq1.jsonl", "missing_batch_seq3.jsonl"],
                "expected": {"incident_rule": "completeness", "missing_sequences": [2]},
            },
            indent=2,
        )
        + "\n"
    )
    print("missing_batch          seq1 + seq3 (seq2 missing)")
    # A small CSV for trying the upload path by hand.
    sample = REPO / "data" / "sample"
    spec = GenerationSpec("payments_core", 100, 7, "mixed_faults", 15, ANCHOR)
    m = write(sample, "upload_sample_100", spec, fmt="csv")
    print(f"sample/upload_sample_100.csv {m['expected']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic datasets")
    parser.add_argument("--out", type=Path, default=REPO / "data" / "fixtures")
    parser.add_argument("--scenario", choices=sorted(ROW_SCENARIOS))
    parser.add_argument("--records", type=int, default=1000)
    parser.add_argument("--pct", type=float, default=10.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--format", choices=["jsonl", "csv"], default="jsonl")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.scenario is None:
        standard(args.out)
        return 0
    name = f"{args.scenario}_{args.records}_s{args.seed}"
    spec = GenerationSpec("payments_core", args.records, args.seed, args.scenario, args.pct, ANCHOR)
    print(name, write(args.out, name, spec, args.format)["expected"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
