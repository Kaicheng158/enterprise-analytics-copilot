"""Fail-closed validation of answers, charts, reports and traceability."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from backend.planning.checkpoint_store import canonical_json_sha256

from .models import SynthesisFailure, TrustedSynthesisResponseV1


NUMBER = re.compile(r"(?<![A-Za-z0-9_-])-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
CAUSAL = re.compile(r"\b(caused?|causes?|because of|led to|drives?|resulted in)\b|导致|因为|驱动|造成", re.I)
SENSITIVE = re.compile(
    r"(?:/Users/|\\Users\\|storage_ref|api[_-]?key|authorization:\s*bearer|session[_-]?secret)",
    re.I,
)


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation:
        raise SynthesisFailure("synthesis_numeric_invalid") from None


class TrustedResultValidator:
    version = "trusted-result-validator-v1"

    def validate(self, response: TrustedSynthesisResponseV1) -> None:
        calculations = {item.calculation_id: item for item in response.calculations}
        if len(calculations) != len(response.calculations):
            raise SynthesisFailure("synthesis_calculation_duplicate")
        source_set = {
            canonical_json_sha256(item.model_dump(mode="json"))
            for item in response.trace.sources
        }
        for calculation in response.calculations:
            if not calculation.sources:
                raise SynthesisFailure("synthesis_source_missing")
            if any(
                canonical_json_sha256(item.model_dump(mode="json")) not in source_set
                for item in calculation.sources
            ):
                raise SynthesisFailure("synthesis_source_invalid")

        for claim in response.answer.claims:
            if claim.causal or CAUSAL.search(claim.text):
                raise SynthesisFailure("synthesis_unsupported_causality")
            referenced_values = []
            for reference in claim.value_references:
                calculation = calculations.get(reference.calculation_id)
                if calculation is None or calculation.value is None:
                    raise SynthesisFailure("synthesis_claim_reference_invalid")
                if (
                    _decimal(reference.value) != _decimal(calculation.value.value)
                    or reference.unit != calculation.value.unit
                ):
                    raise SynthesisFailure("synthesis_claim_value_mismatch")
                referenced_values.append(_decimal(reference.value))
            numbers = [_decimal(item) for item in NUMBER.findall(claim.text)]
            if any(number not in referenced_values for number in numbers):
                raise SynthesisFailure("synthesis_claim_number_ungrounded")

        for chart in response.charts:
            chart_source_set = {
                canonical_json_sha256(item.model_dump(mode="json")) for item in chart.sources
            }
            for series in chart.series:
                if len(series.calculation_ids) != len(series.values):
                    raise SynthesisFailure("synthesis_chart_reference_mismatch")
                units = set()
                for value, calculation_id in zip(series.values, series.calculation_ids, strict=True):
                    calculation = calculations.get(calculation_id)
                    if calculation is None or calculation.value is None:
                        raise SynthesisFailure("synthesis_chart_reference_invalid")
                    if _decimal(value) != _decimal(calculation.value.value):
                        raise SynthesisFailure("synthesis_chart_value_mismatch")
                    units.add(calculation.value.unit)
                    expected_sources = {
                        canonical_json_sha256(item.model_dump(mode="json"))
                        for item in calculation.sources
                    }
                    if not expected_sources.issubset(chart_source_set):
                        raise SynthesisFailure("synthesis_chart_source_missing")
                if len(units) != 1:
                    raise SynthesisFailure("synthesis_chart_unit_mismatch")

        allowed_values = {
            _decimal(item.value.value)
            for item in response.calculations
            if item.value is not None
        }
        if CAUSAL.search(response.answer.summary):
            raise SynthesisFailure("synthesis_unsupported_causality")
        if any(
            _decimal(item) not in allowed_values
            for item in NUMBER.findall(response.answer.summary)
        ):
            raise SynthesisFailure("synthesis_summary_number_ungrounded")
        if response.report is not None:
            if response.report.sources != response.trace.sources:
                raise SynthesisFailure("synthesis_report_source_mismatch")
            text = " ".join(
                [response.report.summary]
                + [section.body for section in response.report.sections]
            )
            if CAUSAL.search(text):
                raise SynthesisFailure("synthesis_unsupported_causality")
            if any(_decimal(item) not in allowed_values for item in NUMBER.findall(text)):
                raise SynthesisFailure("synthesis_report_number_ungrounded")

        serialized = response.model_dump_json()
        if SENSITIVE.search(serialized):
            raise SynthesisFailure("synthesis_sensitive_output")


__all__ = ["TrustedResultValidator"]
