# Phase 4.2 — PostgreSQL read-only data tools

Phase 4.2 keeps the Phase 4.1 in-memory `get_sales_summary` unchanged and registers a separate `get_postgres_sales_summary`. The new tool reads stored, non-sensitive synthetic data. It does not accept SQL, table names, columns, predicates, credentials or an authorization principal from its arguments.

## Storage and seed

Migration `004_sales_tools.sql` creates schema `analytics`, revokes public access, and creates `analytics.sales_orders`:

| Column | Type | Purpose |
|---|---|---|
| `order_id` | UUID primary key | Stable seed identity and deduplication |
| `dataset_version` | text | Restricted to `synthetic-sales-db-v1` |
| `period` | text | Quarter identifier constrained to `YYYY-Q1..Q4` |
| `region` | text | Restricted to `north` or `south` |
| `currency` | text | Restricted to `USD` |
| `amount_cents` | bigint | Exact nonnegative monetary value in cents |
| `occurred_on` | date | Synthetic order date |
| `created_at` | timestamptz | Database creation time |

The `(dataset_version, period, region, currency)` index supports the fixed summary filter. `seeds/001_synthetic_sales.sql` inserts eight stable Q1/Q2 rows with `ON CONFLICT (order_id) DO NOTHING`. `python -m scripts.seed_sales` is an explicit operator command; rerunning it inserts zero duplicates. The tool never invokes migrations or seed code.

## Execution and SQL boundary

The strict input permits only `2026-Q1`, `2026-Q2`, `2026-Q3` and `all`, `north`, `south`. Q3 intentionally has no seeded rows and verifies the no-match contract. Output contains the requested dimensions, `order_count`, Decimal-derived `total_sales` and `average_order_value`, `USD`, and server-created source metadata. When no records match, `order_count=0`, `total_sales="0.00"`, and `average_order_value=null` because an average over an empty set is undefined. When one or more records match but their total is zero, AOV is the defined value `"0.00"`.

The SQL statement and identifiers are constant in source. Dataset version, period, region and currency are bound with psycopg parameters. Before querying, the tool starts an explicit transaction, executes `SET TRANSACTION READ ONLY`, applies a three-second local statement timeout, and verifies PostgreSQL reports `transaction_read_only=on`. The registry resolves the server-owned `analytics-copilot` grant before input parsing or database connection. No arbitrary SQL path exists.

Database connection/provider details and exception text are never returned or logged. PostgreSQL failures become the fixed `tool_database_error` result. Tool logs retain only safe execution metadata inherited from Phase 4.1.

## Scope and next steps

There is still no public Tool API, LLM tool selection or agent loop. The current local application database role owns its objects; defense in depth currently comes from the tool's read-only transaction, fixed query, strict schema and registry authorization. A dedicated least-privilege database login is required before production/multiuser deployment.

- 4.3: bounded provider-independent orchestration and tool-call parsing; no automatic SQL.
- 4.4: internal LLM tool selection behind a feature flag, with injection/malformed-call/evaluation coverage.
- 4.5: separate tool-enabled API, release gate, Docker verification and human-in-the-loop policy.
