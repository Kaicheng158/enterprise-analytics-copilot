"""Independent PV2-04f synthetic end-to-end evaluation in disposable PostgreSQL."""

from datetime import UTC, datetime
import json
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.planning.dataset_agent_runtime import DatasetAgentError
from backend.planning.dataset_models import DatasetPlan, DatasetPlanStep
from backend.planning.dataset_provider import DeterministicDatasetPlanProvider
from backend.product_facade.models import CreatePrivateProjectRequestV2
from backend.synthesis.models import (
    SynthesisFailure,
    TrustedSynthesisRequestV1,
    TrustedSynthesisSettings,
)
from backend.synthesis.provider import FakeResultSynthesizer
from backend.synthesis.service import TrustedResultSynthesisService
from backend.synthesis.validator import TrustedResultValidator
from backend.tools.models import ToolCall
from scripts.migrate import apply_migrations, connect_kwargs
from tests.db import test_dataset_agent_integration as dataset_agent_harness


class WrongIntentProvider:
    """Return a valid cost calculation when the request explicitly asks for revenue."""

    def propose(self, request, context):
        plan = DatasetPlan.model_validate_json(
            DeterministicDatasetPlanProvider().propose(request, context), strict=True
        )
        first = plan.steps[0]
        arguments = dict(first.tool_call.arguments)
        arguments["aggregations"] = [
            {"result_id": "revenue", "function": "sum", "field": "cost"}
        ]
        changed = DatasetPlanStep.model_validate(
            {
                **first.model_dump(mode="json"),
                "tool_call": ToolCall(
                    name=first.tool_call.name, arguments=arguments
                ).model_dump(mode="json"),
            },
            strict=True,
        )
        return plan.model_copy(update={"steps": (changed, *plan.steps[1:])}).model_dump_json()


