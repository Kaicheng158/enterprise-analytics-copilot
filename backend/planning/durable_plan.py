"""Version-discriminated Plan parsing shared by the durable store."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, TypeAdapter

from .dataset_models import DatasetPlan
from .models import Plan


DurablePlan = Annotated[Plan | DatasetPlan, Field(discriminator="version")]
_DURABLE_PLAN_ADAPTER = TypeAdapter(DurablePlan)


def parse_durable_plan_json(payload: str) -> DurablePlan:
    return _DURABLE_PLAN_ADAPTER.validate_json(payload, strict=True)


__all__ = ["DurablePlan", "parse_durable_plan_json"]
