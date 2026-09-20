"""One-off Phase 2.8 measurement; no production prompt selector or version registry."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

from backend.config import load_settings
from backend.examples import example_messages
from backend.llm import get_provider
from backend.output import AnalyticsAnswer
from backend.prompts import OUTPUT_CONTRACT, SYSTEM_PROMPT_V1, build_messages
from eval.run_uncertainty import collect


def compact_candidate(user_message):
    messages = build_messages(user_message)
    before, schema = messages[0]['content'].rsplit('\nJSON schema: ', 1)
    messages[0]['content'] = before + '\nJSON schema: ' + json.dumps(
        json.loads(schema), separators=(',', ':'))
    return messages


def measurement_prefixes():
    # A constant measurement instruction preserves a valid response even when
    # analytical prompt sections are omitted. Its tokens are measured separately.
    control = 'Synthetic token measurement only. Return JSON with summary as a string and facts, interpretation, limitations as arrays of strings. Keep all fields brief.'
    system = control + '\n\n' + SYSTEM_PROMPT_V1
    contract = system + '\n\n' + OUTPUT_CONTRACT
    schema = contract + '\nJSON schema: ' + json.dumps(AnalyticsAnswer.model_json_schema())
    user = 'Synthetic measurement.'
    def pair(text):
        return [{'role':'system','content':text}, {'role':'user','content':user}]
    full = [*pair(schema)[:1], *example_messages(), pair(schema)[-1]]
    return [
        ('control', pair(control)),
        ('system', pair(system)),
        ('contract', pair(contract)),
        ('schema', pair(schema)),
        ('examples', full),
        ('user_increment', [*full[:-1], {'role':'user','content':user + ' Fictional comparable order counts are 20 then 25; explain the change without guessing its cause.'}]),
    ]


def run_comparison(variant):
    cases = []
    for group in ['uncertainty', 'injection']:
        for case in json.loads(Path(__file__).with_name(group + '_cases.json').read_text()):
            cases.append({**case, 'id':group + '/' + case['id']})
    builder = build_messages if variant == 'baseline' else compact_candidate
    with patch('backend.llm.build_messages', builder), patch('eval.run_uncertainty.build_messages', builder):
        report = collect(get_provider(), cases)
    settings = load_settings()
    report['comparison_variant'] = variant
    report['fixed_prefix_snapshot'] = builder('')[:-1]
    report['runtime_settings'] = settings.model_dump(exclude={'api_key'})
    return report


def run_attribution():
    records = []
    for name, messages in measurement_prefixes():
        builder = lambda _, messages=messages: messages
        with patch('backend.llm.build_messages', builder), patch('eval.run_uncertainty.build_messages', builder):
            report = collect(get_provider(), [{'id':name, 'input':'Synthetic measurement.'}])
        records.append(report['records'][0])
    return {'method':'Cumulative real prompt-token deltas, including join/role overhead; control tokens excluded from attributed groups. Not exact independent tokenizer counts. Diagnostic outputs are not quality-eval verdicts.', 'records':records}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Synthetic paid Phase 2.8 diagnostics or 12-case comparison.')
    parser.add_argument('--variant', choices=['attribution','baseline','candidate'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Preserve previous evidence; choose a new path')
    report = run_attribution() if args.variant == 'attribution' else run_comparison(args.variant)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('Saved ' + args.variant + '; review required.', flush=True)
