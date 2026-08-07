import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tig_bench.process import CapturedSnapshot, CapturedSolution, SolverProcessResult
from tig_bench.runner import _materialize_result, _valid_existing_result


class ResultMaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_dir.name)
        self.instance_path = self.root / "instance.json"
        self.instance_path.write_text("{}\n", encoding="utf-8")
        self.helper = self.root / "evaluator.py"
        self.helper.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "solution = json.loads(pathlib.Path(sys.argv[sys.argv.index('--solution') + 1]).read_text())\n"
            "if solution.get('invalid'):\n"
            "    print('invalid candidate', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "print(json.dumps({'objective': solution['score'], 'direction': 'maximize'}))\n",
            encoding="utf-8",
        )
        self.helper.chmod(0o755)
        self.instance = {
            "id": "instance-1",
            "sha256": "ab" * 32,
            "absolute_path": self.instance_path,
        }

    def tearDown(self) -> None:
        self.temporary_dir.cleanup()

    def materialize(
        self,
        process_result: SolverProcessResult,
        *,
        result_id: str = "000000-r000",
        save_solution: bool = False,
        save_all_solutions: bool = False,
    ) -> dict:
        return _materialize_result(
            run_id="run-test",
            result_id=result_id,
            challenge_id="c001",
            algorithm={"kind": "local"},
            dataset_id="dataset-test",
            instance=self.instance,
            repetition=0,
            algorithm_seed="default_seed",
            effective_seed="cd" * 32,
            hyperparameters=None,
            process_result=process_result,
            evaluator_binary=self.helper,
            output_dir=self.root / "run",
            save_solution=save_solution,
            save_all_solutions=save_all_solutions,
        )

    @staticmethod
    def completed(**changes) -> SolverProcessResult:
        values = {
            "ready": True,
            "completion_seen": True,
            "exit_code": 0,
            "outcome": "ok",
            "solver_wallclock_ms": 1.0,
        }
        values.update(changes)
        return SolverProcessResult(**values)

    def test_no_solution_and_timeout_without_solution(self) -> None:
        no_solution = self.materialize(self.completed())
        self.assertEqual(no_solution["status"], "no_solution")
        self.assertIsNone(no_solution["objective"])

        timed_out = self.materialize(
            self.completed(
                timed_out=True,
                outcome=None,
                exit_code=-15,
                error="Solver reached timeout",
            ),
            result_id="000001-r000",
        )
        self.assertEqual(timed_out["status"], "timeout_without_solution")
        self.assertEqual(timed_out["error"]["category"], "timeout")

    def test_timeout_keeps_objective_without_retaining_solution(self) -> None:
        latest = CapturedSolution(1, 0.5, {"score": 7})
        result = self.materialize(
            self.completed(
                timed_out=True,
                outcome=None,
                exit_code=-15,
                error="Solver reached timeout",
                callback_count=1,
                latest_solution=latest,
            )
        )
        self.assertEqual(result["status"], "timeout_with_solution")
        self.assertEqual(result["objective"], {"value": 7, "direction": "maximize"})
        self.assertIsNone(result["solution"])
        self.assertFalse((self.root / "run/solutions").exists())

    def test_invalid_latest_solution_has_stable_status(self) -> None:
        latest = CapturedSolution(1, 0.5, {"invalid": True})
        result = self.materialize(
            self.completed(callback_count=1, latest_solution=latest)
        )
        self.assertEqual(result["status"], "invalid_solution")
        self.assertEqual(result["error"]["category"], "invalid_solution")

    def test_runtime_error_keeps_last_valid_objective(self) -> None:
        latest = CapturedSolution(1, 0.5, {"score": 11})
        result = self.materialize(
            self.completed(
                outcome="error",
                error="algorithm rejected its hyperparameters",
                callback_count=1,
                latest_solution=latest,
            )
        )
        self.assertEqual(result["status"], "runtime_error")
        self.assertEqual(result["objective"], {"value": 11, "direction": "maximize"})
        self.assertEqual(result["error"]["category"], "solver_execution")

    def test_missing_completion_event_is_a_runtime_error(self) -> None:
        latest = CapturedSolution(1, 0.5, {"score": 11})
        result = self.materialize(
            self.completed(
                completion_seen=False,
                outcome=None,
                error="Solver exited with code 0 without a completion event",
                callback_count=1,
                latest_solution=latest,
            )
        )
        self.assertEqual(result["status"], "runtime_error")
        self.assertEqual(result["objective"], {"value": 11, "direction": "maximize"})
        self.assertEqual(result["error"]["category"], "solver_execution")

    def test_all_callbacks_and_snapshots_are_sidecars(self) -> None:
        first = CapturedSolution(1, 1.0, {"score": 3})
        second = CapturedSolution(2, 2.0, {"score": 5})
        process = self.completed(
            callback_count=2,
            latest_solution=second,
            all_solutions=[first, second],
            snapshots=[CapturedSnapshot(1500.0, first)],
        )
        result = self.materialize(process, save_all_solutions=True)
        self.assertEqual(len(result["saved_solutions"]), 2)
        self.assertEqual(result["solution"], result["saved_solutions"][-1]["path"])
        self.assertEqual(result["snapshots"][0]["objective"]["value"], 3)
        for sidecar in result["saved_solutions"]:
            self.assertTrue((self.root / "run" / sidecar["path"]).is_file())