class PV204FIntegrationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = connect_kwargs()
        cls.database_name = "eac_test_pv204f_" + uuid.uuid4().hex
        with psycopg.connect(**cls.admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database_name))
            )
        cls.database = {**cls.admin, "dbname": cls.database_name}
        with psycopg.connect(**cls.database, autocommit=True) as connection:
            apply_migrations(connection)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(**cls.admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.database_name))
            )

    def setUp(self):
        self.harness = dataset_agent_harness.DatasetAgentDatabaseTests(
            methodName="test_submit_queue_execute_status_and_atomic_binding"
        )
        self.harness.database = self.database
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def service(self, agent, planner, store, *, before_publish=None):
        return TrustedResultSynthesisService(
            agent,
            store,
            planner._analysis,
            FakeResultSynthesizer(),
            settings=TrustedSynthesisSettings(enabled=True),
            before_publish=before_publish,
        )

    @staticmethod
    def synth_request(task_id, *, mode="auto", language="en", question="Analyze the verified data."):
        return TrustedSynthesisRequestV1(
            task_id=task_id,
            question=question,
            output_mode=mode,
            language=language,
        )

    def people_request(self, revision):
        return self.harness.request(
            revision,
            (
                {
                    "operation_id": "team_rank",
                    "tool_name": "rank_dataset_revision",
                    "arguments": {"dimension": "team", "measure": "headcount", "limit": 3},
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
            ),
        )

    def test_english_chinese_chart_report_and_no_chart_modes(self):
        finance = self.harness.finance()
        agent, planner, _, _, store, queue = self.harness.stack()
        created = agent.submit(
            self.harness.finance_request(finance), idempotency_key="pv204f-finance-0001"
        )
        self.assertEqual(queue.run_once(worker_id="pv204f-finance").outcome, "completed")
        service = self.service(agent, planner, store)
        chart = service.synthesize(
            self.synth_request(created.execution.execution_id, question="Compare revenue by region and period.")
        )
        self.assertEqual(chart.mode, "answer_with_chart")
        self.assertTrue(chart.charts)
        self.assertTrue(all(item.sources for item in chart.calculations))
        report = service.synthesize(
            self.synth_request(
                created.execution.execution_id,
                mode="full_report",
                language="zh",
                question="请生成包含来源的完整分析报告。",
            )
        )
        self.assertEqual(report.mode, "full_report")
        self.assertTrue(report.answer.summary.startswith("已从"))
        self.assertEqual(report.report.sources, report.trace.sources)

        scalar_request = self.harness.request(
            finance,
            (
                {
                    "operation_id": "total_revenue",
                    "tool_name": "aggregate_dataset_revision",
                    "arguments": {
                        "aggregations": [
                            {"result_id": "total_revenue", "function": "sum", "field": "revenue"}
                        ]
                    },
                },
            ),
        )
        scalar = agent.submit(scalar_request, idempotency_key="pv204f-scalar-0001")
        self.assertEqual(queue.run_once(worker_id="pv204f-scalar").outcome, "completed")
        scalar_response = service.synthesize(self.synth_request(scalar.execution.execution_id))
        self.assertEqual((scalar_response.mode, scalar_response.charts), ("short_answer", ()))

    def test_multiple_dataset_structures_and_duplicate_submission_are_isolated(self):
        finance = self.harness.finance()
        people = self.harness.people()
        agent, planner, executor, provider, store, queue = self.harness.stack()
        finance_request = self.harness.finance_request(finance)
        first = agent.submit(finance_request, idempotency_key="pv204f-duplicate-0001")
        duplicate = agent.submit(finance_request, idempotency_key="pv204f-duplicate-0001")
        self.assertTrue(first.created)
        self.assertFalse(duplicate.created)
        self.assertEqual(first.execution.execution_id, duplicate.execution.execution_id)
        self.assertEqual(queue.run_once(worker_id="pv204f-duplicate").outcome, "completed")
        calls_after_first = len(executor.calls)
        agent.run_to_terminal(first.execution.execution_id, worker_id_prefix="pv204f-replay")
        self.assertEqual(len(executor.calls), calls_after_first)

        second = agent.submit(
            self.people_request(people), idempotency_key="pv204f-people-0001"
        )
        self.assertEqual(queue.run_once(worker_id="pv204f-people").outcome, "completed")
        service = self.service(agent, planner, store)
        finance_result = service.synthesize(self.synth_request(first.execution.execution_id))
        people_result = service.synthesize(self.synth_request(second.execution.execution_id))
        self.assertEqual(provider.calls, 2)
        self.assertEqual(finance_result.trace.dataset_revision_id, finance.dataset_revision_id)
        self.assertEqual(people_result.trace.dataset_revision_id, people.dataset_revision_id)
        self.assertNotEqual(finance_result.trace.normalized_sha256, people_result.trace.normalized_sha256)
        self.assertNotEqual(
            {item.dataset_revision_id for item in finance_result.trace.sources},
            {item.dataset_revision_id for item in people_result.trace.sources},
        )

    def test_multistep_crash_recovery_reuses_receipts_without_tool_replay(self):
        revision = self.harness.finance()
        agent, planner, executor, _, store, _ = self.harness.stack()
        creation = agent.submit(
            self.harness.finance_request(revision), idempotency_key="pv204f-crash-0001"
        )
        crashed = agent.run_next(
            creation.execution.execution_id,
            worker_id="pv204f-crash-worker",
            crash_at="after_checkpoint",
        )
        self.assertEqual(crashed.outcome, "crashed")
        self.assertEqual(len(executor.calls), 1)
        with self.harness.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE agent_runtime.executions SET lease_expires_at=now()-interval '1 millisecond' WHERE execution_id=%s",
                (creation.execution.execution_id,),
            )
        recovered = agent.run_to_terminal(
            creation.execution.execution_id, worker_id_prefix="pv204f-recovery"
        )
        self.assertEqual(recovered.status, "completed")
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(
            [item.status for item in store.list_receipts(creation.execution.execution_id)],
            ["succeeded", "succeeded", "succeeded"],
        )
        response = self.service(agent, planner, store).synthesize(
            self.synth_request(creation.execution.execution_id)
        )
        self.assertTrue(response.calculations)

    def test_cross_user_project_and_revocation_race_fail_closed(self):
        revision = self.harness.finance()
        agent, planner, _, _, store, queue = self.harness.stack()
        creation = agent.submit(
            self.harness.finance_request(revision), idempotency_key="pv204f-auth-0001"
        )
        self.assertEqual(queue.run_once(worker_id="pv204f-auth").outcome, "completed")
        other = self.harness.register("pv204f-other@example.test")
        other_agent, other_planner, _, _, other_store, _ = self.harness.stack(principal=other)
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_not_authorized"):
            self.service(other_agent, other_planner, other_store).synthesize(
                self.synth_request(creation.execution.execution_id)
            )

        another_project = self.harness.products.create_project(
            self.harness.owner,
            CreatePrivateProjectRequestV2(name="Other Project"),
            idempotency_key="pv204f-other-project-0001",
        )
        wrong_project_request = self.harness.finance_request(revision).model_copy(
            update={"project_id": another_project.project_id}
        )
        with self.assertRaisesRegex(DatasetAgentError, "dataset_agent_not_authorized"):
            agent.submit(wrong_project_request, idempotency_key="pv204f-wrong-project-0001")

        service = self.service(
            agent,
            planner,
            store,
            before_publish=lambda: self.harness.authorization.revoke_project(
                self.harness.owner, self.harness.project.project_id
            ),
        )
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_not_authorized"):
            service.synthesize(self.synth_request(creation.execution.execution_id))

    def test_expired_session_cannot_synthesize_completed_task(self):
        revision = self.harness.finance()
        agent, planner, _, _, store, queue = self.harness.stack()
        creation = agent.submit(
            self.harness.finance_request(revision), idempotency_key="pv204f-session-0001"
        )
        self.assertEqual(queue.run_once(worker_id="pv204f-session").outcome, "completed")
        service = self.service(agent, planner, store)
        response = service.synthesize(self.synth_request(creation.execution.execution_id))
        with self.harness.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE product_auth.sessions SET state='expired',expires_at=created_at+interval '1 second' WHERE session_id=%s",
                (self.harness.owner.session_id,),
            )
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_not_authorized"):
            service.synthesize(self.synth_request(creation.execution.execution_id))
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_not_authorized"):
            service.authorize_read(response)

    def test_lifecycle_receipt_and_report_tampering_fail_closed(self):
        revision = self.harness.finance()
        agent, planner, _, _, store, queue = self.harness.stack()
        creation = agent.submit(
            self.harness.finance_request(revision), idempotency_key="pv204f-integrity-0001"
        )
        self.assertEqual(queue.run_once(worker_id="pv204f-integrity").outcome, "completed")
        service = self.service(agent, planner, store)
        response = service.synthesize(
            self.synth_request(creation.execution.execution_id, mode="full_report")
        )
        claim = response.answer.claims[0].model_copy(update={"text": "total revenue is 999 USD."})
        changed_answer = response.answer.model_copy(
            update={"claims": (claim, *response.answer.claims[1:])}
        )
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_claim_number_ungrounded"):
            TrustedResultValidator().validate(response.model_copy(update={"answer": changed_answer}))
        changed_report = response.report.model_copy(
            update={"sources": tuple(reversed(response.report.sources))}
        )
        if len(response.report.sources) == 1:
            changed_report = response.report.model_copy(update={"sources": ()})
        with self.assertRaises(SynthesisFailure):
            TrustedResultValidator().validate(response.model_copy(update={"report": changed_report}))

        with self.harness.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE dataset_store.assets SET state='expired' WHERE project_id=%s AND asset_id=%s",
                (self.harness.project.project_id, revision.asset_id),
            )
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_not_authorized"):
            service.synthesize(self.synth_request(creation.execution.execution_id))
        with self.harness.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE dataset_store.assets SET state='active' WHERE project_id=%s AND asset_id=%s",
                (self.harness.project.project_id, revision.asset_id),
            )
            storage_ref = connection.execute(
                "SELECT storage_ref FROM dataset_store.dataset_revisions WHERE project_id=%s AND dataset_revision_id=%s",
                (self.harness.project.project_id, revision.dataset_revision_id),
            ).fetchone()[0]
        blob_path = self.harness.blobs._path(storage_ref)
        original_blob = blob_path.read_bytes()
        blob_path.write_bytes(b"pv204f-corrupted-synthetic-blob")
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_not_authorized"):
            service.synthesize(self.synth_request(creation.execution.execution_id))
        blob_path.write_bytes(original_blob)

        with self.harness.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE agent_runtime.step_receipts SET output_json='{}'::jsonb WHERE execution_id=%s AND step_id='assemble_results'",
                (creation.execution.execution_id,),
            )
        with self.assertRaisesRegex(SynthesisFailure, "synthesis_receipt_integrity_invalid"):
            service.synthesize(self.synth_request(creation.execution.execution_id))

    def test_wrong_planner_intent_is_rejected_before_persistence(self):
        revision = self.harness.finance()
        agent, _, _, _, _, _ = self.harness.stack(provider=WrongIntentProvider())
        with self.assertRaises(DatasetAgentError) as rejected:
            agent.submit(
                self.harness.finance_request(revision),
                idempotency_key="pv204f-wrong-intent-0001",
            )
        self.assertEqual(rejected.exception.code, "dataset_plan_rejected")
        self.assertIn("plan_request_mismatch", {item.code for item in rejected.exception.issues})
        with self.harness.connections() as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM agent_runtime.executions").fetchone()[0],
                0,
            )


if __name__ == "__main__":
    unittest.main()
