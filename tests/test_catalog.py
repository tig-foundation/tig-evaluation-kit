from pathlib import Path
import tempfile
import unittest

from tig_bench.catalog import AlgorithmCatalog, CatalogError, normalize_challenge


class AlgorithmCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.monorepo = Path(self.temporary_dir.name)
        self.module_root = (
            self.monorepo / "tig-algorithms" / "src" / "vehicle_routing"
        )
        self.module_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary_dir.cleanup()

    def write_module(self, contents: str) -> None:
        (self.module_root / "mod.rs").write_text(contents, encoding="utf-8")

    def write_algorithm(self, name: str) -> None:
        (self.module_root / f"{name}.rs").write_text(
            "pub fn solve_challenge() {}\n", encoding="utf-8"
        )

    def test_lists_stable_ids_and_friendly_names(self) -> None:
        self.write_module(
            "pub mod first_solver;\n"
            "pub use first_solver as c002_a001;\n"
            "// c002_a002\n"
            "pub mod later_solver;\n"
            "pub use later_solver as c002_a103;\n"
        )
        self.write_algorithm("first_solver")
        self.write_algorithm("later_solver")

        algorithms = AlgorithmCatalog(self.monorepo).list("c002")

        self.assertEqual(
            [(algorithm.id, algorithm.name) for algorithm in algorithms],
            [("c002_a001", "first_solver"), ("c002_a103", "later_solver")],
        )
        self.assertEqual(
            AlgorithmCatalog(self.monorepo).resolve("later_solver", "vehicle_routing").id,
            "c002_a103",
        )

    def test_rejects_export_without_declared_or_present_module(self) -> None:
        self.write_module("pub use missing as c002_a001;\n")
        with self.assertRaisesRegex(CatalogError, "undeclared module"):
            AlgorithmCatalog(self.monorepo).list("c002")

        self.write_module("pub mod missing;\npub use missing as c002_a001;\n")
        with self.assertRaisesRegex(CatalogError, "has no source file"):
            AlgorithmCatalog(self.monorepo).list("c002")

    def test_challenge_aliases_normalize_to_stable_id(self) -> None:
        self.assertEqual(normalize_challenge("vehicle_routing"), "c002")
        self.assertEqual(normalize_challenge("c002"), "c002")
        with self.assertRaisesRegex(CatalogError, "Unsupported CPU challenge"):
            normalize_challenge("vector_search")


if __name__ == "__main__":
    unittest.main()
