"""Explicit local migration command: python -m scripts.migrate.

No startup hook, data ingestion or rollback/drop command. Credentials stay in .env.
"""
import hashlib
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def migration_files(directory):
    files = sorted(Path(directory).glob('*.sql'))
    expected = [f'{i:03d}' for i in range(1, len(files) + 1)]
    if not files or [p.name.split('_', 1)[0] for p in files] != expected:
        raise ValueError('Migrations must have contiguous unique numbered prefixes')
    return [(p.name, p.read_text(), hashlib.sha256(p.read_bytes()).hexdigest()) for p in files]


def apply_migrations(connection, directory=ROOT / 'migrations'):
    migrations = migration_files(directory)
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        connection.execute('SELECT pg_advisory_xact_lock(732032)')
        connection.execute('CREATE SCHEMA IF NOT EXISTS app_migrations')
        connection.execute('REVOKE ALL ON SCHEMA app_migrations FROM PUBLIC')
        connection.execute("""CREATE TABLE IF NOT EXISTS app_migrations.applied (
            name text PRIMARY KEY, sha256 text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now())""")
        applied = dict(connection.execute('SELECT name, sha256 FROM app_migrations.applied').fetchall())
        if list(sorted(applied)) != [name for name, _, _ in migrations[:len(applied)]]:
            raise ValueError('Applied migration history is not a prefix of local migrations')
        for name, sql, digest in migrations:
            if name in applied:
                if applied[name] != digest:
                    raise ValueError('Applied migration checksum mismatch: ' + name)
            else:
                connection.execute(sql)
                connection.execute('INSERT INTO app_migrations.applied(name,sha256) VALUES (%s,%s)', (name,digest))
    return [name for name, _, _ in migrations if name not in applied]


def connect_kwargs():
    load_dotenv(ROOT / '.env', override=False)
    return dict(host=os.environ['POSTGRES_HOST'], port=os.environ['POSTGRES_PORT'],
                dbname=os.environ['POSTGRES_DB'], user=os.environ['POSTGRES_USER'],
                password=os.environ['POSTGRES_PASSWORD'], connect_timeout=5)


if __name__ == '__main__':
    with psycopg.connect(**connect_kwargs(), autocommit=True) as connection:
        print({'applied': apply_migrations(connection)})
