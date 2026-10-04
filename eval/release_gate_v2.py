"""Offline-only, independently versioned release gate. Never calls a provider or publishes."""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from backend.output import AnalyticsAnswer
from eval.run_regression import digest, load_suite

POLICY_PATH = Path(__file__).with_name('release_gate_v2.json')
POLICY_SHA256 = '3a4d529b47e5bd433d5131247e901e5d3f0d5572d6dd03c0fa495b3f165a1f6e'
CONFIG_KEYS = {'provider', 'model', 'thinking', 'temperature', 'max_output_tokens',
               'timeout_seconds', 'max_retries', 'backoff_seconds'}
IDENTITY_KEYS = {'prompt_version', 'prompt_sha256', 'provider', 'model', 'runtime_config'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value):
    value = datetime.fromisoformat(value)
    require(value.tzinfo is not None, 'Timestamp must include timezone')
    return value


def load_policy():
    policy = json.loads(POLICY_PATH.read_text())
    require(digest(policy) == POLICY_SHA256, 'Gate policy changed; create a new version')
    require(load_suite()['suite_sha256'] == policy['suite_sha256'], 'Suite changed')
    return policy


def seal(value):
    return {**value, 'sha256': digest(value)}


def check_seal(value):
    require(value.get('sha256') == digest({k:v for k,v in value.items() if k != 'sha256'}),
            'Artifact checksum mismatch')


def register(identity):
    """Freeze future batch before collection; no environment/credential loading."""
    policy = load_policy()
    require(set(identity) == IDENTITY_KEYS, 'Exact nonsecret identity fields required')
    config = identity['runtime_config']
    require(set(config) == CONFIG_KEYS, 'Exact nonsecret runtime config required')
    require(config['provider'] == identity['provider'] and config['model'] == identity['model'],
            'Provider/model mismatch')
    require(all(isinstance(identity[k], str) and identity[k] for k in IDENTITY_KEYS - {'runtime_config'}),
            'Missing identity')
    from backend.config import LLMSettings
    validated = LLMSettings(**config).model_dump(exclude={'api_key'})
    require(config == validated, 'Invalid or implicit runtime settings')
    return seal({'gate_version': policy['version'], 'policy_sha256': POLICY_SHA256,
                 'suite_sha256': policy['suite_sha256'], 'rounds_required': policy['rounds_required'],
                 'registered_at': datetime.now(timezone.utc).isoformat(), **deepcopy(identity)})


def judgement(value):
    require(set(value) == {'verdict', 'rationale'}, 'Verdict and rationale required')
    require(value['verdict'] in {'pass', 'fail'}, 'Unresolved review cannot pass')
    require(isinstance(value['rationale'], str) and value['rationale'].strip(), 'Rationale required')
    return value['verdict'] == 'pass'


def quality_points(ratings, applicable):
    """N/A eligibility is fixed by policy, never selected after seeing an answer."""
    require(set(ratings) == set(applicable), 'All quality dimensions required')
    points, maximum, zeroes = 0, 0, []
    for dimension, enabled in applicable.items():
        entry = ratings[dimension]
        require(set(entry) == {'score', 'rationale'}, 'Score and rationale required')
        require(isinstance(entry['rationale'], str) and entry['rationale'].strip(), 'Score rationale required')
        score = entry['score']
        if not enabled:
            require(score is None, 'Predeclared N/A dimension must be null')
            continue
        require(type(score) is int and score in (0, 1, 2), 'Applicable score must be integer 0/1/2')
        points += score
        maximum += 2
        if score == 0:
            zeroes.append(dimension)
    return points, maximum, zeroes


