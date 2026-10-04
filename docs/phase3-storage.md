# Phase 3.2 — PostgreSQL + pgvector storage foundation

## Verified deployment

PostgreSQL 17.11, pgvector 0.8.7. Compose pins both a release tag and immutable digest:

`pgvector/pgvector:0.8.7-pg17-trixie@sha256:7a7e9f22015b67edb4bef5c59daeebcd7e74bfa570df6ce60ae01237c8648a84`

The existing postgres_data volume, credentials, localhost ports and API service are retained. No PostgreSQL major upgrade. The first bookworm variant exposed a libc collation mismatch; final trixie matches the original environment (stored/actual collation 2.41/2.41). No ALTER DATABASE REFRESH COLLATION VERSION was used to conceal it. Reference: [official pgvector images](https://github.com/pgvector/pgvector#docker).

## Backup and restore evidence

Before changing the service, saved a custom-format database dump, globals (including sensitive role credentials), plain logical dump and checksums under `backups/phase32-20261004T114221Z/`. Directory/file permissions are private, and backups/ is Git-ignored and excluded by the Docker build allowlist. Do not upload these backups.

Restored the custom dump with pg_restore --exit-on-error --no-owner --no-privileges into an independent PG17 container using the exact prior image ID, no host port and no network. Compared normalized full logical dumps: identical (random pg_dump restrict markers excluded). Temporary container and its anonymous volume were removed. Globals are backed up but role/password restoration was not exercised. Source database had zero user tables, so this proves the current database restore path, not large-corpus or populated business-data recovery.

Before applying migrations, the database after image replacement matched the old logical dump (ignoring dump tool version comments). Existing volume was not deleted. /health and /health/db return 200 and SELECT 1. Final RAG tables contain zero rows.

For another backup, use pg_dump -Fc through `docker compose exec -T postgres` with container POSTGRES_USER/POSTGRES_DB, redirect to a private ignored path; separately save pg_dumpall --globals-only. Never print globals or environment values. A restore drill must create a new disposable container/database, restore with pg_restore --exit-on-error, compare schema/data and then remove only that drill container/volume. Restoring over the live database is not a migration rollback command. A future post-pgvector restore needs the pinned pgvector-enabled image. Retain the current backup until later recovery/retention policy is established.

## Versioned migrations

Run from the repository using the project venv:

```sh
analytics-agent-env/bin/python -m scripts.migrate
```

The runner reads existing .env connection values without printing secrets. It uses one transaction, bounded lock/statement timeouts and a transaction advisory lock. app_migrations.applied records filename, exact SHA-256 and timestamp. Repeat execution skips matching migrations; edits, gaps/unknown applied history fail. SQL failure rolls back the batch, including ledger changes. Do not edit applied SQL; append the next numbered migration. There is no automatic startup migration or destructive down migration.

- 001_vector.sql: enable vector 0.8.7 in public; reject unexpected installed versions.
- 002_rag_storage.sql: create rag schema, revoke public schema privileges and create four empty tables.
- rag.documents: tenant/document/revision identity, source, media type, content hash, JSON-object metadata, status and created time.
- rag.document_chunks: tenant-scoped document FK, stable chunk ID, ordinal, text, locator, chunker version/hash and metadata; cascade document deletion.
- rag.embedding_profiles: provider/model/revision/preprocessing/dimensions/distance identity, no selected provider or rows.
- rag.chunk_embeddings: vector column plus profile/dimension FK and tenant/chunk FK; vector length must match declared/profile dimensions and vectors must be nonzero. Cascade chunk deletion.

The vector column intentionally has no fixed typmod: no production model/dimension has been chosen. Per-row checks and a composite FK enforce dimensions instead. Future indexing must explicitly select a profile/dimension and compatible metric. No ANN index, retrieval SQL API, embedding adapter or ingestion is implemented. Profile preprocessing identity must account for normalization and query/document modes. Future migrations can strengthen version/readiness/access policies as their implementations are introduced.

Composite tenant FKs prevent cross-tenant relationships, but they do NOT provide read authorization or RLS. Current database owner is privileged; do not expose these tables through a retrieval API until least-privilege roles and authorization enforcement are implemented. No claim of enterprise tenant security is made here.

## Tests and acceptance

```sh
analytics-agent-env/bin/python -m unittest discover -s tests -q
analytics-agent-env/bin/python -m unittest discover -s tests/db -v
```

89 offline tests pass, including all 87 pre-existing tests. Four explicit database tests pass in a uniquely named eac_test_* database, automatically dropped: repeat migration/version verification, checksum tampering rejection, failed migration transaction rollback, vector storage/comparison and FK/dimension/zero-vector/cascade constraints.

Synthetic vectors are used only inside a rollback transaction in that disposable database: L2 distance [1,0,0] to itself = 0; to [0,1,0] = sqrt(2). No production test vectors/documents remain. [Machine-readable verification](phase3-storage-verification.json) records backup hashes, image, migration hashes and health results.

analytics-v2 remains active at the same SHA. Published prompts, schema, gate, historical evaluation, generation config, requirements and /chat code are unchanged. No paid API calls, ingestion, retrieval or generation took place. No commit/push.

Next: Phase 3.3 bounded local text/Markdown ingestion and deterministic chunking with provenance, idempotent revisions, offline fixtures and transactional persistence; no embedding or RAG generation yet.
