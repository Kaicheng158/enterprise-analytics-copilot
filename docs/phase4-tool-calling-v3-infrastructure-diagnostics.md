# Phase 4.5 Evaluator v3 infrastructure diagnostics

Status: infrastructure recovered; free preflight PASS; historical live gate remains FAIL.

## Preserved result

The executed v3 regression is unchanged. Its three rounds each failed the same
two PostgreSQL cases, producing six `tool_database_error` results. The recorded
gate verdict remains `FAIL`; this diagnostic does not re-score or replace it.

All v3 suite, evaluator, policy, gate, live raw/reviewed/decision evidence, and
summary files are protected by `historical-live-v3-protection.json`.

## Root cause

The failure was infrastructure availability, not tool selection, argument
validation, SQL arithmetic, or PostgreSQL data content.

- The three live evidence files were written at approximately 23:54 AEDT on
  2026-10-18. In all six failures, DeepSeek selected
  `get_postgres_sales_summary` with valid arguments and the tool attempted one
  execution.
- Docker Desktop and its daemon were unavailable when diagnostics began. The
  Compose API and PostgreSQL containers were stopped.
- PostgreSQL had no query activity corresponding to the live regression.
- After Docker Desktop was restarted, PostgreSQL reported an interrupted prior
  shutdown, performed automatic WAL recovery, and became ready without database
  initialization. This confirms the existing data directory was reused.
- The old live plan relied on a previously completed preflight. It did not run
  a fresh database check immediately before each paid production-database case,
  allowing provider calls to proceed after the infrastructure state changed.

The initiating event that stopped Docker Desktop cannot be recovered from the
available logs: there is no clean shutdown or crash entry at the regression
time. The supported conclusion is that the daemon/database were already
unavailable before the first live round, while the precise reason Docker
Desktop stopped remains unknown.

## Safe recovery

Docker Desktop was started, then the existing PostgreSQL and API containers
were started in place. No container, image, volume, database row, or evaluation
evidence was deleted. The named volume remains
`enterprise-analytics-copilot_postgres_data`.

Recovery verification:

- PostgreSQL container: running and healthy.
- API container: running and healthy.
- `GET /health`: HTTP 200, `{"status":"ok"}`.
- `GET /health/db`: HTTP 200 with `SELECT 1` result.
- Agent host connection: `SELECT 1` succeeded.
- `analytics.sales_orders`: present with 8 rows for
  `synthetic-sales-db-v1` and total 100000 cents.
- Q2 North: 2 orders and 30000 cents.
- `get_postgres_sales_summary`: success, USD 300.00 total and USD 150.00 AOV.

## Connection and startup behavior

`open_sales_connection` creates one connection per tool call and closes it in a
`finally` block. Connection timeout is 5 seconds, session statement timeout is
5 seconds, and the tool transaction sets a 3-second local statement timeout and
read-only mode. Compose binds the API startup to the PostgreSQL health check via
`depends_on: condition: service_healthy`.

That startup dependency only governs Compose startup. It does not prove the
database remains reachable during a later host-side evaluation, so a separate
runtime guard is required.

## Runtime guard

`tool-calling-database-runtime-guard-v1` is separate from all frozen evaluation
artifacts. It checks, without a provider call:

1. agent-side database connection and `SELECT 1`;
2. presence of `analytics.sales_orders`;
3. presence of the expected synthetic dataset;
4. successful execution of the actual PostgreSQL sales tool.

The result contains only safe status metadata. It omits connection strings,
credentials, exception text, and row contents. Future paid runners must invoke
the guard at run start and immediately before every case using the server-owned
production exposure profile. A failed check raises
`database_runtime_unavailable` before provider work begins.

## New free preflight

Evidence: `eval/tools/v3/infrastructure-diagnostics-v3-001/`

- Paid API calls: 0.
- Docker daemon: reachable.
- PostgreSQL: running and healthy.
- Agent runtime database/tool check: healthy.
- Offline tests: 283 passed.
- Database tests: 44 passed.
- Frozen v3 verification: PASS.
- Historical live v3 protected files: 15 checked, 0 changed.
- Preflight verdict: PASS.

This preflight only clears the infrastructure blocker. It does not change the
historical gate verdict and does not authorize another paid regression.
