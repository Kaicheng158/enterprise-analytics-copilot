import tempfile
import unittest
from pathlib import Path
from scripts.migrate import migration_files


class MigrationFileTests(unittest.TestCase):
    def test_checksum_covers_exact_sql(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / '001_initial.sql'
            p.write_text('SELECT 1;')
            self.assertEqual(migration_files(directory)[0][2], hashlib.sha256(p.read_bytes()).hexdigest())

    def test_missing_or_duplicate_sequence_rejected(self):
        for names in [[], ['002_gap.sql'], ['001_one.sql','001_duplicate.sql']]:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as directory:
                for name in names: (Path(directory)/name).write_text('SELECT 1;')
                with self.assertRaises(ValueError): migration_files(directory)
