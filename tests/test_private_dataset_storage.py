import hashlib
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

    def test_cleanup_removes_only_unreferenced_objects_and_staging(self):
        referenced = self.store.new_reference("raw")
        orphan = self.store.new_reference("dataset")
        self.store.put(referenced, b"keep")
        self.store.put(orphan, b"remove")
        shard = self.store._path(self.store.new_reference("raw")).parent
        staging = shard / ".staging-synthetic"
        staging.write_bytes(b"partial")
        result = self.store.cleanup_unreferenced({referenced}, 10)
        self.assertEqual(result[:3], (1, 1, 13))
        self.assertFalse(result[4])
        self.assertTrue(self.store.verify(referenced, 4, hashlib.sha256(b"keep").hexdigest()))
        self.assertFalse(staging.exists())

    def test_cleanup_is_bounded_and_rejects_untrusted_references(self):
        for _ in range(3):
            self.store.put(self.store.new_reference("raw"), b"orphan")
        first = self.store.cleanup_unreferenced(set(), 2)
        self.assertEqual(first[0], 2)
        self.assertTrue(first[4])
        self.assertEqual(self.store.object_count(), 1)
        second = self.store.cleanup_unreferenced(set(), 2)
        self.assertEqual(second[0], 1)
        self.assertFalse(second[4])
        with self.assertRaisesRegex(PersistenceFailure, "invalid_cleanup_reference"):
            self.store.cleanup_unreferenced({"../outside"}, 1)

    def test_cleanup_never_traverses_symlinked_shard(self):
        outside = Path(self.temporary.name) / "outside-cleanup"
        outside.mkdir()
        external = outside / ("b" * 32 + ".raw")
        external.write_bytes(b"outside")
        (self.root / "objects" / "bb").symlink_to(outside, target_is_directory=True)
        result = self.store.cleanup_unreferenced(set(), 10)
        self.assertEqual(result[:2], (0, 0))
        self.assertEqual(external.read_bytes(), b"outside")


if __name__ == "__main__":
    unittest.main()
