import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.datasets import DatasetPersistenceSettings, PersistenceFailure, PrivateLocalBlobStore


class PrivateDatasetStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "private"
        self.store = PrivateLocalBlobStore(self.root)

    def test_reference_is_opaque_and_content_is_private(self):
        reference = self.store.new_reference("raw")
        size, digest = self.store.put(reference, b"synthetic-only")
        self.assertTrue(reference.startswith("private:v1:"))
        self.assertNotIn(str(self.root), reference)
        self.assertEqual(size, 14)
        self.assertTrue(self.store.verify(reference, size, digest))
        files = list((self.root / "objects").glob("*/*"))
        self.assertEqual(len(files), 1)
        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(files[0]).st_mode & 0o777, 0o600)

    def test_atomic_write_failure_leaves_no_temp_or_object(self):
        reference = self.store.new_reference("dataset")
        with patch("backend.datasets.private_storage.os.link", side_effect=OSError("synthetic")):
            with self.assertRaisesRegex(PersistenceFailure, "storage_write_failed"):
                self.store.put(reference, b"synthetic")
        self.assertEqual(self.store.object_count(), 0)
        self.assertEqual(list((self.root / "objects").glob("*/.staging-*")), [])

    def test_failure_after_publish_removes_published_object(self):
        reference=self.store.new_reference("raw")
        with patch.object(self.store,"_fsync_directory",side_effect=OSError("synthetic")):
            with self.assertRaisesRegex(PersistenceFailure,"storage_write_failed"):
                self.store.put(reference,b"synthetic")
        self.assertEqual(self.store.object_count(),0)

    def test_invalid_references_and_duplicate_write_fail_closed(self):
        for reference in ("../source", "private:v1:nothex:raw", "private:v1:" + "a" * 32 + ":other"):
            with self.subTest(reference=reference), self.assertRaises(PersistenceFailure):
                self.store.verify(reference, 1, "0" * 64)
        reference = self.store.new_reference("raw")
        self.store.put(reference, b"one")
        with self.assertRaisesRegex(PersistenceFailure, "storage_reference_exists"):
            self.store.put(reference, b"two")
        self.assertEqual(self.store.object_count(), 1)

    def test_feature_is_default_off_and_contract_is_strict(self):
        self.assertFalse(DatasetPersistenceSettings().enabled)
        with self.assertRaises(ValidationError):
            DatasetPersistenceSettings(enabled=True, extra=True)

    def test_symlinked_object_shard_is_rejected(self):
        outside=Path(self.temporary.name)/"outside"; outside.mkdir()
        reference="private:v1:"+"a"*32+":raw"
        shard=self.root/"objects"/"aa"
        shard.symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(PersistenceFailure,"invalid_storage_reference"):
            self.store.put(reference,b"synthetic")
        self.assertEqual(list(outside.iterdir()),[])


if __name__ == "__main__":
    unittest.main()
