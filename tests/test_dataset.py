import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tig_bench import dataset as dataset_module
from tig_bench.dataset import DatasetError, generate_dataset, load_dataset


class DatasetManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_dir.name) / "dataset.json"
        self.manifest = {
            "schema_version": "tig.dataset/v1",
            "dataset_id": "test-dataset",
            "name": "test dataset",
            "challenge_id": "c001",
            "challenge": "satisfiability",
            "monorepo_commit": "ab" * 20,
            "instances": [
                {"id": "000000", "path": "instances/000000.json", "sha256": "cd" * 32}
            ],
            "provenance": {},
            "metadata": {},
        }

    def tearDown(self) -> None:
        self.temporary_dir.cleanup()

    def write_manifest(self) -> None:
        self.path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_accepts_stable_complete_manifest(self) -> None:
        self.write_manifest()
        path, value = load_dataset(self.path)
        self.assertEqual(path, self.path)
        self.assertEqual(value["dataset_id"], "test-dataset")

    def test_rejects_friendly_challenge_id(self) -> None:
        self.manifest["challenge_id"] = "satisfiability"
        self.write_manifest()
        with self.assertRaisesRegex(DatasetError, "stable cNNN"):
            load_dataset(self.path)

    def test_rejects_invalid_commit(self) -> None:
        self.manifest["monorepo_commit"] = "main"
        self.write_manifest()
        with self.assertRaisesRegex(DatasetError, "full lowercase Git SHA"):
            load_dataset(self.path)

    def test_generation_rejects_non_object_metadata_before_building(self) -> None:
        with self.assertRaisesRegex(DatasetError, "metadata must be a JSON object"):
            generate_dataset(
                challenge="c001",
                track="n_vars=10,ratio=4000",
                metadata=[],  # type: ignore[arg-type]
            )

    def test_interrupted_generation_leaves_no_partial_output(self) -> None:
        output = Path(self.temporary_dir.name) / "generated"
        generated = {
            "challenge": {"num_variables": 3, "clauses": [[1, 2, 3]]},
            "generation": {
                "seed": "default_seed",
                "benchmark_settings": {
                    "track_id": "n_vars=3,ratio=1000",
                    "n_vars": 3,
                },
            },
        }
        original_atomic_write = dataset_module.atomic_write

        def fail_manifest(path: Path, data: bytes, **kwargs: object) -> None:
            if path.name == "dataset.json":
                raise RuntimeError("simulated interruption")
            original_atomic_write(path, data, **kwargs)

        with (
            patch.object(dataset_module, "SourceManager") as source_manager,
            patch.object(dataset_module, "RustBuildManager") as build_manager,
            patch.object(dataset_module, "_run_helper", return_value=generated),
        ):
            source_manager.return_value.ensure.return_value = SimpleNamespace(
                commit="ab" * 20
            )
            build_manager.return_value.instance_helper.return_value = Path("helper")
            with patch.object(
                dataset_module, "atomic_write", side_effect=fail_manifest
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                    generate_dataset(
                        challenge="c001",
                        track="n_vars=3,ratio=1000",
                        output=output,
                    )

            self.assertFalse(output.exists())
            manifest = generate_dataset(
                challenge="c001",
                track="n_vars=3,ratio=1000",
                output=output,
            )
            self.assertTrue(manifest.is_file())


if __name__ == "__main__":
    unittest.main()
