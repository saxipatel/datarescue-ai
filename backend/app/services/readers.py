"""Streaming readers for uploaded/generated batch files.

Each reader yields one dict per input row. Rows that cannot be parsed are yielded as
{CORRUPT_MARKER: text} so the engine quarantines them instead of failing the batch.
"""

import csv
import io
import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.errors import ValidationFailed
from app.quality.contract import CORRUPT_MARKER, REQUIRED_FIELDS

csv.field_size_limit(1024 * 1024)
MAX_LINE_CHARS = 64 * 1024


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            if len(text) > MAX_LINE_CHARS:
                yield {CORRUPT_MARKER: text[:500]}
                continue
            try:
                obj = json.loads(text, parse_float=Decimal)
            except json.JSONDecodeError:
                yield {CORRUPT_MARKER: text[:500]}
                continue
            yield obj if isinstance(obj, dict) else {CORRUPT_MARKER: text[:500]}


def iter_csv(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        width = len(reader.fieldnames or [])
        for row in reader:
            # DictReader puts overflow under None and fills missing columns with None.
            values = [v for k, v in row.items() if k is not None]
            if None in row or sum(v is None for v in values) > 0 or len(values) != width:
                raw = ",".join(str(v) for v in row.values() if v is not None)
                yield {CORRUPT_MARKER: raw[:500]}
                continue
            # Empty CSV cells mean "absent"; drop optional columns nobody filled in.
            yield {k: v for k, v in row.items() if k is not None and v != ""} | {
                k: None for k in REQUIRED_FIELDS if row.get(k) == ""
            }


def iter_rows(path: Path, fmt: str) -> Iterator[dict[str, Any]]:
    if fmt == "jsonl":
        return iter_jsonl(path)
    if fmt == "csv":
        return iter_csv(path)
    raise ValueError(f"Unsupported format {fmt}")


def detect_format(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith((".jsonl", ".ndjson")):
        return "jsonl"
    if lower.endswith(".csv"):
        return "csv"
    raise ValidationFailed(
        "Unsupported file type; upload .csv or .jsonl", details={"filename": filename}
    )


def preflight(path: Path, fmt: str) -> dict[str, Any]:
    """Cheap synchronous checks before queueing: encoding and CSV headers."""
    with path.open("rb") as handle:
        head = handle.read(64 * 1024)
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # The sample may cut a multi-byte character; only fail if it's not at the tail.
        if exc.start < len(head) - 4:
            raise ValidationFailed(
                "File is not valid UTF-8", details={"byte_offset": exc.start}
            ) from exc
        text = head[: exc.start].decode("utf-8")
    if not text.strip():
        raise ValidationFailed("File is empty")
    if fmt == "csv":
        header = next(csv.reader(io.StringIO(text)), [])
        missing = [f for f in REQUIRED_FIELDS if f not in header]
        if missing:
            raise ValidationFailed(
                "CSV header is missing required columns",
                details={"missing_columns": missing, "header": header[:30]},
            )
        return {"header": header}
    return {}
