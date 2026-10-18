# Phase 4.5 FAIL independent review package

This package is a compact, self-contained snapshot for independent review of the Phase 4.5 FAIL. It does not modify or supersede the frozen prompt, evaluator, rubric, gate, cases, evidence, or historical verdicts. No paid API run was performed to create it.

## Start here

1. `acceptance/final-acceptance.md` — final acceptance report.
2. `frozen/` — byte-for-byte copies of the suite, rubric/policy, release gate, and freeze identity.
3. `failures/repeated-failures.json` — complete structured evidence for the three repeated failures across all three rounds.
4. `failures/repeated-failures.md` — human-readable rendering of the same nine records.
5. `evaluator/` — evaluator and gate implementation snapshots plus non-authoritative classification notes.
6. `tool-catalog/tool-catalog.json` — full exported definitions for the two authorized sales tools; source snapshots sit beside it.
7. `checksums.sha256` and `manifest.json` — integrity and provenance.

## Security and privacy

The package contains only synthetic, non-sensitive evaluation data and already-redacted provider telemetry. It contains no `.env`, credential value, authorization header, database backup, cache, log, or private diagnostics file. Provider request IDs, tool call IDs, and correlation IDs are retained as evidence identifiers; they are not credentials.

## Export

The sibling ZIP file `phase4.5-fail-review-package.zip` is the exportable copy. Verify extracted files with `checksums.sha256`.
