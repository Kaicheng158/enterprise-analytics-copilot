"""Read-only PostgreSQL sales summary tool with fixed query shape."""

from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
import os
from pathlib import Path
from typing import Callable, ContextManager, Literal

import psycopg
from dotenv import load_dotenv
from pydantic import Field

from .models import StrictModel
from .registry import ToolRegistryError

DATASET_VERSION = "synthetic-sales-db-v1"
RELATION = "analytics.sales_orders"

# Identifiers and shape are constant. Every runtime value is a bound parameter.
SUMMARY_SQL = """
SELECT count(*)::integer,
       COALESCE(sum(amount_cents), 0)::bigint,
       current_setting('transaction_read_only')
FROM analytics.sales_orders
WHERE dataset_version = %s
  AND period = %s
  AND (%s = 'all' OR region = %s)
  AND currency = %s
"""


class PostgresSalesSummaryInput(StrictModel):
    period: Literal["2026-Q1", "2026-Q2", "2026-Q3"]
    region: Literal["all", "north", "south"] = "all"


class SalesDataSource(StrictModel):
    kind: Literal["postgresql"] = "postgresql"
    relation: Literal["analytics.sales_orders"] = RELATION
    dataset_version: Literal["synthetic-sales-db-v1"] = DATASET_VERSION
    read_only: Literal[True] = True


class PostgresSalesSummaryOutput(StrictModel):
    period: Literal["2026-Q1", "2026-Q2", "2026-Q3"]
    region: Literal["all", "north", "south"]
    currency: Literal["USD"] = "USD"
    order_count: int = Field(ge=0)
    total_sales: Decimal = Field(ge=0, decimal_places=2)
    # Undefined when there are no matching orders; zero only when matched orders sum to zero.
    average_order_value: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    data_source: SalesDataSource


@contextmanager
def open_sales_connection():
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    connection = psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=5,
        options="-c statement_timeout=5000",
    )
    try:
        yield connection
    finally:
        connection.close()


ConnectionFactory = Callable[[], ContextManager[psycopg.Connection]]


def make_get_postgres_sales_summary(connection_factory: ConnectionFactory):
    def get_postgres_sales_summary(
        arguments: PostgresSalesSummaryInput,
    ) -> PostgresSalesSummaryOutput:
        try:
            with connection_factory() as connection:
                with connection.transaction():
                    connection.execute("SET TRANSACTION READ ONLY")
                    connection.execute("SET LOCAL statement_timeout = '3s'")
                    row = connection.execute(
                        SUMMARY_SQL,
                        (
                            DATASET_VERSION,
                            arguments.period,
                            arguments.region,
                            arguments.region,
                            "USD",
                        ),
                    ).fetchone()
            if row is None or len(row) != 3 or row[2] != "on":
                raise ToolRegistryError("tool_database_error")
            count, total_cents, _ = row
            if type(count) is not int or type(total_cents) is not int:
                raise ToolRegistryError("tool_database_error")
        except ToolRegistryError:
            raise
        except (psycopg.Error, KeyError, OSError, ValueError):
            raise ToolRegistryError("tool_database_error") from None

        total = (Decimal(total_cents) / Decimal(100)).quantize(Decimal("0.01"))
        average = None
        if count:
            average = (total / Decimal(count)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        return PostgresSalesSummaryOutput(
            period=arguments.period,
            region=arguments.region,
            order_count=count,
            total_sales=total,
            average_order_value=average,
            data_source=SalesDataSource(),
        )

    return get_postgres_sales_summary
