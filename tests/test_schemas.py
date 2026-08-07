import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator


class JsonSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_root = Path(__file__).resolve().parent.parent / "schemas"
        cls.schemas = {
            path.stem.removesuffix(".schema"): json.loads(
                path.read_text(encoding="utf-8")
            )
            for path in cls.schema_root.glob("*.schema.json")
        }

    def validate(self, name: str, value: object) -> None:
        Draft202012Validator(self.schemas[name]).validate(value)

    def test_schema_documents_are_valid_draft_2020_12(self) -> None:
        self.assertEqual(
            set(self.schemas), {"dataset", "event", "instance", "result", "run"}
        )
        for schema in self.schemas.values():
            Draft202012Validator.check_schema(schema)

    def test_representative_cpu_pipeline_documents_validate(self) -> None:
        commit = "ab" * 20
        instance_hash = "cd" * 32
        self.validate(
            "instance",
            {
                "schema_version": "tig.instance/v1",
                "instance_id": "000000",
                "challenge_id": "c003",
                "challenge": {"num_items": 3},
                "provenance": {
                    "kind": "academic_import",
                    "monorepo_commit": commit,
                    "source": "tiny.txt",
                    "source_sha256": "ef" * 32,
                    "adapter": {"name": "tig-qkp", "version": 1},
                    "generation": None,
                },
            },
        )
        self.validate(
            "dataset",
            {
                "schema_version": "tig.dataset/v1",
                "dataset_id": "c003-academic-test",
                "name": "tiny qkp",
                "challenge_id": "c003",
                "challenge": "knapsack",
                "monorepo_commit": commit,
                "instances": [
                    {
                        "id": "000000",
                        "path": "instances/000000.json",
                        "sha256": instance_hash,
                    }
                ],
                "provenance": {"kind": "academic_import"},
                "metadata": {"bks": {"000000": 27}},
            },
        )
        self.validate(
            "run",
            {
                "schema_version": "tig.run/v1",
                "run_id": "run-test",
                "configuration": {"challenge_id": "c003"},
                "environment": {
                    "os": "Linux",
                    "architecture": "x86_64",
                    "python": "3.12.0",
                },
            },
        )
        self.validate(
            "result",
            {
                "schema_version": "tig.result/v1",
                "run_id": "run-test",
                "result_id": "000000-r000",
                "challenge_id": "c003",
                "algorithm": {"kind": "baseline"},
                "instance": {
                    "dataset_id": "c003-academic-test",
                    "id": "000000",
                    "sha256": instance_hash,
                },
                "repetition": 0,
                "algorithm_seed": None,
                "hyperparameters": None,
                "status": "solution",
                "solver_wallclock_ms": 1.5,
                "objective": {"value": 27, "direction": "maximize"},
                "callback_count": 1,
                "solution": None,
                "solution_sha256": None,
                "saved_solutions": [],
                "snapshots": [],
                "peak_memory_bytes": None,
                "error": None,
            },
        )

    def test_every_solver_event_variant_validates(self) -> None:
        events = [
            {"type": "ready"},
            {
                "type": "solution",
                "callback_wallclock_ms": 2.5,
                "solution": {"items": [0, 1]},
            },
            {"type": "complete", "outcome": "ok", "solver_wallclock_ms": 3.0},
            {
                "type": "complete",
                "outcome": "panic",
                "solver_wallclock_ms": 3.0,
                "error": "panic payload",
            },
        ]
        for event in events:
            self.validate("event", event)


if __name__ == "__main__":
    unittest.main()
