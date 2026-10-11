"""Redaction applied before evidence leaves the database boundary (UI samples, LLM prompts)."""

from typing import Any

from app.quality.contract import CORRUPT_MARKER

_MASKED_FIELDS = {"customer_id"}
_MAX_TEXT = 200


def mask(value: str) -> str:
    if len(value) <= 3:
        return "***"
    return value[:1] + "***" + value[-2:]


def redact_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key == CORRUPT_MARKER:
            out[key] = "<unparseable text omitted>"
        elif key in _MASKED_FIELDS and isinstance(value, str):
            out[key] = mask(value)
        elif isinstance(value, str) and len(value) > _MAX_TEXT:
            out[key] = value[:_MAX_TEXT] + "…"
        else:
            out[key] = value
    return out


def strip_instructions(text: str, limit: int = _MAX_TEXT) -> str:
    """Neutralize free text that could be read as instructions by an LLM.

    Incident evidence is data. We truncate it and remove characters commonly used to
    fake prompt structure; the prompt also tells the model to treat it as untrusted.
    """
    cleaned = text.replace("```", "'''").replace("<", "\u2039").replace(">", "\u203a")
    return cleaned[:limit]
