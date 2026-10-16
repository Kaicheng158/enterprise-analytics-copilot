"""Explicit idempotent seed command for the non-sensitive synthetic sales fixture."""

from pathlib import Path

import psycopg

from scripts.migrate import ROOT, connect_kwargs

SEED_DIRECTORY = ROOT / "seeds"


def seed_files(directory=SEED_DIRECTORY):
    files = sorted(Path(directory).glob("*.sql"))
    expected = [f"{index:03d}" for index in range(1, len(files) + 1)]
    if not files or [path.name.split("_", 1)[0] for path in files] != expected:
        raise ValueError("Seed files must have contiguous unique numbered prefixes")
    return files


def seed_sales(connection, directory=SEED_DIRECTORY) -> int:
    inserted = 0
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL statement_timeout = '10s'")
        connection.execute("SELECT pg_advisory_xact_lock(732042)")
        for path in seed_files(directory):
            row = connection.execute(path.read_text()).fetchone()
            if row is None or type(row[0]) is not int or row[0] < 0:
                raise ValueError("Seed did not return an inserted row count")
            inserted += row[0]
    return inserted


if __name__ == "__main__":
    with psycopg.connect(**connect_kwargs(), autocommit=True) as database:
        print({"inserted": seed_sales(database)})
