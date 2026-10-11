# ADR 0003: Deterministic diagnosis first, LLM enrichment optional

**Status:** accepted

## Context

Incidents need an explanation and a suggested next step. LLMs write fluent explanations
but are non-deterministic, cost money, need network access and credentials, and can be
steered by content inside the data they read. Recovery actions change business data, so
whatever recommends them has to be predictable and testable.

## Decision

- A rules-based provider always runs. It maps failure signatures (rule failures, error-code
  breakdown, schema versions, duplicate origins, sequence gaps, freshness checks) to ranked
  causes. Each cause has a heuristic confidence label (low, medium or high, explicitly not
  a probability) and the IDs of supporting and contradicting evidence. Recommended actions
  come from a fixed allowlist.
- An optional enrichment step (Anthropic SDK, `claude-opus-5-5` by default, switched on
  by `LLM_ENABLED` and `LLM_API_KEY`) receives a redacted, size-bounded summary and
  returns a schema-validated short explanation. Unknown evidence citations invalidate the
  output, and suggested actions are filtered to the allowlist. The result is stored as
  `ai_enrichment` with `unverified: true` and shown with that label.
- Neither provider modifies data. Every action still needs a proposal, a dry run and an
  approval.

## Consequences

- The core product works offline at no cost, and diagnosis is unit-tested with fixed
  inputs.
- The deterministic rules only know the failure modes they were written for. An
  unmatched incident says so ("No known failure signature matched").
- The LLM path is covered by tests with stubbed completions (valid output, unknown
  citations, timeouts, refusals, invalid output, prompt-injection text). It was not run
  against a live API key during development.
