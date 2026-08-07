from pathlib import Path
import tempfile
import unittest

from tig_bench.build import RustBuildManager


class LocalSourceHashTests(unittest.TestCase):
    def test_non_mod_entry_hash_includes_nested_rust_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            entry = root / "solver.rs"
            helper = root / "solver" / "helper.rs"
            helper.parent.mkdir()
            entry.write_text("mod helper;\n", encoding="utf-8")
            helper.write_text("pub const VALUE: u8 = 1;\n", encoding="utf-8")

            first = RustBuildManager._local_source_hash(entry)
            helper.write_text("pub const VALUE: u8 = 2;\n", encoding="utf-8")
            second = RustBuildManager._local_source_hash(entry)

            self.assertNotEqual(first, second)

    def test_hash_ignores_non_rust_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            entry = root / "solver.rs"
            notes = root / "notes.txt"
            entry.write_text("pub fn solve() {}\n", encoding="utf-8")
            notes.write_text("first\n", encoding="utf-8")

            first = RustBuildManager._local_source_hash(entry)
            notes.write_text("second\n", encoding="utf-8")
            second = RustBuildManager._local_source_hash(entry)

            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
