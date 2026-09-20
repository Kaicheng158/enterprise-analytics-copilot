"""Server-owned synthetic demonstrations; no provider-specific configuration."""
from backend.output import AnalyticsAnswer

# Immutable strings and tuples prevent request assembly from mutating examples.
FEW_SHOT_EXAMPLES = (
    (
        'Synthetic test data: a fictional shop reports 100 orders last month and 80 this month, '
        'using the same counting rules. The owner suspects a price increase caused the decline. '
        'What can we conclude?',
        AnalyticsAnswer(
            summary='Reported orders fell 20%; the proposed price explanation is unverified.',
            facts=[
                'The supplied synthetic counts are 100 and 80 orders under the same counting rules; they are not independently verified.',
                'Change = 80 - 100 = -20 orders; percentage change = -20 / 100 = -20%.',
            ],
            interpretation=[
                'A price increase is the owner\'s unverified hypothesis, not an established cause. Order counts alone cannot establish it.',
            ],
            limitations=[
                'No price history or evidence linking price changes to orders was supplied. Request dated prices, traffic and conversion data to investigate; these alone may not establish causality.',
            ],
        ).model_dump_json(),
    ),
    (
        'Synthetic scenario: a fictional subscription service asks why churn increased last month. '
        'No churn counts, rates, definitions or comparison data are supplied. Explain the cause.',
        AnalyticsAnswer(
            summary='Whether churn increased, its magnitude and its cause are unknown from the supplied information.',
            facts=[],
            interpretation=[
                'The question presupposes an increase, but no measurements support that premise. No cause can be inferred.',
            ],
            limitations=[
                'Provide the churn definition, periods, churned and eligible customer counts for both periods to establish whether an increase occurred.',
                'Only after confirming the change, examine relevant customer or cancellation evidence to investigate causes; none is available here.',
            ],
        ).model_dump_json(),
    ),
)


def example_messages() -> list[dict[str, str]]:
    return [
        message
        for user, answer in FEW_SHOT_EXAMPLES
        for message in (
            {'role': 'user', 'content': user},
            {'role': 'assistant', 'content': answer},
        )
    ]
