"""PV2-04d durable Dataset Agent integration in disposable PostgreSQL."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import uuid

from pydantic import SecretStr
import psycopg
from psycopg import sql

from backend.auth.models import RegisterRequest
from backend.auth.passwords import Argon2PasswordService
from backend.auth.service import LocalAuthService
from backend.auth.settings import LocalAuthSettings
from backend.auth.store import PostgresAuthStore
from backend.authorization import ProjectAuthorizationService, ProjectAuthorizationSettings, project_scope_key
from backend.datasets import DatasetPersistenceSettings, PrivateDatasetPersistenceService
from backend.datasets.analysis_models import DatasetAnalysisSettings
from backend.datasets.private_storage import PrivateLocalBlobStore
from backend.planning.checkpoint_store import PostgresCheckpointStore
from backend.planning.dataset_agent_runtime import (
    DatasetAgentError,
    DurableDatasetAgentRuntime,
    DurableDatasetPlanner,
)
from backend.planning.dataset_models import DatasetAgentRequest, DatasetAgentSettings
from backend.planning.dataset_planner_models import DatasetPlannerQuestion
from backend.planning.dataset_provider import DeterministicDatasetPlanProvider
from backend.planning.deepseek_dataset_planner import (
    DeepSeekDatasetPlannerAdapter,
    DeepSeekDatasetPlannerV3Adapter,
    PreparedDatasetPlanProvider,
)
from backend.planning.deepseek_planner_adapter import DeepSeekPlannerSettings
from backend.planning.planner_safety import PlannerCostGuard
from backend.planning.dataset_validation import DatasetPlanValidator
from backend.planning.task_queue import PersistentAgentQueueSettings, PersistentAgentQueueWorker
from backend.planning.worker_runtime import DurableWorkerRuntime, DurableWorkerSettings
from backend.product_facade.datasets import AuthorizedDatasetProductService
from backend.product_facade.models import (
    CreatePrivateProjectRequestV2,
    DatasetRevisionBinding,
    DatasetUploadMetadata,
    ProductDatasetSettings,
    ProductFacadeSettings,
)
from backend.tools.dataset_analysis import AuthorizedDatasetAnalysisRuntime, build_dataset_analysis_executor
from backend.synthesis.models import (
    SynthesisFailure,
    TrustedSynthesisRequestV1,
    TrustedSynthesisSettings,
)
from backend.synthesis.provider import FakeResultSynthesizer
from backend.synthesis.service import TrustedResultSynthesisService
from scripts.migrate import apply_migrations, connect_kwargs
from tests.dataset_fixtures import workbook


PASSWORD = SecretStr("Synthetic-Dataset-Agent-Password!")
SECRET = b"pv2-04d-dataset-agent-idempotency-secret"


class RecordingExecutor:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = []

    def execute(self, call, context):
        self.calls.append((call.name, dict(call.arguments)))
        return self.delegate.execute(call, context)


class FakePlannerHttpResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.payload[:limit]


class DatasetAgentDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = connect_kwargs()
        cls.database_name = "eac_test_dataset_agent_" + uuid.uuid4().hex
        with psycopg.connect(**cls.admin, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database_name)))
        cls.addClassCleanup(cls.cleanup_database)
        cls.database = {**cls.admin, "dbname": cls.database_name}
        with psycopg.connect(**cls.database, autocommit=True) as connection:
            apply_migrations(connection)

    @classmethod
    def cleanup_database(cls):
        with psycopg.connect(**cls.admin, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.database_name)))

    @contextmanager
    def connections(self):
        connection = psycopg.connect(**self.database)
        try:
            yield connection
        finally:
            connection.close()

    def setUp(self):
        with psycopg.connect(**self.database, autocommit=True) as connection:
            connection.execute("TRUNCATE product_auth.users CASCADE")
            connection.execute("TRUNCATE dataset_store.projects CASCADE")
            connection.execute("TRUNCATE agent_runtime.executions CASCADE")
        self.storage = TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.auth = LocalAuthService(
            PostgresAuthStore(self.connections),
            settings=LocalAuthSettings(
                enabled=True,
                allowed_origin="http://127.0.0.1:8775",
                rate_limit_secret=SecretStr("synthetic-dataset-agent-rate-secret"),
            ),
            passwords=Argon2PasswordService(time_cost=1, memory_cost=8_192, parallelism=1),
        )
        self.authorization = ProjectAuthorizationService(
            self.connections, settings=ProjectAuthorizationSettings(enabled=True)
        )
        self.blobs = PrivateLocalBlobStore(Path(self.storage.name))
        self.persistence = PrivateDatasetPersistenceService(
            self.connections,
            self.blobs,
            settings=DatasetPersistenceSettings(enabled=True),
        )
        self.products = AuthorizedDatasetProductService(
            self.connections,
            self.authorization,
            self.persistence,
            facade_settings=ProductFacadeSettings(enabled=True),
            dataset_settings=ProductDatasetSettings(
                enabled=True,
                idempotency_secret=SecretStr("synthetic-dataset-agent-upload-secret"),
                storage_root=self.storage.name,
            ),
        )
        self.owner = self.register("owner@example.test")
        self.project = self.products.create_project(
            self.owner,
            CreatePrivateProjectRequestV2(name="Dataset Agent Project"),
            idempotency_key="dataset-agent-project-0001",
        )

    def register(self, account):
        _view, token = self.auth.register(
            RegisterRequest(account=account, display_name="Synthetic Analyst", password=PASSWORD),
            client_address="127.0.0.1",
        )
        return self.auth.require_principal(token)[0]

    @staticmethod
    def metadata(filename, name, columns):
        return DatasetUploadMetadata(dataset_name=name, filename=filename, columns=tuple(columns))

    def upload(self, *, key, source, metadata):
        return self.products.upload(
            self.owner,
            self.project.project_id,
            metadata,
            source,
            idempotency_key=key,
        )

    def finance(self):
        return self.upload(
            key="dataset-agent-finance-0001",
            source=(
                b"region,period,revenue,cost\n"
                b"North,2026-01-31,100.25,60\n"
                b"South,2026-01-31,-20.25,30\n"
                b"North,2026-02-28,120,80\n"
                b"South,2026-02-28,0,0\n"
            ),
            metadata=self.metadata(
                "finance.csv",
                "Finance",
                [
                    {"name": "region", "kind": "text", "required": True},
                    {"name": "period", "kind": "date", "required": True},
                    {"name": "revenue", "kind": "money", "required": True, "unit": "USD"},
                    {"name": "cost", "kind": "money", "required": True, "unit": "USD"},
                ],
            ),
        )

    def people(self):
        return self.upload(
            key="dataset-agent-people-0001",
            source=workbook(
                [
                    ["team", "headcount", "capacity"],
                    ["A", "10", "8"],
                    ["B", "20", "16"],
                    ["C", "5", "5"],
                ]
            ),
            metadata=self.metadata(
                "people.xlsx",
                "People",
                [
                    {"name": "team", "kind": "text", "required": True},
                    {"name": "headcount", "kind": "decimal", "required": True},
                    {"name": "capacity", "kind": "decimal", "required": True},
                ],
            ),
        )

    @staticmethod
    def binding(revision):
        return DatasetRevisionBinding(
            asset_id=revision.asset_id,
            asset_revision_id=revision.asset_revision_id,
            dataset_revision_id=revision.dataset_revision_id,
        )

    def request(self, revision, operations):
        return DatasetAgentRequest(
            project_id=self.project.project_id,
            binding=self.binding(revision),
            operations=tuple(operations),
        )

    def stack(self, *, principal=None, provider=None, revision=None):
        principal = principal or self.owner
        provider = provider or DeterministicDatasetPlanProvider()
        store = PostgresCheckpointStore(
            self.connections,
            schema="agent_runtime",
            idempotency_secret=SECRET,
            max_active_executions=20,
            project_authorization_enabled=True,
        )
        analysis = AuthorizedDatasetAnalysisRuntime(
            self.connections,
            self.authorization,
            self.persistence,
            principal=principal,
            project_id=self.project.project_id,
            settings=DatasetAnalysisSettings(enabled=True),
        )
        executor = RecordingExecutor(build_dataset_analysis_executor(analysis))
        settings = DatasetAgentSettings(enabled=True)
        planner = DurableDatasetPlanner(
            provider, store, analysis, executor, settings=settings
        )
        worker = DurableWorkerRuntime(
            store,
            settings=DurableWorkerSettings(
                enabled=True,
                lease_ttl_seconds=1,
                heartbeat_interval_ms=50,
                operation_timeout_ms=2_000,
            ),
        )
        agent = DurableDatasetAgentRuntime(planner, worker, settings=settings)
        queue = PersistentAgentQueueWorker(
            store,
            agent,
            scope_key=project_scope_key(self.project.project_id),
            settings=PersistentAgentQueueSettings(
                enabled=True,
                lease_ttl_seconds=1,
                poll_interval_ms=25,
                max_tasks_per_drain=20,
            ),
        )
        return agent, planner, executor, provider, store, queue

    def finance_request(self, revision):
        return self.request(
            revision,
            (
                {
                    "operation_id": "regional_revenue",
                    "tool_name": "aggregate_dataset_revision",
                    "arguments": {
                        "group_by": ["region"],
                        "aggregations": [
                            {"result_id": "revenue", "function": "sum", "field": "revenue"}
                        ],
                        "sort": [{"key": "revenue", "direction": "descending"}],
                    },
                },
                {
                    "operation_id": "period_change",
                    "tool_name": "compare_dataset_revision",
                    "arguments": {
                        "dimension": "period",
                        "measure": "revenue",
                        "baseline": "2026-01-31",
                        "comparison": "2026-02-28",
                        "derived_metric": "absolute_change",
                    },
                },
            ),
        )

    def test_submit_queue_execute_status_and_atomic_binding(self):
        revision = self.finance()
        request = self.finance_request(revision)
        agent, _, executor, provider, store, queue = self.stack()
        creation = agent.submit(
            request,
            idempotency_key="dataset-agent-task-finance-0001",
            now=datetime.now(UTC),
        )
        self.assertTrue(creation.created)
        with self.connections() as connection:
            binding = connection.execute(
                "SELECT project_id,owner_user_id,state FROM product_auth.project_task_bindings WHERE execution_id=%s",
                (creation.execution.execution_id,),
            ).fetchone()
        self.assertEqual(binding, (self.project.project_id, self.owner.user_id, "active"))
        self.assertEqual(agent.get(creation.execution.execution_id).status, "queued")
        worked = queue.run_once(worker_id="dataset-worker")
        self.assertEqual(worked.outcome, "completed")
        view = agent.get(creation.execution.execution_id)
        self.assertEqual(view.status, "completed")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(executor.calls), 2)
        aggregate, comparison = view.result.results
        self.assertEqual(
            [(row.dimensions[0].result.value, row.measures[0].result.value) for row in aggregate.rows],
            [("North", "220.25"), ("South", "-20.25")],
        )
        self.assertEqual(comparison.rows[0].measures[2].result.value, "40")
        self.assertTrue(all(source.dataset_revision_id == revision.dataset_revision_id for source in view.result.sources))
        self.assertEqual(store.audit_execution_consistency(creation.execution.execution_id).status, "consistent")

    def test_trusted_synthesis_uses_completed_receipts_and_revocation_blocks_read(self):
        revision = self.finance()
        agent, planner, _, _, store, queue = self.stack()
        creation = agent.submit(
            self.finance_request(revision),
            idempotency_key="dataset-synthesis-finance-0001",
        )
        self.assertEqual(queue.run_once(worker_id="synthesis-worker").outcome, "completed")
        service = TrustedResultSynthesisService(
            agent,
            store,
            planner._analysis,
            FakeResultSynthesizer(),
            settings=TrustedSynthesisSettings(enabled=True),
        )
        response = service.synthesize(
            TrustedSynthesisRequestV1(
                task_id=creation.execution.execution_id,
                question="Compare revenue by region and period.",
                output_mode="full_report",
            )
        )
        self.assertEqual(response.mode, "full_report")
        self.assertEqual(response.trace.dataset_revision_id, revision.dataset_revision_id)
        self.assertEqual(
            {item.output_sha256 for item in response.trace.receipts},
            {item.output_sha256 for item in store.list_receipts(creation.execution.execution_id)},
        )
        self.assertTrue(response.calculations)
        self.authorization.revoke_project(self.owner, self.project.project_id)
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_not_authorized"):
            service.authorize_read(response)

    def test_trusted_synthesis_blocks_expired_and_corrupted_revisions(self):
        revision = self.finance()
        agent, planner, _, _, store, queue = self.stack()
        creation = agent.submit(
            self.finance_request(revision),
            idempotency_key="dataset-synthesis-lifecycle-0001",
        )
        self.assertEqual(queue.run_once(worker_id="synthesis-lifecycle-worker").outcome, "completed")
        service = TrustedResultSynthesisService(
            agent,
            store,
            planner._analysis,
            FakeResultSynthesizer(),
            settings=TrustedSynthesisSettings(enabled=True),
        )
        request = TrustedSynthesisRequestV1(
            task_id=creation.execution.execution_id,
            question="Summarize verified revenue.",
            output_mode="short_answer",
        )
        response = service.synthesize(request)
        with self.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE dataset_store.assets SET state='expired' WHERE project_id=%s AND asset_id=%s",
                (self.project.project_id, revision.asset_id),
            )
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_not_authorized"):
            service.synthesize(request)
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_not_authorized"):
            service.authorize_read(response)

        with self.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE dataset_store.assets SET state='active' WHERE project_id=%s AND asset_id=%s",
                (self.project.project_id, revision.asset_id),
            )
            storage_ref = connection.execute(
                "SELECT storage_ref FROM dataset_store.dataset_revisions WHERE project_id=%s AND dataset_revision_id=%s",
                (self.project.project_id, revision.dataset_revision_id),
            ).fetchone()[0]
        self.blobs._path(storage_ref).write_bytes(b"corrupted-synthetic-blob")
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_not_authorized"):
            service.synthesize(request)

    def test_idempotent_submission_and_completed_replay_do_not_repeat_tools(self):
        revision = self.finance()
        request = self.finance_request(revision)
        agent, _, executor, provider, _, queue = self.stack()
        first = agent.submit(request, idempotency_key="dataset-agent-idempotent-0001")
        second = agent.submit(request, idempotency_key="dataset-agent-idempotent-0001")
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.execution.execution_id, second.execution.execution_id)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(queue.run_once(worker_id="idempotent-worker").outcome, "completed")
        calls = len(executor.calls)
        replay = agent.run_to_terminal(
            first.execution.execution_id, worker_id_prefix="replay-worker"
        )
        self.assertEqual(replay.status, "completed")
        self.assertEqual(len(executor.calls), calls)

    def test_independent_xlsx_structure_executes_rank_quality_and_derived_metric(self):
        revision = self.people()
        request = self.request(
            revision,
            (
                {
                    "operation_id": "team_rank",
                    "tool_name": "rank_dataset_revision",
                    "arguments": {"dimension": "team", "measure": "headcount", "limit": 2},
                },
                {
                    "operation_id": "utilization",
                    "tool_name": "derive_dataset_metric",
                    "arguments": {
                        "numerator_field": "capacity",
                        "denominator_field": "headcount",
                        "operation": "percentage",
                        "result_id": "utilization",
                    },
                },
                {
                    "operation_id": "quality",
                    "tool_name": "inspect_dataset_quality",
                    "arguments": {"fields": ["headcount", "capacity"]},
                },
            ),
        )
        agent, _, _, _, _, queue = self.stack()
        creation = agent.submit(request, idempotency_key="dataset-agent-people-0001")
        self.assertEqual(queue.run_once(worker_id="people-worker").outcome, "completed")
        result = agent.get(creation.execution.execution_id).result
        self.assertEqual(result.results[0].rows[0].dimensions[0].result.value, "B")
        self.assertEqual(result.results[1].rows[0].measures[2].result.value, "82.8571")
        self.assertEqual(result.results[2].rows[0].measures[0].result.value, "0")

    def test_cross_user_wrong_field_and_revocation_fail_closed(self):
        revision = self.finance()
        other = self.register("other@example.test")
        other_agent, _, _, _, other_store, _ = self.stack(principal=other)
        with self.assertRaises(DatasetAgentError) as unauthorized:
            other_agent.submit(
                self.finance_request(revision),
                idempotency_key="dataset-agent-cross-user-0001",
            )
        self.assertEqual(unauthorized.exception.code, "dataset_agent_not_authorized")
        with self.connections() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM agent_runtime.executions").fetchone()[0], 0)

        bad_request = self.request(
            revision,
            (
                {
                    "operation_id": "missing_fields",
                    "tool_name": "aggregate_dataset_revision",
                    "arguments": {
                        "group_by": ["missing_group"],
                        "aggregations": [
                            {"result_id": "value", "function": "sum", "field": "missing_value"}
                        ],
                    },
                },
            ),
        )
        owner_agent, _, _, _, _, _ = self.stack()
        with self.assertRaises(DatasetAgentError) as rejected:
            owner_agent.submit(bad_request, idempotency_key="dataset-agent-bad-field-0001")
        self.assertEqual(rejected.exception.code, "dataset_plan_rejected")
        self.assertEqual(
            {field for issue in rejected.exception.issues for field in issue.fields},
            {"missing_group", "missing_value"},
        )

        creation = owner_agent.submit(
            self.finance_request(revision), idempotency_key="dataset-agent-revoke-0001"
        )
        self.authorization.revoke_project(self.owner, self.project.project_id)
        _, _, _, _, store, queue = self.stack()
        self.assertEqual(queue.run_once(worker_id="revoked-worker").outcome, "idle")
        self.assertEqual(store.list_receipts(creation.execution.execution_id), ())
        with self.assertRaises(DatasetAgentError):
            owner_agent.get(creation.execution.execution_id)

    def test_post_checkpoint_crash_resumes_without_replaying_completed_tool(self):
        revision = self.finance()
        request = self.finance_request(revision)
        agent, _, executor, _, store, _ = self.stack()
        creation = agent.submit(request, idempotency_key="dataset-agent-crash-0001")
        execution_id = creation.execution.execution_id
        crashed = agent.run_next(
            execution_id, worker_id="dataset-crash-worker", crash_at="after_checkpoint"
        )
        self.assertEqual(crashed.outcome, "crashed")
        self.assertEqual(len(executor.calls), 1)
        with self.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE agent_runtime.executions SET lease_expires_at=now()-interval '1 millisecond' WHERE execution_id=%s",
                (execution_id,),
            )
        recovered = agent.run_to_terminal(execution_id, worker_id_prefix="dataset-recovery")
        self.assertEqual(recovered.status, "completed")
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual([item.status for item in store.list_receipts(execution_id)], ["succeeded"] * 3)

    def test_tampered_blob_stops_in_reconciliation_without_fabricated_result(self):
        revision = self.finance()
        request = self.finance_request(revision)
        agent, _, _, _, store, queue = self.stack()
        creation = agent.submit(request, idempotency_key="dataset-agent-tamper-0001")
        with self.connections() as connection:
            reference = connection.execute(
                "SELECT storage_ref FROM dataset_store.dataset_revisions WHERE project_id=%s AND dataset_revision_id=%s",
                (self.project.project_id, revision.dataset_revision_id),
            ).fetchone()[0]
        self.blobs._path(reference).write_bytes(b"{}")
        outcome = queue.run_once(worker_id="tamper-worker")
        self.assertEqual(outcome.outcome, "reconciling")
        self.assertIsNone(agent._planner.final_output(creation.execution.execution_id))
        receipt = store.list_receipts(creation.execution.execution_id)[0]
        self.assertEqual(receipt.status, "ambiguous")

    def test_mock_deepseek_dataset_planner_runs_existing_durable_runtime(self):
        revision = self.finance()
        binding = self.binding(revision)
        analysis = AuthorizedDatasetAnalysisRuntime(
            self.connections,
            self.authorization,
            self.persistence,
            principal=self.owner,
            project_id=self.project.project_id,
            settings=DatasetAnalysisSettings(enabled=True),
        )
        context = analysis.binding_context(binding)
        catalog = analysis.planner_catalog(binding)
        question = DatasetPlannerQuestion(
            project_id=self.project.project_id,
            binding=binding,
            question="Compare January 2026 and February 2026 revenue by region and give the overall change.",
            preferred_language="en",
        )
        draft = {
            "response_version": "dataset-planner-draft-v1",
            "outcome": "plan",
            "operations": [
                {
                    "operation_id": "january_by_region",
                    "tool_name": "aggregate_dataset_revision",
                    "description": "Aggregate January 2026 revenue by region",
                    "arguments": {
                        "filters": [{"field": "period", "operator": "eq", "values": ["2026-01-31"]}],
                        "group_by": ["region"],
                        "aggregations": [{"result_id": "revenue", "function": "sum", "field": "revenue"}],
                        "sort": [{"key": "region", "direction": "ascending"}]
                    },
                    "depends_on": []
                },
                {
                    "operation_id": "february_by_region",
                    "tool_name": "aggregate_dataset_revision",
                    "description": "Aggregate February 2026 revenue by region",
                    "arguments": {
                        "filters": [{"field": "period", "operator": "eq", "values": ["2026-02-28"]}],
                        "group_by": ["region"],
                        "aggregations": [{"result_id": "revenue", "function": "sum", "field": "revenue"}],
                        "sort": [{"key": "region", "direction": "ascending"}]
                    },
                    "depends_on": []
                },
                {
                    "operation_id": "overall_period_change",
                    "tool_name": "compare_dataset_revision",
                    "description": "Compare overall January and February revenue",
                    "arguments": {
                        "dimension": "period",
                        "measure": "revenue",
                        "aggregation": "sum",
                        "baseline": "2026-01-31",
                        "comparison": "2026-02-28",
                        "derived_metric": "absolute_change"
                    },
                    "depends_on": []
                }
            ],
            "clarifications": []
        }

        def opener(request, timeout):
            payload = json.loads(request.data)
            self.assertNotIn(str(self.project.project_id), payload["messages"][1]["content"])
            return FakePlannerHttpResponse({
                "id": "dataset-planner-db-mock-1",
                "model": "deepseek-flash",
                "choices": [{
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(draft)},
                }],
                "usage": {"prompt_tokens": 350, "completion_tokens": 200, "total_tokens": 550},
            })

        adapter = DeepSeekDatasetPlannerAdapter(
            DeepSeekPlannerSettings(
                enabled=True,
                api_key=SecretStr("synthetic-dataset-planner-key-not-real"),
            ),
            validator=DatasetPlanValidator(settings=DatasetAgentSettings(enabled=True)),
            cost_guard=PlannerCostGuard(),
            opener=opener,
        )
        outcome = adapter.plan(question, context, catalog)
        self.assertEqual(outcome.status, "planned")
        agent, _, executor, provider, store, queue = self.stack(
            provider=PreparedDatasetPlanProvider(outcome)
        )
        creation = agent.submit(
            outcome.request,
            idempotency_key="dataset-agent-llm-mock-0001",
        )
        self.assertEqual(queue.run_once(worker_id="dataset-llm-mock-worker").outcome, "completed")
        view = agent.get(creation.execution.execution_id)
        self.assertEqual(view.status, "completed")
        january = view.result.results[0]
        february = view.result.results[1]
        comparison = view.result.results[2]
        self.assertEqual(
            [(row.dimensions[0].result.value, row.measures[0].result.value) for row in january.rows],
            [("North", "100.25"), ("South", "-20.25")],
        )
        self.assertEqual(
            [(row.dimensions[0].result.value, row.measures[0].result.value) for row in february.rows],
            [("North", "120"), ("South", "0")],
        )
        self.assertEqual(comparison.rows[0].measures[0].result.value, "80")
        self.assertEqual(comparison.rows[0].measures[1].result.value, "120")
        self.assertEqual(comparison.rows[0].measures[2].result.value, "40")
        self.assertEqual([call[0] for call in executor.calls], [
            "aggregate_dataset_revision",
            "aggregate_dataset_revision",
            "compare_dataset_revision",
        ])
        self.assertEqual(store.audit_execution_consistency(creation.execution.execution_id).status, "consistent")

    def test_compare_v2_contract_conversion_runs_both_failed_intents_through_durable_runtime(self):
        for index, (question_text, language, group_by) in enumerate(
            (
                (
                    "Compare January 2026 and February 2026 revenue by region and give the overall change.",
                    "en",
                    ["region"],
                ),
                ("比较 2026 年一月与二月的总收入变化。", "zh", []),
            ),
            1,
        ):
            with self.subTest(question=question_text):
                revision = self.finance()
                binding = self.binding(revision)
                analysis = AuthorizedDatasetAnalysisRuntime(
                    self.connections,
                    self.authorization,
                    self.persistence,
                    principal=self.owner,
                    project_id=self.project.project_id,
                    settings=DatasetAnalysisSettings(enabled=True),
                )
                context = analysis.binding_context(binding)
                catalog = analysis.planner_catalog(binding)
                question = DatasetPlannerQuestion(
                    project_id=self.project.project_id,
                    binding=binding,
                    question=question_text,
                    preferred_language=language,
                )
                draft = {
                    "response_version": "dataset-planner-draft-v2",
                    "outcome": "plan",
                    "operations": [
                        {
                            "operation_id": "month_comparison",
                            "tool_name": "compare_dataset_revision_v2",
                            "description": "Compare independently filtered month scopes",
                            "arguments": {
                                "table_index": 1,
                                "baseline_filters": [
                                    {
                                        "field": "period",
                                        "operator": "between",
                                        "values": ["2026-01-01", "2026-01-31"],
                                    }
                                ],
                                "comparison_filters": [
                                    {
                                        "field": "period",
                                        "operator": "between",
                                        "values": ["2026-02-01", "2026-02-28"],
                                    }
                                ],
                                "group_by": group_by,
                                "baseline_measure": "revenue",
                                "comparison_measure": "revenue",
                                "aggregation": "sum",
                                "derived_metric": "absolute_change",
                            },
                            "depends_on": [],
                        }
                    ],
                    "clarifications": [],
                }

                def opener(request, timeout):
                    return FakePlannerHttpResponse(
                        {
                            "id": f"dataset-planner-v3-db-mock-{index}",
                            "model": "deepseek-flash",
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "stop",
                                    "message": {
                                        "role": "assistant",
                                        "content": json.dumps(draft),
                                    },
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 400,
                                "completion_tokens": 120,
                                "total_tokens": 520,
                            },
                        }
                    )

                adapter = DeepSeekDatasetPlannerV3Adapter(
                    DeepSeekPlannerSettings(
                        enabled=True,
                        api_key=SecretStr("synthetic-v3-db-key-not-real"),
                    ),
                    validator=DatasetPlanValidator(
                        settings=DatasetAgentSettings(enabled=True)
                    ),
                    cost_guard=PlannerCostGuard(),
                    opener=opener,
                )
                outcome = adapter.plan(question, context, catalog)
                agent, _, executor, _, store, queue = self.stack(
                    provider=PreparedDatasetPlanProvider(outcome)
                )
                creation = agent.submit(
                    outcome.request,
                    idempotency_key=f"dataset-agent-compare-v2-replay-{index}",
                )
                self.assertEqual(
                    queue.run_once(worker_id=f"compare-v2-worker-{index}").outcome,
                    "completed",
                )
                view = agent.get(creation.execution.execution_id)
                self.assertEqual(view.result.status, "completed")
                result = view.result.results[0]
                self.assertEqual(executor.calls[0][0], "compare_dataset_revision_v2")
                if group_by:
                    values = {
                        row.dimensions[0].result.value: [
                            cell.result.value for cell in row.measures
                        ]
                        for row in result.rows
                    }
                    self.assertEqual(
                        values,
                        {
                            "North": ["100.25", "120", "19.75"],
                            "South": ["-20.25", "0", "20.25"],
                        },
                    )
                else:
                    self.assertEqual(
                        [cell.result.value for cell in result.rows[0].measures],
                        ["80", "120", "40"],
                    )
                self.assertEqual(
                    store.audit_execution_consistency(
                        creation.execution.execution_id
                    ).status,
                    "consistent",
                )


if __name__ == "__main__":
    unittest.main()