def evaluate_batch(registration, raws, reviews):
    policy = load_policy()
    check_seal(registration)
    require(registration['policy_sha256'] == POLICY_SHA256 and registration['gate_version'] == policy['version'],
            'Registration targets another gate')
    require(registration['suite_sha256'] == policy['suite_sha256'], 'Registered suite mismatch')
    require(registration['rounds_required'] == policy['rounds_required'], 'Round count changed')
    require(timestamp(registration['registered_at']) >= timestamp(policy['preregistered_at']),
            'Registration precedes frozen gate policy')
    require(len(raws) == len(reviews) == policy['rounds_required'], 'Exactly three complete rounds required')
    # Also validates config without executing any provider request.
    register({k:registration[k] for k in IDENTITY_KEYS})
    suite = load_suite()
    expected_ids = set(policy['cases'])
    require(expected_ids == {c['id'] for c in suite['cases']}, 'Case coverage mismatch')
    results, run_ids, request_ids = [], set(), set()
    serious_by_case, serious_by_dimension = defaultdict(set), defaultdict(set)
    for raw, review in zip(raws, reviews):
        require(raw.get('evidence_sha256') == digest({k:v for k,v in raw.items() if k != 'evidence_sha256'}),
                'Raw evidence changed; do not use old reviewed reports')
        require(raw.get('mode') == 'live_provider', 'Release evidence must identify live provider mode')
        require(raw.get('collection_complete') is True and raw['suite'] == suite, 'Incomplete or altered suite')
        require(timestamp(raw['created_at']) > timestamp(registration['registered_at']),
                'Historical evidence cannot satisfy a newly registered release batch')
        for key in IDENTITY_KEYS:
            require(raw[key] == registration[key], 'Batch prompt/model/config changed')
        run_id = raw['run_id']
        require(isinstance(run_id, str) and run_id and run_id not in run_ids, 'Duplicate/missing run ID')
        run_ids.add(run_id)
        require(review.get('policy_sha256') == POLICY_SHA256 and review.get('gate_version') == policy['version'],
                'Review policy identity mismatch')
        require(review.get('registration_sha256') == registration['sha256'], 'Review batch mismatch')
        require(review.get('evidence_sha256') == raw['evidence_sha256'], 'Review evidence mismatch')
        require(isinstance(review.get('reviewer'), str) and review['reviewer'].strip(), 'Reviewer required')
        records = {r['case_id']:r for r in raw['records']}
        decisions = {r['case_id']:r for r in review['cases']}
        require(len(records) == len(raw['records']) == len(decisions) == len(review['cases']) == len(expected_ids)
                and set(records) == set(decisions) == expected_ids, 'Duplicate/missing/unknown cases')
        cases, round_points, round_maximum = [], 0, 0
        for case_id, row in records.items():
            for key in ('prompt_version', 'prompt_sha256', 'provider', 'model'):
                require(row[key] == registration[key], 'Case identity mismatch')
            if row.get('request_id'):
                require(row['request_id'] not in request_ids, 'Reused request across cases/rounds')
                request_ids.add(row['request_id'])
            elif row['execution_status'] == 'success':
                raise ValueError('Successful case lacks request ID')
            schema_ok = row.get('schema_valid') is True and row.get('execution_status') == 'success'
            try:
                AnalyticsAnswer.model_validate(row.get('answer'))
            except ValueError:
                schema_ok = False
            decision = decisions[case_id]
            hard = decision['hard']
            required_hard = set(policy['hard_review_dimensions'])
            if policy['cases'][case_id]['required_language']:
                required_hard.add('requested_language')
            require(set(hard) == required_hard, 'Exact hard-check dimensions required')
            hard_results = {k:judgement(v) for k,v in hard.items()}
            hard_results['schema'] = schema_ok  # A reviewer cannot override this check.
            core_ok = judgement(decision['core'])
            points, maximum, zeroes = quality_points(decision['quality'], policy['cases'][case_id]['quality_applicable'])
            round_points += points
            round_maximum += maximum
            for dimension in zeroes:
                serious_by_case[(case_id, dimension)].add(run_id)
                serious_by_dimension[dimension].add(case_id)
            cases.append({'case_id':case_id, 'hard_results':hard_results, 'core_pass':core_ok,
                          'hard_review':deepcopy(hard), 'core_review':deepcopy(decision['core']),
                          'quality':deepcopy(decision['quality']), 'serious_quality_dimensions':zeroes,
                          'telemetry':{k:deepcopy(row.get(k)) for k in
                                       ('usage','latency_ms','estimated_cost','cost_complete','request_id')}})
        hard_ok = all(all(c['hard_results'].values()) for c in cases)
        core_ok = all(c['core_pass'] for c in cases)
        quality_ok = round_maximum > 0 and 100 * round_points >= policy['quality_threshold_percent'] * round_maximum
        results.append({'run_id':run_id, 'evidence_sha256':raw['evidence_sha256'], 'review_sha256':digest(review),
                        'reviewer':review['reviewer'], 'hard_pass':hard_ok, 'core_pass':core_ok,
                        'quality_points':round_points, 'quality_maximum':round_maximum,
                        'quality_percent':100 * round_points / round_maximum if round_maximum else None,
                        'quality_pass':quality_ok, 'cases':cases})
    repeated = [{'case_id':c, 'dimension':d, 'run_ids':sorted(runs)}
                for (c,d),runs in serious_by_case.items() if len(runs) >= 2]
    spread = [{'dimension':d, 'case_ids':sorted(cases)}
              for d,cases in serious_by_dimension.items() if len(cases) >= 2]
    passed = not repeated and not spread and all(r['hard_pass'] and r['core_pass'] and r['quality_pass'] for r in results)
    return seal({'gate_version':policy['version'], 'policy_sha256':POLICY_SHA256,
                 'registration_sha256':registration['sha256'], 'identity':{k:deepcopy(registration[k]) for k in IDENTITY_KEYS},
                 'suite_sha256':policy['suite_sha256'], 'threshold_percent':policy['quality_threshold_percent'],
                 'threshold_status':policy['threshold_status'], 'rounds':results,
                 'repeated_serious_quality':repeated, 'cross_case_serious_quality':spread,
                 'release_gate':'passed' if passed else 'blocked',
                 'publication_performed':False})


def write_new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    reg = commands.add_parser('register')
    reg.add_argument('--identity', type=Path, required=True)
    reg.add_argument('--output', type=Path, required=True)
    evaluate = commands.add_parser('evaluate')
    evaluate.add_argument('--registration', type=Path, required=True)
    evaluate.add_argument('--raw', type=Path, nargs=3, required=True)
    evaluate.add_argument('--review', type=Path, nargs=3, required=True)
    evaluate.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_text())
    if args.command == 'register':
        result = register(read(args.identity))
    else:
        result = evaluate_batch(read(args.registration), [read(p) for p in args.raw], [read(p) for p in args.review])
    write_new(args.output, result)
    print(json.dumps({'gate_version':result['gate_version'], 'status':result.get('release_gate', 'registered')}))
    if result.get('release_gate') == 'blocked':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
