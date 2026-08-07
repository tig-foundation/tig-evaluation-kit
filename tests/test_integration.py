import json
import os
from pathlib import Path
import tempfile
import unittest

from tig_bench.dataset import generate_dataset
from tig_bench.importers import import_dataset
from tig_bench.runner import run_dataset


MONOREPO = os.environ.get("TIG_INTEGRATION_MONOREPO")


@unittest.skipUnless(MONOREPO, "set TIG_INTEGRATION_MONOREPO for native smoke tests")
class AuthoritativePipelineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_dir.name)
        configured_cache = os.environ.get("TIG_INTEGRATION_CACHE_DIR")
        self.cache = (
            Path(configured_cache).resolve()
            if configured_cache
            else self.root / "cache"
        )
        self.source = Path(MONOREPO).resolve()
        self.repository = Path(__file__).resolve().parent.parent

    def tearDown(self) -> None:
        self.temporary_dir.cleanup()

    def test_named_local_and_baseline_pipeline(self) -> None:
        first_dataset = generate_dataset(
            challenge="c001",
            track="n_vars=20,ratio=4200",
            count=2,
            seed="integration",
            name="deterministic integration dataset",
            metadata={"purpose": "integration"},
            output=self.root / "generated-first",
            cache_dir=self.cache,
            source=self.source,
        )
        second_dataset = generate_dataset(
            challenge="c001",
            track="n_vars=20,ratio=4200",
            count=2,
            seed="integration",
            name="deterministic integration dataset",
            metadata={"purpose": "integration"},
            output=self.root / "generated-second",
            cache_dir=self.cache,
            source=self.source,
        )
        first_manifest = json.loads(first_dataset.read_text(encoding="utf-8"))
        second_manifest = json.loads(second_dataset.read_text(encoding="utf-8"))
        self.assertEqual(first_manifest, second_manifest)
        generation = first_manifest["provenance"]["generation"]
        self.assertEqual(generation["seed"], "integration")
        self.assertNotIn("rand_hash", generation)
        self.assertNotIn("mode", generation)
        self.assertEqual(
            [entry["sha256"] for entry in first_manifest["instances"]],
            [entry["sha256"] for entry in second_manifest["instances"]],
        )

        named_run = run_dataset(
            dataset=first_dataset,
            algorithm_selector="c001_a001",
            timeout_seconds=5,
            workers=2,
            repetitions=2,
            output=self.root / "named-run",
            cache_dir=self.cache,
            source=self.source,
        )
        named_results = self.read_results(named_run)
        self.assertEqual(len(named_results), 4)
        self.assertTrue(all(result["status"] == "solution" for result in named_results))
        self.assertTrue(
            all(result["objective"]["direction"] == "maximize" for result in named_results)
        )
        self.assertEqual(
            len({result["algorithm_seed"]["effective"] for result in named_results}),
            4,
        )
        self.assertFalse((named_run / "solutions").exists())

        result_mtimes = {
            path.name: path.stat().st_mtime_ns
            for path in (named_run / "results").glob("*.json")
        }
        resumed_run = run_dataset(
            dataset=first_dataset,
            algorithm_selector="c001_a001",
            timeout_seconds=5,
            workers=2,
            repetitions=2,
            output=named_run,
            resume=True,
            cache_dir=self.cache,
            source=self.source,
        )
        self.assertEqual(resumed_run, named_run)
        self.assertEqual(
            result_mtimes,
            {
                path.name: path.stat().st_mtime_ns
                for path in (named_run / "results").glob("*.json")
            },
        )

        alias_run = run_dataset(
            dataset=first_dataset,
            algorithm_selector="schnoing",
            timeout_seconds=5,
            workers=2,
            repetitions=2,
            output=self.root / "alias-run",
            cache_dir=self.cache,
            source=self.source,
        )
        alias_results = self.read_results(alias_run)
        self.assertEqual(
            [result["run_id"] for result in named_results],
            [result["run_id"] for result in alias_results],
        )
        self.assertEqual(
            [result["objective"] for result in named_results],
            [result["objective"] for result in alias_results],
        )
        self.assertEqual(
            [result["algorithm_seed"] for result in named_results],
            [result["algorithm_seed"] for result in alias_results],
        )

        academic_dataset = import_dataset(
            challenge="c001",
            inputs=[self.repository / "tests/fixtures/satisfiability/tiny.cnf"],
            format_name="dimacs-cnf",
            metadata={"source_url": "https://example.invalid/tiny"},
            output=self.root / "academic",
            cache_dir=self.cache,
            source=self.source,
        )
        local_run = run_dataset(
            dataset=academic_dataset,
            local=self.repository / "examples/local/satisfiability.rs",
            timeout_seconds=5,
            output=self.root / "local-run",
            cache_dir=self.cache,
            source=self.source,
        )
        local_result = self.read_results(local_run)[0]
        self.assertEqual(local_result["status"], "solution")
        self.assertIn(local_result["objective"]["value"], (0, 1))
        self.assertEqual(
            json.loads(academic_dataset.read_text(encoding="utf-8"))["metadata"],
            {"source_url": "https://example.invalid/tiny"},
        )
        self.assert_local_behavior_contract(academic_dataset)

        routing_dataset = import_dataset(
            challenge="c002",
            inputs=[self.repository / "tests/fixtures/vehicle_routing/tiny.txt"],
            format_name="solomon-vrptw",
            output=self.root / "routing",
            cache_dir=self.cache,
            source=self.source,
        )
        baseline_run = run_dataset(
            dataset=routing_dataset,
            baseline=True,
            timeout_seconds=5,
            save_solution=True,
            output=self.root / "baseline-run",
            cache_dir=self.cache,
            source=self.source,
        )
        baseline_result = self.read_results(baseline_run)[0]
        self.assertEqual(baseline_result["status"], "solution")
        self.assertEqual(
            baseline_result["objective"], {"value": 20, "direction": "minimize"}
        )
        self.assertTrue((baseline_run / baseline_result["solution"]).is_file())

        knapsack_dataset = import_dataset(
            challenge="c003",
            inputs=[self.repository / "tests/fixtures/knapsack/tiny.txt"],
            format_name="tig-qkp",
            output=self.root / "knapsack",
            cache_dir=self.cache,
            source=self.source,
        )
        knapsack_result = self.run_baseline(knapsack_dataset, "knapsack-run")
        self.assertEqual(
            knapsack_result["objective"], {"value": 27, "direction": "maximize"}
        )

        scheduling_dataset = import_dataset(
            challenge="c007",
            inputs=[self.repository / "tests/fixtures/job_scheduling/tiny.fjs"],
            format_name="brandimarte-fjsp",
            output=self.root / "job-scheduling",
            cache_dir=self.cache,
            source=self.source,
        )
        scheduling_result = self.run_baseline(
            scheduling_dataset, "job-scheduling-run"
        )
        self.assertEqual(scheduling_result["objective"]["direction"], "minimize")
        self.assertIsInstance(scheduling_result["objective"]["value"], int)
        delayed_run = run_dataset(
            dataset=scheduling_dataset,
            local=self.repository / "examples/local/job_scheduling_delayed.rs",
            timeout_seconds=5,
            output=self.root / "job-scheduling-delayed-run",
            cache_dir=self.cache,
            source=self.source,
        )
        delayed_result = self.read_results(delayed_run)[0]
        self.assertEqual(delayed_result["status"], "solution")
        self.assertGreater(
            delayed_result["objective"]["value"],
            scheduling_result["objective"]["value"],
        )

        energy_dataset = generate_dataset(
            challenge="c008",
            track="s=baseline",
            count=1,
            seed="integration",
            output=self.root / "energy",
            cache_dir=self.cache,
            source=self.source,
        )
        energy_result = self.run_baseline(energy_dataset, "energy-run")
        self.assertEqual(energy_result["objective"]["direction"], "maximize")
        self.assertIsInstance(energy_result["objective"]["value"], (int, float))

    def run_baseline(self, dataset: Path, output_name: str) -> dict:
        run = run_dataset(
            dataset=dataset,
            baseline=True,
            timeout_seconds=5,
            output=self.root / output_name,
            cache_dir=self.cache,
            source=self.source,
        )
        result = self.read_results(run)[0]
        self.assertEqual(result["status"], "solution")
        self.assertIsNotNone(result["objective"])
        return result

    def assert_local_behavior_contract(self, dataset: Path) -> None:
        local = self.repository / "tests/fixtures/local/satisfiability_behaviors.rs"

        explicit_empty_run = run_dataset(
            dataset=dataset,
            local=local,
            hyperparameters={},
            timeout_seconds=5,
            output=self.root / "local-empty-parameters",
            cache_dir=self.cache,
            source=self.source,
        )
        explicit_empty = self.read_results(explicit_empty_run)[0]
        self.assertEqual(explicit_empty["status"], "solution")
        self.assertEqual(explicit_empty["hyperparameters"], {})

        expectations = {
            "multiple": ("solution", 2),
            "no_callback": ("no_solution", 0),
            "error": ("runtime_error", 1),
            "panic": ("runtime_error", 1),
        }
        for mode, (status, callback_count) in expectations.items():
            run = run_dataset(
                dataset=dataset,
                local=local,
                hyperparameters={"mode": mode, "ignored_partial_value": 1},
                timeout_seconds=5,
                output=self.root / f"local-{mode}",
                cache_dir=self.cache,
                source=self.source,
            )
            result = self.read_results(run)[0]
            self.assertEqual(result["status"], status)
            self.assertEqual(result["callback_count"], callback_count)
            if mode in {"error", "panic"}:
                self.assertIsNotNone(result["objective"])

    @staticmethod
    def read_results(run: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in (run / "results.jsonl").read_text(encoding="utf-8").splitlines()
        ]


if __name__ == "__main__":
    unittest.main()