class ResumeResultValidationTests(unittest.TestCase):
    def test_resume_requires_matching_terminal_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "results" / "000000-r000.json"
            path.parent.mkdir()
            value = {
                "schema_version": "tig.result/v1",
                "run_id": "run-test",
                "result_id": "000000-r000",
                "challenge_id": "c001",
                "algorithm": {"kind": "local"},
                "instance": {
                    "dataset_id": "dataset-test",
                    "id": "instance-1",
                    "sha256": "ab" * 32,
                },
                "repetition": 0,
                "algorithm_seed": {
                    "base": "default_seed",
                    "effective": "cd" * 32,
                },
                "hyperparameters": None,
                "status": "solution",
                "solver_wallclock_ms": 1.0,
                "objective": {"value": 1, "direction": "maximize"},
                "callback_count": 1,
                "solution": None,
                "solution_sha256": None,
                "saved_solutions": [],
                "snapshots": [],
                "peak_memory_bytes": None,
                "error": None,
            }
            path.write_text(json.dumps(value), encoding="utf-8")
            arguments = {
                "run_id": "run-test",
                "result_id": "000000-r000",
                "challenge_id": "c001",
                "algorithm": {"kind": "local"},
                "dataset_id": "dataset-test",
                "instance_id": "instance-1",
                "instance_sha256": "ab" * 32,
                "repetition": 0,
                "algorithm_seed": {
                    "base": "default_seed",
                    "effective": "cd" * 32,
                },
                "hyperparameters": None,
            }
            self.assertTrue(_valid_existing_result(path, **arguments))
            value["instance"]["sha256"] = "ef" * 32
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertFalse(_valid_existing_result(path, **arguments))
            value["instance"]["sha256"] = "ab" * 32
            del value["objective"]
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertFalse(_valid_existing_result(path, **arguments))
            path.write_text("{partial", encoding="utf-8")
            self.assertFalse(_valid_existing_result(path, **arguments))

    def test_resume_rejects_missing_or_corrupt_solution_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            path = root / "results" / "000000-r000.json"
            sidecar = root / "solutions" / "000000-r000" / "final.json"
            path.parent.mkdir()
            sidecar.parent.mkdir(parents=True)
            encoded_solution = b'{"answer":true}\n'
            sidecar.write_bytes(encoded_solution)
            value = {
                "schema_version": "tig.result/v1",
                "run_id": "run-test",
                "result_id": "000000-r000",
                "challenge_id": "c001",
                "algorithm": {"kind": "local"},
                "instance": {
                    "dataset_id": "dataset-test",
                    "id": "instance-1",
                    "sha256": "ab" * 32,
                },
                "repetition": 0,
                "algorithm_seed": {
                    "base": "default_seed",
                    "effective": "cd" * 32,
                },
                "hyperparameters": None,
                "status": "solution",
                "solver_wallclock_ms": 1.0,
                "objective": {"value": 1, "direction": "maximize"},
                "callback_count": 1,
                "solution": "solutions/000000-r000/final.json",
                "solution_sha256": hashlib.sha256(encoded_solution).hexdigest(),
                "saved_solutions": [],
                "snapshots": [],
                "peak_memory_bytes": None,
                "error": None,
            }
            path.write_text(json.dumps(value), encoding="utf-8")
            arguments = {
                "run_id": "run-test",
                "result_id": "000000-r000",
                "challenge_id": "c001",
                "algorithm": {"kind": "local"},
                "dataset_id": "dataset-test",
                "instance_id": "instance-1",
                "instance_sha256": "ab" * 32,
                "repetition": 0,
                "algorithm_seed": {
                    "base": "default_seed",
                    "effective": "cd" * 32,
                },
                "hyperparameters": None,
            }
            self.assertTrue(_valid_existing_result(path, **arguments))
            sidecar.write_text("corrupt\n", encoding="utf-8")
            self.assertFalse(_valid_existing_result(path, **arguments))
            sidecar.unlink()
            self.assertFalse(_valid_existing_result(path, **arguments))


if __name__ == "__main__":
    unittest.main()
