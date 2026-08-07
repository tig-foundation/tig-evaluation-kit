"""Run canonical or local TIG algorithms over versioned datasets."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import tempfile
from typing import Any

from .build import RustBuildManager
from .catalog import Algorithm, AlgorithmCatalog, normalize_challenge
from .dataset import DatasetError, load_dataset
from .jsonio import atomic_write, canonical_json_bytes, sha256_hex
from .process import SolverProcessResult, run_solver_process
from .source import SourceManager


class RunError(RuntimeError):
    """Raised when a benchmark run cannot be prepared or completed."""


def run_dataset(
    *,
    dataset: Path | str,
    algorithm_selector: str | None = None,
    local: Path | str | None = None,
    baseline: bool = False,
    hyperparameters: dict[str, Any] | None = None,
    algorithm_seed: str = "default_seed",
    timeout_seconds: float = 60.0,
    workers: int = 1,
    repetitions: int = 1,
    output: Path | str | None = None,
    save_solution: bool = False,
    save_all_solutions: bool = False,
    snapshot_interval_seconds: float | None = None,
    snapshot_times_seconds: list[float] | None = None,
    resume: bool = False,
    cache_dir: Path | str | None = None,
) -> Path:
    if sum((algorithm_selector is not None, local is not None, baseline)) != 1:
        raise RunError("Select exactly one of --algorithm, --local, or --baseline")
    if baseline and hyperparameters is not None:
        raise RunError("--hyperparameters is not used with --baseline")
    if baseline and algorithm_seed != "default_seed":
        raise RunError("--algorithm-seed is not used with --baseline")
    if save_solution and save_all_solutions:
        raise RunError("Use either --save-solution or --save-all-solutions, not both")
    if workers < 1:
        raise RunError("--workers must be at least 1")
    if repetitions < 1:
        raise RunError("--repetitions must be at least 1")
    if timeout_seconds <= 0:
        raise RunError("--timeout must be positive")
    if snapshot_interval_seconds is not None and snapshot_times_seconds:
        raise RunError("Use either --snapshot-interval or --snapshot-times, not both")

    dataset_path, dataset_manifest = load_dataset(dataset)
    challenge_id = normalize_challenge(dataset_manifest["challenge_id"])
    if baseline and challenge_id == "c001":
        raise RunError("The canonical c001 challenge does not expose a baseline solver")
    monorepo_commit = dataset_manifest["monorepo_commit"]
    dataset_sha256 = sha256_hex(dataset_path.read_bytes())
    instances = _validated_instances(dataset_path, dataset_manifest)

    source_manager = SourceManager(cache_dir=cache_dir)
    snapshot = source_manager.ensure(monorepo_commit)
    catalog = AlgorithmCatalog(snapshot.checkout)
    algorithm: Algorithm | None = None
    local_path: Path | None = None
    if baseline:
        algorithm_record = {
            "kind": "baseline",
            "id": "tig_baseline",
            "name": "canonical_baseline",
            "monorepo_commit": snapshot.commit,
            "local_source_sha256": None,
        }
    elif algorithm_selector is not None:
        algorithm = catalog.resolve(algorithm_selector, challenge_id)
        algorithm_record: dict[str, Any] = {
            "kind": "monorepo",
            "id": algorithm.id,
            "name": algorithm.name,
            "monorepo_commit": snapshot.commit,
            "local_source_sha256": None,
        }
    else:
        local_path = Path(local).expanduser().resolve()
        algorithm_record = {
            "kind": "local",
            "id": None,
            "name": local_path.stem,
            "source": local_path.name,
            "monorepo_commit": snapshot.commit,
            "local_source_sha256": None,
        }

    tool_fingerprint = _tool_fingerprint()
    environment = _environment_provenance()
    run_configuration = {
        "schema_version": "tig.run-config/v1",
        "tool_fingerprint": tool_fingerprint,
        "tool_revision": _tool_revision(),
        "environment_fingerprint": sha256_hex(canonical_json_bytes(environment)),
        "monorepo_commit": snapshot.commit,
        "dataset_id": dataset_manifest["dataset_id"],
        "dataset_sha256": dataset_sha256,
        "challenge_id": challenge_id,
        "algorithm": algorithm_record,
        "hyperparameters": hyperparameters,
        "algorithm_seed": None if baseline else algorithm_seed,
        "algorithm_seed_derivation": None if baseline else "tig.algorithm-rng/v1",
        "timeout_seconds": timeout_seconds,
        "workers": workers,
        "repetitions": repetitions,
        "capture": {
            "save_solution": save_solution,
            "save_all_solutions": save_all_solutions,
            "snapshot_interval_seconds": snapshot_interval_seconds,
            "snapshot_times_seconds": snapshot_times_seconds or [],
        },
    }

    # Build the selected source before creating the final run fingerprint because a
    # local source hash is part of its algorithm identity.
    build_manager = RustBuildManager(cache_dir)
    evaluator_binary = build_manager.instance_helper(snapshot, challenge_id)
    solver_binary: Path | None = None
    if not baseline:
        solver_binary, local_hash = build_manager.solver_harness(
            snapshot,
            challenge=challenge_id,
            algorithm=algorithm,
            local=local_path,
        )
        if local_hash is not None:
            algorithm_record["local_source_sha256"] = local_hash

    encoded_configuration = canonical_json_bytes(run_configuration)
    run_id = f"run-{sha256_hex(encoded_configuration)[:16]}"
    output_dir = Path(output) if output else Path("runs") / run_id
    run_manifest = {
        "schema_version": "tig.run/v1",
        "run_id": run_id,
        "configuration": run_configuration,
        "environment": environment,
    }
    manifest_path = output_dir / "manifest.json"
    if (
        output_dir.exists()
        and any(output_dir.iterdir())
        and not manifest_path.exists()
    ):
        raise RunError(
            f"Output directory is non-empty and is not a benchmark run: {output_dir}"
        )
    if manifest_path.exists():
        if not resume:
            raise RunError(
                f"Run directory already exists: {output_dir}. Use --resume to continue it."
            )
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunError(f"Could not validate existing run manifest: {exc}") from exc
        if existing_manifest.get("configuration") != run_configuration:
            raise RunError("Existing run configuration does not match this command")
        run_manifest = existing_manifest
    else:
        atomic_write(manifest_path, canonical_json_bytes(run_manifest))

    work_items: list[dict[str, Any]] = []
    for instance_index, instance in enumerate(instances):
        for repetition in range(repetitions):
            result_id = f"{instance_index:06d}-r{repetition:03d}"
            result_path = output_dir / "results" / f"{result_id}.json"
            effective_seed = (
                None
                if baseline
                else derive_algorithm_seed(
                    algorithm_seed, instance["sha256"], repetition
                )
            )
            expected_algorithm_seed = (
                None
                if baseline
                else {"base": algorithm_seed, "effective": effective_seed}
            )
            if resume and _valid_existing_result(
                result_path,
                run_id=run_id,
                result_id=result_id,
                challenge_id=challenge_id,
                algorithm=algorithm_record,
                dataset_id=dataset_manifest["dataset_id"],
                instance_id=instance["id"],
                instance_sha256=instance["sha256"],
                repetition=repetition,
                algorithm_seed=expected_algorithm_seed,
                hyperparameters=hyperparameters,
            ):
                continue
            work_items.append(
                {
                    "result_id": result_id,
                    "instance": instance,
                    "repetition": repetition,
                    "effective_seed": effective_seed,
                    "result_path": result_path,
                }
            )

    def execute(work_item: dict[str, Any]) -> Path:
        instance = work_item["instance"]
        repetition = work_item["repetition"]
        effective_seed = work_item["effective_seed"]
        if baseline:
            process_result = _run_baseline(
                evaluator_binary,
                instance["absolute_path"],
                timeout_seconds,
                save_all_solutions,
                snapshot_interval_seconds,
                snapshot_times_seconds,
            )
        else:
            assert solver_binary is not None
            process_result = run_solver_process(
                solver_binary,
                instance=instance["absolute_path"],
                algorithm_seed=effective_seed,
                hyperparameters=hyperparameters,
                timeout_seconds=timeout_seconds,
                save_all_solutions=save_all_solutions,
                snapshot_interval_seconds=snapshot_interval_seconds,
                snapshot_times_seconds=snapshot_times_seconds,
            )
        result = _materialize_result(
            run_id=run_id,
            result_id=work_item["result_id"],
            challenge_id=challenge_id,
            algorithm=algorithm_record,
            dataset_id=dataset_manifest["dataset_id"],
            instance=instance,
            repetition=repetition,
            algorithm_seed=None if baseline else algorithm_seed,
            effective_seed=effective_seed,
            hyperparameters=hyperparameters,
            process_result=process_result,
            evaluator_binary=evaluator_binary,
            output_dir=output_dir,
            save_solution=save_solution,
            save_all_solutions=save_all_solutions,
        )
        atomic_write(work_item["result_path"], canonical_json_bytes(result))
        _write_log(output_dir, work_item["result_id"], process_result)
        return work_item["result_path"]

    if workers == 1:
        for work_item in work_items:
            execute(work_item)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(execute, work_items))

    result_paths = [
        output_dir / "results" / f"{instance_index:06d}-r{repetition:03d}.json"
        for instance_index in range(len(instances))
        for repetition in range(repetitions)
    ]
    missing = [path for path in result_paths if not path.is_file()]
    if missing:
        raise RunError(f"Run is incomplete; missing result {missing[0]}")
    results_jsonl = b"".join(
        canonical_json_bytes(json.loads(path.read_text(encoding="utf-8")))
        for path in result_paths
    )
    atomic_write(output_dir / "results.jsonl", results_jsonl)
    return output_dir


def derive_algorithm_seed(base: str, instance_sha256: str, repetition: int) -> str:
    digest = hashlib.sha256()
    digest.update(b"tig.algorithm-rng/v1\0")
    encoded_base = base.encode("utf-8")
    digest.update(len(encoded_base).to_bytes(8, "big"))
    digest.update(encoded_base)
    try:
        digest.update(bytes.fromhex(instance_sha256))
    except ValueError as exc:
        raise RunError(f"Invalid instance SHA-256 {instance_sha256!r}") from exc
    digest.update(repetition.to_bytes(8, "big"))
    return digest.hexdigest()


def parse_hyperparameters(
    value: str | None, *, label: str = "Hyperparameters"
) -> dict[str, Any] | None:
    if value is None:
        return None
    candidate = Path(value)
    encoded = value
    try:
        if candidate.is_file():
            encoded = candidate.read_text(encoding="utf-8")
    except OSError:
        pass
    try:
        parsed = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise RunError(f"Could not parse {label.lower()}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RunError(f"{label} must be a JSON object")
    return parsed


def parse_snapshot_times(value: str | None) -> list[float] | None:
    if value is None:
        return None
    try:
        times = [float(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise RunError("Snapshot times must be comma-separated numbers") from exc
    if not times or any(item <= 0 for item in times):
        raise RunError("Snapshot times must contain positive values")
    return sorted(set(times))


def _validated_instances(
    manifest_path: Path, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    root = manifest_path.parent.resolve()
    challenge_id = normalize_challenge(manifest["challenge_id"])
    monorepo_commit = manifest.get("monorepo_commit")
    instances = manifest.get("instances")
    if not isinstance(instances, list) or not instances:
        raise DatasetError("Dataset manifest contains no instances")
    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in instances:
        if not isinstance(entry, dict):
            raise DatasetError("Dataset instance entry is not an object")
        instance_id = entry.get("id")
        relative = entry.get("path")
        expected_hash = entry.get("sha256")
        if not all(isinstance(value, str) for value in (instance_id, relative, expected_hash)):
            raise DatasetError("Dataset instance is missing id, path, or sha256")
        if instance_id in seen_ids:
            raise DatasetError(f"Duplicate instance ID {instance_id!r}")
        seen_ids.add(instance_id)
        if Path(relative).is_absolute():
            raise DatasetError(f"Instance path must be relative: {relative}")
        absolute = (root / relative).resolve()
        if not absolute.is_relative_to(root):
            raise DatasetError(f"Instance path escapes dataset directory: {relative}")
        try:
            encoded = absolute.read_bytes()
        except OSError as exc:
            raise DatasetError(f"Could not read instance {absolute}: {exc}") from exc
        actual_hash = sha256_hex(encoded)
        if actual_hash != expected_hash:
            raise DatasetError(
                f"Instance hash mismatch for {instance_id}: {actual_hash} != {expected_hash}"
            )
        try:
            envelope = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise DatasetError(
                f"Instance {instance_id!r} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(envelope, dict):
            raise DatasetError(f"Instance {instance_id!r} is not a JSON object")
        if envelope.get("schema_version") != "tig.instance/v1":
            raise DatasetError(
                f"Instance {instance_id!r} has an unsupported schema version"
            )
        if envelope.get("instance_id") != instance_id:
            raise DatasetError(
                f"Instance ID mismatch for {instance_id!r}: "
                f"file contains {envelope.get('instance_id')!r}"
            )
        if envelope.get("challenge_id") != challenge_id:
            raise DatasetError(
                f"Instance {instance_id!r} belongs to "
                f"{envelope.get('challenge_id')!r}, not {challenge_id!r}"
            )
        if not isinstance(envelope.get("challenge"), dict):
            raise DatasetError(
                f"Instance {instance_id!r} has no canonical Challenge object"
            )
        provenance = envelope.get("provenance")
        if not isinstance(provenance, dict) or provenance.get(
            "monorepo_commit"
        ) != monorepo_commit:
            raise DatasetError(
                f"Instance {instance_id!r} was not normalized against dataset "
                f"monorepo commit {monorepo_commit}"
            )
        validated.append({**entry, "absolute_path": absolute})
    return validated


def _materialize_result(
    *,
    run_id: str,
    result_id: str,
    challenge_id: str,
    algorithm: dict[str, Any],
    dataset_id: str,
    instance: dict[str, Any],
    repetition: int,
    algorithm_seed: str | None,
    effective_seed: str | None,
    hyperparameters: dict[str, Any] | None,
    process_result: SolverProcessResult,
    evaluator_binary: Path,
    output_dir: Path,
    save_solution: bool,
    save_all_solutions: bool,
) -> dict[str, Any]:
    latest_evaluation: dict[str, Any] | None = None
    evaluation_error: str | None = None
    if process_result.latest_solution is not None:
        latest_evaluation, evaluation_error = _evaluate(
            evaluator_binary,
            instance["absolute_path"],
            process_result.latest_solution.solution,
        )

    if evaluation_error is not None:
        status = "invalid_solution"
    elif process_result.timed_out:
        status = (
            "timeout_with_solution"
            if latest_evaluation is not None
            else "timeout_without_solution"
        )
    elif process_result.startup_timed_out:
        status = "runtime_error"
    elif not process_result.completion_seen:
        status = "runtime_error"
    elif process_result.outcome in {"error", "panic"} or process_result.exit_code != 0:
        status = "runtime_error"
    elif process_result.latest_solution is None:
        status = "no_solution"
    else:
        status = "solution"

    solution_reference: str | None = None
    solution_sha256: str | None = None
    saved_solutions: list[dict[str, Any]] = []
    if save_all_solutions:
        for captured in process_result.all_solutions:
            relative = (
                Path("solutions")
                / result_id
                / f"callback-{captured.callback_index:06d}.json"
            )
            encoded_solution = canonical_json_bytes(captured.solution)
            atomic_write(output_dir / relative, encoded_solution)
            saved_solutions.append(
                {
                    "callback_index": captured.callback_index,
                    "callback_wallclock_ms": captured.callback_wallclock_ms,
                    "path": relative.as_posix(),
                    "sha256": sha256_hex(encoded_solution),
                }
            )
        if saved_solutions:
            solution_reference = saved_solutions[-1]["path"]
            solution_sha256 = saved_solutions[-1]["sha256"]
    elif save_solution and process_result.latest_solution is not None:
        relative = Path("solutions") / result_id / "final.json"
        encoded_solution = canonical_json_bytes(process_result.latest_solution.solution)
        atomic_write(output_dir / relative, encoded_solution)
        solution_reference = relative.as_posix()
        solution_sha256 = sha256_hex(encoded_solution)

    snapshots: list[dict[str, Any]] = []
    for index, snapshot in enumerate(process_result.snapshots, start=1):
        relative = Path("snapshots") / result_id / f"snapshot-{index:06d}.json"
        encoded_snapshot = canonical_json_bytes(snapshot.captured.solution)
        atomic_write(output_dir / relative, encoded_snapshot)
        snapshot_evaluation, snapshot_error = _evaluate(
            evaluator_binary,
            instance["absolute_path"],
            snapshot.captured.solution,
        )
        snapshots.append(
            {
                "requested_wallclock_ms": snapshot.requested_wallclock_ms,
                "callback_index": snapshot.captured.callback_index,
                "callback_wallclock_ms": snapshot.captured.callback_wallclock_ms,
                "path": relative.as_posix(),
                "sha256": sha256_hex(encoded_snapshot),
                "objective": (
                    None
                    if snapshot_evaluation is None
                    else {
                        "value": snapshot_evaluation["objective"],
                        "direction": snapshot_evaluation["direction"],
                    }
                ),
                "error": snapshot_error,
            }
        )

    error_message = evaluation_error or process_result.error
    if not error_message and process_result.exit_code not in (None, 0):
        error_message = f"Solver exited with code {process_result.exit_code}"
    error = None
    if error_message:
        error = {
            "category": (
                "invalid_solution"
                if evaluation_error
                else "timeout"
                if process_result.timed_out
                else "solver_execution"
            ),
            "message": error_message,
        }

    objective = None
    if latest_evaluation is not None:
        objective = {
            "value": latest_evaluation["objective"],
            "direction": latest_evaluation["direction"],
        }
    return {
        "schema_version": "tig.result/v1",
        "run_id": run_id,
        "result_id": result_id,
        "challenge_id": challenge_id,
        "algorithm": algorithm,
        "instance": {
            "dataset_id": dataset_id,
            "id": instance["id"],
            "sha256": instance["sha256"],
        },
        "repetition": repetition,
        "algorithm_seed": (
            None
            if algorithm_seed is None
            else {"base": algorithm_seed, "effective": effective_seed}
        ),
        "hyperparameters": hyperparameters,
        "status": status,
        "solver_wallclock_ms": process_result.solver_wallclock_ms,
        "objective": objective,
        "callback_count": process_result.callback_count,
        "solution": solution_reference,
        "solution_sha256": solution_sha256,
        "saved_solutions": saved_solutions,
        "snapshots": snapshots,
        "peak_memory_bytes": None,
        "error": error,
    }


def _evaluate(
    helper: Path, instance: Path, solution: Any
) -> tuple[dict[str, Any] | None, str | None]:
    with tempfile.TemporaryDirectory(prefix="tig-evaluate-") as temporary_dir:
        solution_path = Path(temporary_dir) / "solution.json"
        solution_path.write_bytes(canonical_json_bytes(solution))
        try:
            completed = subprocess.run(
                [
                    str(helper),
                    "evaluate",
                    "--instance",
                    str(instance),
                    "--solution",
                    str(solution_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            return None, detail
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return None, "Canonical evaluator returned invalid JSON"
        if not isinstance(value, dict) or "objective" not in value:
            return None, "Canonical evaluator returned an invalid result"
        objective = value["objective"]
        if (
            isinstance(objective, bool)
            or not isinstance(objective, (int, float))
            or not math.isfinite(objective)
        ):
            return None, "Canonical evaluator returned a non-finite or non-numeric objective"
        if value.get("direction") not in {"minimize", "maximize"}:
            return None, "Canonical evaluator returned an invalid objective direction"
        return value, None


def _run_baseline(
    helper: Path,
    instance: Path,
    timeout_seconds: float,
    save_all_solutions: bool,
    snapshot_interval_seconds: float | None,
    snapshot_times_seconds: list[float] | None,
) -> SolverProcessResult:
    return run_solver_process(
        helper,
        instance=instance,
        algorithm_seed=None,
        hyperparameters=None,
        timeout_seconds=timeout_seconds,
        save_all_solutions=save_all_solutions,
        snapshot_interval_seconds=snapshot_interval_seconds,
        snapshot_times_seconds=snapshot_times_seconds,
        subcommand="baseline-events",
    )


def _valid_existing_result(
    path: Path,
    *,
    run_id: str,
    result_id: str,
    challenge_id: str,
    algorithm: dict[str, Any],
    dataset_id: str,
    instance_id: str,
    instance_sha256: str,
    repetition: int,
    algorithm_seed: dict[str, str] | None,
    hyperparameters: dict[str, Any] | None,
) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict) or set(value) != _RESULT_FIELDS:
        return False
    instance = value.get("instance")
    if (
        value.get("schema_version") != "tig.result/v1"
        or value.get("run_id") != run_id
        or value.get("result_id") != result_id
        or value.get("challenge_id") != challenge_id
        or value.get("algorithm") != algorithm
        or value.get("algorithm_seed") != algorithm_seed
        or value.get("hyperparameters") != hyperparameters
        or instance
        != {
            "dataset_id": dataset_id,
            "id": instance_id,
            "sha256": instance_sha256,
        }
        or value.get("repetition") != repetition
        or isinstance(value.get("repetition"), bool)
        or value.get("status") not in _RESULT_STATUSES
    ):
        return False

    wallclock = value.get("solver_wallclock_ms")
    peak_memory = value.get("peak_memory_bytes")
    callback_count = value.get("callback_count")
    if wallclock is not None and not _valid_nonnegative_number(wallclock):
        return False
    if peak_memory is not None and not _valid_nonnegative_integer(peak_memory):
        return False
    if not _valid_nonnegative_integer(callback_count):
        return False
    if not _valid_objective(value.get("objective")):
        return False
    if not _valid_error(value.get("error")):
        return False

    solution = value.get("solution")
    solution_hash = value.get("solution_sha256")
    if (solution is None) != (solution_hash is None):
        return False
    if solution is not None and not _valid_sidecar(
        path, solution, solution_hash, Path("solutions") / result_id
    ):
        return False

    saved_solutions = value.get("saved_solutions")
    if not isinstance(saved_solutions, list):
        return False
    saved_paths: dict[str, str] = {}
    for saved in saved_solutions:
        if not isinstance(saved, dict) or set(saved) != {
            "callback_index",
            "callback_wallclock_ms",
            "path",
            "sha256",
        }:
            return False
        callback_index = saved.get("callback_index")
        if (
            not _valid_positive_integer(callback_index)
            or callback_index > callback_count
            or not _valid_nonnegative_number(saved.get("callback_wallclock_ms"))
            or not _valid_sidecar(
                path,
                saved.get("path"),
                saved.get("sha256"),
                Path("solutions") / result_id,
            )
            or saved["path"] in saved_paths
        ):
            return False
        saved_paths[saved["path"]] = saved["sha256"]
    if solution is not None and saved_solutions:
        if saved_paths.get(solution) != solution_hash:
            return False

    snapshots = value.get("snapshots")
    if not isinstance(snapshots, list):
        return False
    snapshot_paths: set[str] = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "requested_wallclock_ms",
            "callback_index",
            "callback_wallclock_ms",
            "path",
            "sha256",
            "objective",
            "error",
        }:
            return False
        callback_index = snapshot.get("callback_index")
        snapshot_error = snapshot.get("error")
        if (
            not _valid_nonnegative_number(snapshot.get("requested_wallclock_ms"))
            or not _valid_positive_integer(callback_index)
            or callback_index > callback_count
            or not _valid_nonnegative_number(snapshot.get("callback_wallclock_ms"))
            or not _valid_objective(snapshot.get("objective"))
            or (snapshot_error is not None and not isinstance(snapshot_error, str))
            or not _valid_sidecar(
                path,
                snapshot.get("path"),
                snapshot.get("sha256"),
                Path("snapshots") / result_id,
            )
            or snapshot["path"] in snapshot_paths
        ):
            return False
        snapshot_paths.add(snapshot["path"])

    status = value["status"]
    objective = value["objective"]
    if status in {"solution", "timeout_with_solution"} and objective is None:
        return False
    if status in {"no_solution", "timeout_without_solution"} and objective is not None:
        return False
    return True


_RESULT_FIELDS = {
    "schema_version",
    "run_id",
    "result_id",
    "challenge_id",
    "algorithm",
    "instance",
    "repetition",
    "algorithm_seed",
    "hyperparameters",
    "status",
    "solver_wallclock_ms",
    "objective",
    "callback_count",
    "solution",
    "solution_sha256",
    "saved_solutions",
    "snapshots",
    "peak_memory_bytes",
    "error",
}

_RESULT_STATUSES = {
    "solution",
    "no_solution",
    "invalid_solution",
    "timeout_with_solution",
    "timeout_without_solution",
    "runtime_error",
    "build_error",
    "unsupported_instance",
    "host_out_of_memory",
    "gpu_out_of_memory",
}


def _valid_nonnegative_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


def _valid_nonnegative_integer(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _valid_positive_integer(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _valid_objective(value: Any) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and set(value) == {"value", "direction"}
        and not isinstance(value.get("value"), bool)
        and isinstance(value.get("value"), (int, float))
        and math.isfinite(value["value"])
        and value.get("direction") in {"minimize", "maximize"}
    )


def _valid_error(value: Any) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and set(value) == {"category", "message"}
        and isinstance(value.get("category"), str)
        and bool(value["category"])
        and isinstance(value.get("message"), str)
        and bool(value["message"])
    )


def _valid_sidecar(
    result_path: Path,
    reference: Any,
    expected_hash: Any,
    expected_parent: Path,
) -> bool:
    if (
        not isinstance(reference, str)
        or not reference
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
    ):
        return False
    relative = Path(reference)
    if relative.is_absolute() or relative.parent != expected_parent:
        return False
    output_root = result_path.parent.parent.resolve()
    absolute = (output_root / relative).resolve()
    if not absolute.is_relative_to(output_root) or not absolute.is_file():
        return False
    digest = hashlib.sha256()
    try:
        with absolute.open("rb") as sidecar:
            for chunk in iter(lambda: sidecar.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == expected_hash


def _write_log(
    output_dir: Path, result_id: str, process_result: SolverProcessResult
) -> None:
    lines = []
    lines.extend(f"stdout: {line}" for line in process_result.stdout_log)
    lines.extend(f"stderr: {line}" for line in process_result.stderr_log)
    if lines:
        atomic_write(
            output_dir / "logs" / f"{result_id}.log",
            ("\n".join(lines) + "\n").encode("utf-8"),
        )


def _tool_fingerprint() -> str:
    package_root = Path(__file__).parent
    files = sorted(package_root.rglob("*.py")) + sorted(
        (package_root / "templates").glob("*.rs")
    )
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        contents = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()


def _environment_provenance() -> dict[str, Any]:
    try:
        rust_version = subprocess.run(
            ["rustc", "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        rust_version = None
    processor = platform.processor().strip() or None
    if processor is None and platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.lower().startswith(("model name", "hardware")):
                    processor = line.split(":", 1)[-1].strip() or None
                    break
        except OSError:
            pass
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "processor": processor,
        "python": platform.python_version(),
        "rust": rust_version,
        "logical_cpu_count": os.cpu_count(),
    }


def _tool_revision() -> dict[str, Any]:
    repository = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        commit = None
        dirty = None
    return {"commit": commit, "dirty": dirty}
