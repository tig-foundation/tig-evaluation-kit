import json
from pathlib import Path
import tempfile
import unittest

from tig_bench.process import run_solver_process
from tig_bench.runner import derive_algorithm_seed, parse_hyperparameters


class SolverProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_dir.name)
        self.instance = self.root / "instance.json"
        self.instance.write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_dir.cleanup()

    def executable(self, body: str) -> Path:
        path = self.root / "solver.py"
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_captures_completion_and_solutions(self) -> None:
        binary = self.executable(
            "import json\n"
            "print('TIG_EVENT\\t' + json.dumps({'type': 'ready'}), flush=True)\n"
            "print('algorithm log', flush=True)\n"
            "print('TIG_EVENT\\t' + json.dumps({'type': 'solution', "
            "'callback_wallclock_ms': 1.5, 'solution': {'x': 1}}), flush=True)\n"
            "print('TIG_EVENT\\t' + json.dumps({'type': 'complete', "
            "'outcome': 'ok', 'solver_wallclock_ms': 2.5}), flush=True)\n"
        )
        result = run_solver_process(
            binary,
            instance=self.instance,
            algorithm_seed="00" * 32,
            hyperparameters=None,
            timeout_seconds=1,
            save_all_solutions=True,
        )
        self.assertEqual(result.outcome, "ok")
        self.assertTrue(result.completion_seen)
        self.assertEqual(result.callback_count, 1)
        self.assertEqual(result.latest_solution.solution, {"x": 1})
        self.assertEqual(result.stdout_log, ["algorithm log"])

    def test_timeout_keeps_latest_solution_and_snapshot(self) -> None:
        binary = self.executable(
            "import json, time\n"
            "print('TIG_EVENT\\t' + json.dumps({'type': 'ready'}), flush=True)\n"
            "print('TIG_EVENT\\t' + json.dumps({'type': 'solution', "
            "'callback_wallclock_ms': 0.1, 'solution': 'saved'}), flush=True)\n"
            "time.sleep(1)\n"
        )
        result = run_solver_process(
            binary,
            instance=self.instance,
            algorithm_seed="00" * 32,
            hyperparameters={},
            timeout_seconds=0.08,
            snapshot_times_seconds=[0.02, 0.04],
        )
        self.assertTrue(result.timed_out)
        self.assertFalse(result.completion_seen)
        self.assertEqual(result.latest_solution.solution, "saved")
        self.assertEqual(len(result.snapshots), 1)
        self.assertLess(result.solver_wallclock_ms, 100)

    def test_subcommand_process_needs_no_algorithm_seed(self) -> None:
        binary = self.executable(
            "import json, sys\n"
            "assert sys.argv[1] == 'baseline-events'\n"
            "assert '--algorithm-seed' not in sys.argv\n"
            "print('TIG_EVENT\\t' + json.dumps({'type': 'ready'}), flush=True)\n"
            "print('TIG_EVENT\\t' + json.dumps({'type': 'solution', "
            "'callback_wallclock_ms': 0.5, 'solution': {'baseline': True}}), flush=True)\n"
            "print('TIG_EVENT\\t' + json.dumps({'type': 'complete', "
            "'outcome': 'ok', 'solver_wallclock_ms': 0.5}), flush=True)\n"
        )
        result = run_solver_process(
            binary,
            instance=self.instance,
            algorithm_seed=None,
            hyperparameters=None,
            timeout_seconds=1,
            subcommand="baseline-events",
        )
        self.assertEqual(result.outcome, "ok")
        self.assertEqual(result.latest_solution.solution, {"baseline": True})

    def test_startup_time_is_outside_solver_timeout(self) -> None:
        binary = self.executable(
            "import json, time\n"
            "time.sleep(0.08)\n"
            "print('TIG_EVENT\\t' + json.dumps({'type': 'ready'}), flush=True)\n"
            "time.sleep(0.01)\n"
            "print('TIG_EVENT\\t' + json.dumps({'type': 'complete', "
            "'outcome': 'ok', 'solver_wallclock_ms': 10.0}), flush=True)\n"
        )
        result = run_solver_process(
            binary,
            instance=self.instance,
            algorithm_seed="00" * 32,
            hyperparameters=None,
            timeout_seconds=0.04,
        )
        self.assertFalse(result.timed_out)
        self.assertTrue(result.ready)
        self.assertEqual(result.solver_wallclock_ms, 10.0)


class RunInputTests(unittest.TestCase):
    def test_algorithm_seed_depends_on_instance_and_repetition(self) -> None:
        instance_hash = "11" * 32
        first = derive_algorithm_seed("default_seed", instance_hash, 0)
        self.assertEqual(first, derive_algorithm_seed("default_seed", instance_hash, 0))
        self.assertNotEqual(first, derive_algorithm_seed("different", instance_hash, 0))
        self.assertNotEqual(first, derive_algorithm_seed("default_seed", instance_hash, 1))

    def test_hyperparameters_preserve_none_versus_empty_object(self) -> None:
        self.assertIsNone(parse_hyperparameters(None))
        self.assertEqual(parse_hyperparameters("{}"), {})
        with self.assertRaisesRegex(Exception, "JSON object"):
            parse_hyperparameters("[]")


if __name__ == "__main__":
    unittest.main()
