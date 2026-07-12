# MDF Parsing Guide Review Checkpoint

This checkpoint is a safety and reproducibility invariant for local web runs
that include Stage 2.

## Invariant

Stage 2 Pass 2 must consume an explicitly approved, schema-valid immutable
snapshot through a server-minted `ApprovedParseRules` capability. Generated,
draft, external, cached, bare-path, or CLI-trusted rules are never implicitly
approved for a web run.

## State transition

```text
discovering_parse_rules
  → write generated snapshot atomically
  → validate DictionaryMarkerCheatsheet
  → awaiting_parse_rules_review
  → user edits/saves drafts as needed
  → explicit Approve and continue
  → validate again
  → write and fsync immutable approved/<sha256>.json
  → transactionally record review/version/digest and enqueue Pass 2
  → update compatibility parse-rules.json
  → mint run-bound ApprovedParseRules
  → worker reads, hashes, validates, and consumes those exact bytes
```

Browser disconnect, server shutdown, application restart, time passage, or a
successful draft save cannot trigger approval.

## Review data

The editor covers the complete current schema:

```text
dictionary_name: string
markers[]:
  marker: string
  description: string
rules[]: string
abbreviations: mapping[string, string]
```

UI validation adds non-empty, normalized marker names and duplicate detection.
Pydantic remains the authoritative server-side schema validator.

A shared semantic validator canonicalizes marker codes and rejects empty codes,
duplicates, invalid syntax, and normalized collisions such as `lx` versus
`\\lx`. The editor, approval handler, and worker startup all call it.

## Regeneration

Regeneration is explicit and warns that it makes another LLM call. A user may
change representative pages. Each generation is retained or versioned with its
sample page identifiers and usage so the review can reset to a known version.

Regeneration invalidates any prior approval and returns the run to review. It
must not overwrite the approved rules used by a completed Pass 2.

## Approval concurrency

Approval is idempotent for the same content hash. Simultaneous or repeated
requests cannot launch duplicate workers. A changed draft after approval does
not alter the queued/running Pass 2; editing requires an explicit invalidate and
rerun flow.

The capability is never accepted from form data. It is minted server-side from
the committed review row. The worker verifies run ID, review version, digest,
schema, and immutable snapshot immediately before consuming the already-loaded
rules. Crash injection covers each filesystem/database boundary; startup
reconciles orphaned snapshots and incomplete enqueue records.

## Existing MDF parsing guide files

When the user selects an existing MDF parsing guide JSON file, copy it into the
run's generated/review area, validate it, and enter the same review state. Do
not run Pass 2 directly from an external mutable path. Internal compatibility
filenames and state values continue to use `parse-rules`/`parse_rules`.

## Failure and recovery

- Invalid generated rules: show the error and allow regenerate or edit.
- Invalid draft: remain awaiting review and preserve the last valid draft.
- Worker failure after approval: retain approved rules and offer resume of Pass
  2 without repeating discovery.
- App restart: restore awaiting-review state from SQLite and artifact hashes.
- Missing/tampered approved file: block resume and require review/reapproval.
