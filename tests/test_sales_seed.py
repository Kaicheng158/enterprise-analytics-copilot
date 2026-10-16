import tempfile
import unittest
from pathlib import Path

from scripts.seed_sales import seed_files


class SalesSeedFileTests(unittest.TestCase):
    def test_seed_sequence_must_be_contiguous(self):
        for names in [[], ["002_gap.sql"], ["001_one.sql", "001_duplicate.sql"]]:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as directory:
                for name in names:
                    (Path(directory) / name).write_text("SELECT 0;")
                with self.assertRaises(ValueError):
                    seed_files(directory)


if __name__ == "__main__":
    unittest.main()
