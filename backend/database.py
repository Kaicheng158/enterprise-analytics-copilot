import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def check_database() -> int:
    with psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=5,
        options="-c statement_timeout=5000",
    ) as connection:
        return connection.execute("SELECT 1").fetchone()[0]
