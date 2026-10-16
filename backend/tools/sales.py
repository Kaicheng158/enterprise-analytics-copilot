"""Deterministic synthetic sales analytics tool; no database or network access."""

from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import Field

from .models import StrictModel

DATA_VERSION = "synthetic-sales-v1"

# Integer cents make the fixture and aggregation exact and reproducible.
SALES = (
    ("2026-Q1", "north", 12_000),
    ("2026-Q1", "north", 8_000),
    ("2026-Q1", "south", 15_000),
    ("2026-Q1", "south", 5_000),
    ("2026-Q2", "north", 18_000),
    ("2026-Q2", "north", 12_000),
    ("2026-Q2", "south", 16_000),
    ("2026-Q2", "south", 14_000),
)


class SalesSummaryInput(StrictModel):
    period: Literal["2026-Q1", "2026-Q2"]
    region: Literal["all", "north", "south"] = "all"


class SalesSummaryOutput(StrictModel):
    period: Literal["2026-Q1", "2026-Q2"]
    region: Literal["all", "north", "south"]
    currency: Literal["USD"] = "USD"
    order_count: int = Field(ge=0)
    total_sales: Decimal = Field(ge=0, decimal_places=2)
    average_order_value: Decimal = Field(ge=0, decimal_places=2)
    data_version: Literal["synthetic-sales-v1"] = DATA_VERSION


def get_sales_summary(arguments: SalesSummaryInput) -> SalesSummaryOutput:
    selected = [
        cents
        for period, region, cents in SALES
        if period == arguments.period and (arguments.region == "all" or region == arguments.region)
    ]
    count = len(selected)
    total = (Decimal(sum(selected)) / Decimal(100)).quantize(Decimal("0.01"))
    average = (
        (total / Decimal(count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if count
        else Decimal("0.00")
    )
    return SalesSummaryOutput(
        period=arguments.period,
        region=arguments.region,
        order_count=count,
        total_sales=total,
        average_order_value=average,
    )
