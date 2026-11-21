"""Provider-independent deterministic synthesizer used by PV2-04e tests."""

from __future__ import annotations

from .models import (
    ClaimValueReference,
    SynthesisDraftV1,
    TrustedClaimV1,
    TrustedSynthesisRequestV1,
)


class FakeResultSynthesizer:
    """Produce bounded prose from verified calculation references only."""

    def synthesize(self, request, calculations):
        available = [item for item in calculations if item.value is not None]
        if not available:
            return SynthesisDraftV1(
                summary=(
                    "No verified numeric result is available for this request."
                    if request.language == "en"
                    else "当前请求没有可用的已验证数值结果。"
                ),
                claims=(
                    TrustedClaimV1(
                        claim_id="action_1",
                        kind="proposed_action",
                        text=(
                            "Review the reported data-quality limitations before continuing."
                            if request.language == "en"
                            else "继续前请先检查已报告的数据质量限制。"
                        ),
                    ),
                ),
                limitations=("verified_numeric_result_unavailable",),
            )

        claims = []
        for index, calculation in enumerate(available[:16], 1):
            if request.language == "zh":
                text = f"{calculation.label} 为 {calculation.value.value} {calculation.value.unit}。"
            else:
                text = f"{calculation.label} is {calculation.value.value} {calculation.value.unit}."
            claims.append(
                TrustedClaimV1(
                    claim_id=f"fact_{index}",
                    kind="fact",
                    text=text,
                    value_references=(
                        ClaimValueReference(
                            calculation_id=calculation.calculation_id,
                            value=calculation.value.value,
                            unit=calculation.value.unit,
                        ),
                    ),
                )
            )
        limitations = tuple(
            dict.fromkeys(
                code
                for calculation in calculations
                for code in calculation.limitation_codes
            )
        )
        summary = (
            "Verified calculation results from the authorized dataset revision."
            if request.language == "en"
            else "已从授权数据修订中验证计算结果。"
        )
        return SynthesisDraftV1(
            summary=summary,
            claims=tuple(claims),
            limitations=limitations,
        )


__all__ = ["FakeResultSynthesizer"]
