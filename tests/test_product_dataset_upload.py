"""Offline contracts and security boundaries for PV2-04b."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import uuid

from pydantic import SecretStr, ValidationError

from backend.datasets import Column
from backend.datasets.persistence_models import PersistenceFailure
from backend.datasets.private_storage import PrivateLocalBlobStore
from backend.product_facade.datasets import AuthorizedDatasetProductService
from backend.product_facade.models import (
    CreatePrivateProjectRequestV2,
    DatasetPreviewCell,
    DatasetUploadMetadata,
    ProductDatasetSettings,
)
from backend.product_facade.settings import validate_product_dataset_settings


class ProductDatasetUploadContractTests(unittest.TestCase):
    def test_settings_are_default_off_and_enabled_settings_require_private_inputs(self):
        self.assertFalse(validate_product_dataset_settings(ProductDatasetSettings()).enabled)
        with self.assertRaisesRegex(ValueError, "32 characters"):
            validate_product_dataset_settings(
                ProductDatasetSettings(enabled=True, storage_root="/tmp/eac-datasets")
            )
        with self.assertRaisesRegex(ValueError, "absolute"):
            validate_product_dataset_settings(
                ProductDatasetSettings(
                    enabled=True,
                    idempotency_secret=SecretStr("s" * 32),
                    storage_root="relative/path",
                )
            )

    def test_project_and_upload_contracts_are_strict_and_normalized(self):
        request = CreatePrivateProjectRequestV2(name="Synthetic Project")
        self.assertEqual(request.contract_version, "create-private-project-v2")
        metadata = DatasetUploadMetadata(
            dataset_name="Quarterly sales",
            filename="sales.csv",
            columns=(Column(name="region"), Column(name="revenue", kind="money", unit="USD")),
        )
        self.assertEqual(len(metadata.columns), 2)
        for values in (
            {**metadata.model_dump(), "trusted_local": True},
            {**metadata.model_dump(), "sql": "SELECT *"},
            {**metadata.model_dump(), "principal": "attacker"},
        ):
            with self.assertRaises(ValidationError):
                DatasetUploadMetadata(**values)
        for filename in ("../sales.csv", "sales.xlsm", "sales.CSV", ".sales.csv"):
            with self.subTest(filename=filename), self.assertRaises(ValidationError):
                DatasetUploadMetadata(
                    dataset_name="Sales",
                    filename=filename,
                    columns=(Column(name="value"),),
                )
        with self.assertRaises(ValidationError):
            CreatePrivateProjectRequestV2(name="  Project  ")

    def test_preview_cell_cannot_expose_values(self):
        cell = DatasetPreviewCell(value="[redacted]", kind="money", row=2, column=1)
        self.assertEqual(cell.value, "[redacted]")
        with self.assertRaises(ValidationError):
            DatasetPreviewCell(value="100.00", kind="money", row=2, column=1)

    def test_opaque_blob_reads_are_bounded_and_integrity_checked(self):
        with TemporaryDirectory() as directory:
            store = PrivateLocalBlobStore(Path(directory))
            reference = store.new_reference("dataset")
            payload = b'{"synthetic":true}'
            size, digest = store.put(reference, payload)
            self.assertEqual(
                store.read_verified(
                    reference,
                    expected_size=size,
                    expected_sha256=digest,
                    max_bytes=1024,
                ),
                payload,
            )
            for kwargs in (
                {"expected_size": size + 1, "expected_sha256": digest, "max_bytes": 1024},
                {"expected_size": size, "expected_sha256": "0" * 64, "max_bytes": 1024},
                {"expected_size": size, "expected_sha256": digest, "max_bytes": 1},
            ):
                with self.assertRaises(PersistenceFailure):
                    store.read_verified(reference, **kwargs)

    def test_idempotency_key_is_server_hashed_and_bounded(self):
        settings = ProductDatasetSettings(
            enabled=True,
            idempotency_secret=SecretStr("synthetic-idempotency-secret-32-bytes"),
            storage_root="/tmp/eac-synthetic-datasets",
        )
        service = object.__new__(AuthorizedDatasetProductService)
        service._settings = settings
        digest = service._key_hash("synthetic-request-0001")
        self.assertEqual(len(digest), 64)
        self.assertNotIn("synthetic-request", digest)
        with self.assertRaisesRegex(Exception, "product_idempotency_invalid"):
            service._key_hash("short")

    def test_existing_apps_do_not_publish_upload_routes_and_defaults_remain_off(self):
        from backend.local_product_facade_app import app as product_app
        from backend.main import app as main_app
        from backend.rag_app import app as rag_app
        from backend.workspace_bridge_app import app as bridge_app

        product_paths = {route.path for route in product_app.routes if hasattr(route, "path")}
        self.assertIn("/product-api/v2/projects/{project_id}/datasets", product_paths)
        self.assertFalse(product_app.state.dataset_upload_enabled)
        for existing in (main_app, rag_app, bridge_app):
            paths = {route.path for route in existing.routes if hasattr(route, "path")}
            self.assertFalse(any(path.startswith("/product-api/v2/") for path in paths))
        env_example = (Path(__file__).parents[1] / ".env.example").read_text()
        self.assertIn("PRODUCT_DATASET_UPLOAD_ENABLED=false", env_example)
        harness = (Path(__file__).parents[1] / "frontend" / "product-upload" / "app.js").read_text()
        self.assertIn("crypto.randomUUID", harness)
        self.assertIn("credentials:\"same-origin\"", harness)
        self.assertNotIn("INTERNAL_AGENT_API_KEY", harness)


if __name__ == "__main__":
    unittest.main()
