import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tig_bench import importers as importers_module
from tig_bench.importers import (
    AcademicImportError,
    detect_format,
    import_dataset,
    parse_instance,
    supported_formats,
)


class AcademicImporterTests(unittest.TestCase):
    def test_dimacs_three_sat(self) -> None:
        encoded = b"c tiny\np cnf 3 2\n1 -2 3 0\n-1 2 -3 0\n"
        challenge = parse_instance("c001", "dimacs-cnf", encoded)
        self.assertEqual(challenge["num_variables"], 3)
        self.assertEqual(challenge["clauses"], [[1, -2, 3], [-1, 2, -3]])
        self.assertEqual(challenge["seed"], [0] * 32)

    def test_dimacs_rejects_non_three_sat(self) -> None:
        with self.assertRaisesRegex(AcademicImportError, "exactly three"):
            parse_instance("c001", "dimacs-cnf", b"p cnf 2 1\n1 -2 0\n")

    def test_solomon_vrptw(self) -> None:
        encoded = b"""tiny
VEHICLE
NUMBER CAPACITY
2 10
CUSTOMER
CUST NO. X Y DEMAND READY DUE SERVICE
0 0 0 0 0 100 0
1 3 4 5 0 90 10
2 6 8 5 0 90 10
"""
        challenge = parse_instance("c002", "solomon-vrptw", encoded)
        self.assertEqual(challenge["fleet_size"], 2)
        self.assertEqual(challenge["distance_matrix"][0], [0, 5, 10])
        self.assertEqual(challenge["service_time"], 10)

    def test_tig_qkp(self) -> None:
        encoded = b"""3 4 int
0 0 10
1 1 20
0 1 -3
1 2 7
2 3 4
5
"""
        challenge = parse_instance("c003", "tig-qkp", encoded)
        self.assertEqual(challenge["values"], [10, 20, 0])
        self.assertEqual(challenge["interaction_values"][0][1], -3)
        self.assertEqual(challenge["weights"], [2, 3, 4])
        self.assertEqual(challenge["max_weight"], 5)

    def test_standard_fjsp_with_decimal_flexibility(self) -> None:
        encoded = b"""2 2 1.00
2 1 1 3 1 2 4
2 1 1 5 1 2 6
"""
        challenge = parse_instance("c007", "standard-fjsp", encoded)
        self.assertEqual(challenge["num_jobs"], 2)
        self.assertEqual(challenge["jobs_per_product"], [1, 1])
        self.assertEqual(challenge["product_processing_times"][0][0], {"0": 3})
        self.assertEqual(
            detect_format("c007", Path("tiny.fjs"), encoded), "standard-fjsp"
        )
        self.assertIn("standard-fjsp", supported_formats("c007"))

    def test_brandimarte_name_is_a_standard_fjsp_compatibility_alias(self) -> None:
        encoded = b"1 2 2\n1 2 1 3 2 4\n"
        self.assertEqual(
            parse_instance("c007", "brandimarte-fjsp", encoded),
            parse_instance("c007", "standard-fjsp", encoded),
        )

    def test_standard_fjsp_rejects_invalid_header_and_duplicate_machine(self) -> None:
        with self.assertRaisesRegex(AcademicImportError, "positive finite"):
            parse_instance("c007", "standard-fjsp", b"1 1 NaN\n1 1 1 3\n")
        with self.assertRaisesRegex(AcademicImportError, "repeats"):
            parse_instance(
                "c007", "standard-fjsp", b"1 2 2.00\n1 2 1 3 1 4\n"
            )

    def test_classic_jsp(self) -> None:
        encoded = b"""2 2
0 3 1 4
1 5 0 6
"""
        challenge = parse_instance("c007", "classic-jsp", encoded)
        self.assertEqual(challenge["num_operations"], 2)
        self.assertEqual(challenge["product_processing_times"][1][0], {"1": 5})
        self.assertEqual(
            detect_format("c007", Path("tiny.txt"), encoded), "classic-jsp"
        )

    def test_import_rejects_non_object_metadata_before_building(self) -> None:
        with self.assertRaisesRegex(AcademicImportError, "metadata must be a JSON object"):
            import_dataset(
                challenge="c001",
                inputs=[Path("unused")],
                metadata=[],  # type: ignore[arg-type]
            )

    def test_interrupted_import_leaves_no_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source_file = root / "tiny.txt"
            source_file.write_text(
                "3 4 int\n0 0 10\n1 1 20\n0 1 -3\n1 2 7\n2 3 4\n5\n",
                encoding="utf-8",
            )
            output = root / "imported"
            original_atomic_write = importers_module.atomic_write

            def fail_manifest(path: Path, data: bytes, **kwargs: object) -> None:
                if path.name == "dataset.json":
                    raise RuntimeError("simulated interruption")
                original_atomic_write(path, data, **kwargs)

            with (
                patch.object(importers_module, "SourceManager") as source_manager,
                patch.object(importers_module, "RustBuildManager") as build_manager,
                patch.object(
                    importers_module,
                    "_prepare",
                    side_effect=lambda _helper, _challenge_id, challenge: challenge,
                ),
            ):
                source_manager.return_value.ensure.return_value = SimpleNamespace(
                    commit="ab" * 20
                )
                build_manager.return_value.instance_helper.return_value = Path("helper")
                with patch.object(
                    importers_module, "atomic_write", side_effect=fail_manifest
                ):
                    with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                        import_dataset(
                            challenge="c003",
                            inputs=[source_file],
                            format_name="tig-qkp",
                            output=output,
                        )

                self.assertFalse(output.exists())
                manifest = import_dataset(
                    challenge="c003",
                    inputs=[source_file],
                    format_name="tig-qkp",
                    output=output,
                )
                self.assertTrue(manifest.is_file())

    def test_directory_import_preserves_relative_source_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            collection = root / "collection"
            first = collection / "Track1" / "SetA" / "shared.fjs"
            second = collection / "Track3" / "SetB" / "shared.fjs"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text("1 1 1.00\n1 1 1 3\n", encoding="utf-8")
            second.write_text("1 1 1.00\n1 1 1 4\n", encoding="utf-8")
            output = root / "imported"

            with (
                patch.object(importers_module, "SourceManager") as source_manager,
                patch.object(importers_module, "RustBuildManager") as build_manager,
                patch.object(
                    importers_module,
                    "_prepare",
                    side_effect=lambda _helper, _challenge_id, challenge: challenge,
                ),
            ):
                source_manager.return_value.ensure.return_value = SimpleNamespace(
                    commit="ab" * 20
                )
                build_manager.return_value.instance_helper.return_value = Path("helper")
                manifest_path = import_dataset(
                    challenge="c007",
                    inputs=[collection],
                    output=output,
                )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [entry["source"] for entry in manifest["instances"]],
                ["Track1/SetA/shared.fjs", "Track3/SetB/shared.fjs"],
            )
            self.assertTrue(
                all(entry["format"] == "standard-fjsp" for entry in manifest["instances"])
            )
            envelopes = [
                json.loads((output / entry["path"]).read_text(encoding="utf-8"))
                for entry in manifest["instances"]
            ]
            self.assertEqual(
                [value["provenance"]["source"] for value in envelopes],
                ["Track1/SetA/shared.fjs", "Track3/SetB/shared.fjs"],
            )


if __name__ == "__main__":
    unittest.main()
