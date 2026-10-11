"""Write generated datasets to disk as JSONL or CSV plus a ground-truth manifest."""

import csv
import json
from pathlib import Path
from typing import Any

from app.datagen.generator import GenerationSpec, Oracle, iter_events, record_outcome
from app.quality.contract import CONTRACT_FIELDS, CORRUPT_MARKER

# Labels kept inline in the manifest; the full list always goes to the .labels.jsonl file.
MAX_INLINE_LABELS = 20_000


def write_dataset(spec: GenerationSpec, path: Path, fmt: str = "jsonl") -> dict[str, Any]:
    if fmt not in {"jsonl", "csv"}:
        raise ValueError("fmt must be jsonl or csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    labels_path = path.with_suffix(".labels.jsonl")
    oracle = Oracle()
    inline: list[dict[str, Any]] = []

    with path.open("w", newline="", encoding="utf-8") as out, labels_path.open("w") as lab:
        writer = None
        if fmt == "csv":
            columns = [*CONTRACT_FIELDS, "amount_minor", "promo_code"]
            writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
        for payload, label in iter_events(spec):
            record_outcome(oracle, label)
            if label:
                entry = {
                    "row": label.row_number,
                    "event_id": label.event_id,
                    "fault": label.fault,
                    "expected": label.expected,
                }
                lab.write(json.dumps(entry) + "\n")
                if len(inline) < MAX_INLINE_LABELS:
                    inline.append(entry)
            if writer is None:
                if CORRUPT_MARKER in payload:
                    out.write(payload[CORRUPT_MARKER] + "\n")
                else:
                    out.write(json.dumps(payload) + "\n")
            elif CORRUPT_MARKER in payload:
                # A truncated line: too few columns, which the CSV reader flags as corrupt.
                out.write(payload[CORRUPT_MARKER].replace('"', "").replace(",", ";")[:40] + "\n")
            else:
                writer.writerow({k: ("" if v is None else v) for k, v in payload.items()})

    return {
        "format": fmt,
        "params": spec.as_params(),
        "expected": oracle.summary(),
        "labels": inline,
        "labels_truncated": oracle.summary()["received"] - oracle.expected["accepted"]
        > len(inline),
    }
