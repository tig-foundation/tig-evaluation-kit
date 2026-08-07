"""Academic instance adapters into the canonical CPU Challenge envelope."""

from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Callable

from .build import RustBuildManager
from .catalog import CPU_CHALLENGES, normalize_challenge
from .jsonio import atomic_directory, atomic_write, canonical_json_bytes, sha256_hex
from .source import SourceManager


class AcademicImportError(RuntimeError):
    """Raised when an academic instance cannot be normalized."""


ADAPTER_VERSION = 1
ZERO_SEED = [0] * 32


def import_dataset(
    *,
    challenge: str,
    inputs: list[Path | str],
    format_name: str = "auto",
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
    output: Path | str | None = None,
    cache_dir: Path | str | None = None,
    source: Path | str | None = None,
) -> Path:
    if metadata is not None and not isinstance(metadata, dict):
        raise AcademicImportError("Dataset metadata must be a JSON object")
    challenge_id = normalize_challenge(challenge)
    paths = _collect_inputs(inputs)
    if not paths:
        raise AcademicImportError("No academic instance files were found")

    snapshot = SourceManager(cache_dir=cache_dir, source=source).ensure()
    helper = RustBuildManager(cache_dir).instance_helper(snapshot, challenge_id)
    normalized: list[dict[str, Any]] = []
    identity_inputs = []
    for path in paths:
        encoded = path.read_bytes()
        source_hash = sha256_hex(encoded)
        selected_format = (
            detect_format(challenge_id, path, encoded)
            if format_name == "auto"
            else format_name
        )
        challenge_value = parse_instance(challenge_id, selected_format, encoded)
        prepared = _prepare(helper, challenge_id, challenge_value)
        normalized.append(
            {
                "path": path,
                "source_sha256": source_hash,
                "format": selected_format,
                "challenge": prepared,
            }
        )
        identity_inputs.append(
            {
                "name": path.name,
                "sha256": source_hash,
                "format": selected_format,
            }
        )

    identity = {
        "schema_version": "tig.dataset-import/v1",
        "challenge_id": challenge_id,
        "monorepo_commit": snapshot.commit,
        "inputs": identity_inputs,
        "adapter_version": ADAPTER_VERSION,
    }
    identity_hash = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    dataset_id = f"{challenge_id}-academic-{identity_hash[:16]}"
    output_dir = Path(output) if output else Path("datasets") / challenge_id / dataset_id
    if (
        output_dir.exists()
        and any(output_dir.iterdir())
        and not (output_dir / "dataset.json").exists()
    ):
        raise AcademicImportError(
            f"Output directory is non-empty and is not a dataset: {output_dir}"
        )
    existing_manifest_path = output_dir / "dataset.json"
    use_existing_output = existing_manifest_path.exists()
    if existing_manifest_path.exists():
        try:
            existing_manifest = json.loads(
                existing_manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise AcademicImportError(
                f"Could not validate existing dataset: {exc}"
            ) from exc
        if existing_manifest.get("dataset_id") != dataset_id:
            raise AcademicImportError(
                f"Output directory contains a different dataset: {output_dir}"
            )

    if output_dir.exists() and not use_existing_output:
        output_dir.rmdir()

    output_context = (
        nullcontext(output_dir)
        if use_existing_output
        else atomic_directory(output_dir)
    )
    with output_context as write_dir:
        manifest_instances = []
        for index, item in enumerate(normalized):
            source_path: Path = item["path"]
            safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", source_path.stem).strip(
                "-._"
            )
            instance_id = f"{index:06d}-{safe_stem or 'instance'}"
            relative_path = Path("instances") / f"{instance_id}.json"
            envelope = {
                "schema_version": "tig.instance/v1",
                "instance_id": instance_id,
                "challenge_id": challenge_id,
                "challenge": item["challenge"],
                "provenance": {
                    "kind": "academic_import",
                    "monorepo_commit": snapshot.commit,
                    "source": source_path.name,
                    "source_sha256": item["source_sha256"],
                    "adapter": {
                        "name": item["format"],
                        "version": ADAPTER_VERSION,
                    },
                    "generation": None,
                    "challenge_seed": {
                        "value": ZERO_SEED,
                        "meaning": "serialization compatibility only; replaced by algorithm RNG seed at run time",
                    },
                },
            }
            instance_bytes = canonical_json_bytes(envelope)
            atomic_write(write_dir / relative_path, instance_bytes, require_same=True)
            manifest_instances.append(
                {
                    "id": instance_id,
                    "path": relative_path.as_posix(),
                    "sha256": sha256_hex(instance_bytes),
                    "source": source_path.name,
                    "source_sha256": item["source_sha256"],
                    "format": item["format"],
                }
            )

        manifest = {
            "schema_version": "tig.dataset/v1",
            "dataset_id": dataset_id,
            "name": name or dataset_id,
            "challenge_id": challenge_id,
            "challenge": CPU_CHALLENGES[challenge_id],
            "monorepo_commit": snapshot.commit,
            "instances": manifest_instances,
            "provenance": {
                "kind": "academic_import",
                "adapter_version": ADAPTER_VERSION,
            },
            "metadata": metadata or {},
        }
        atomic_write(
            write_dir / "dataset.json",
            canonical_json_bytes(manifest),
            require_same=True,
        )
    return output_dir / "dataset.json"


def supported_formats(challenge: str) -> list[str]:
    challenge_id = normalize_challenge(challenge)
    return {
        "c001": ["auto", "dimacs-cnf", "canonical-json"],
        "c002": ["auto", "solomon-vrptw", "canonical-json"],
        "c003": ["auto", "tig-qkp", "canonical-json"],
        "c007": ["auto", "brandimarte-fjsp", "classic-jsp", "canonical-json"],
        "c008": ["auto", "canonical-json"],
    }[challenge_id]


def detect_format(challenge_id: str, path: Path, encoded: bytes) -> str:
    if path.suffix.lower() == ".json":
        return "canonical-json"
    text = encoded.decode("utf-8", errors="replace")
    if challenge_id == "c001" and re.search(r"(?m)^\s*p\s+cnf\s+", text):
        return "dimacs-cnf"
    if challenge_id == "c002" and "VEHICLE" in text.upper() and "CUSTOMER" in text.upper():
        return "solomon-vrptw"
    if challenge_id == "c003":
        first = _nonempty_lines(text)[0].split()
        if len(first) >= 3:
            return "tig-qkp"
    if challenge_id == "c007":
        if path.suffix.lower() == ".fjs":
            return "brandimarte-fjsp"
        lines = _nonempty_lines(text)
        if len(lines) >= 2:
            header = lines[0].split()
            if len(header) >= 2 and all(part.isdigit() for part in header[:2]):
                num_machines = int(header[1])
                second = lines[1].split()
                if len(second) == 2 * num_machines:
                    return "classic-jsp"
                return "brandimarte-fjsp"
    raise AcademicImportError(
        f"Could not detect the {challenge_id} format for {path}; use --format explicitly"
    )


def parse_instance(challenge_id: str, format_name: str, encoded: bytes) -> dict[str, Any]:
    parsers: dict[tuple[str, str], Callable[[bytes], dict[str, Any]]] = {
        ("c001", "dimacs-cnf"): _parse_dimacs,
        ("c002", "solomon-vrptw"): _parse_solomon,
        ("c003", "tig-qkp"): _parse_tig_qkp,
        ("c007", "brandimarte-fjsp"): _parse_brandimarte,
        ("c007", "classic-jsp"): _parse_classic_jsp,
    }
    if format_name == "canonical-json":
        return _parse_canonical_json(challenge_id, encoded)
    try:
        parser = parsers[(challenge_id, format_name)]
    except KeyError as exc:
        choices = ", ".join(supported_formats(challenge_id))
        raise AcademicImportError(
            f"Format {format_name!r} is not supported for {challenge_id}; choose: {choices}"
        ) from exc
    return parser(encoded)


def _parse_canonical_json(challenge_id: str, encoded: bytes) -> dict[str, Any]:
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise AcademicImportError(f"Invalid canonical JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AcademicImportError("Canonical JSON must be an object")
    if value.get("schema_version") == "tig.instance/v1":
        if value.get("challenge_id") != challenge_id:
            raise AcademicImportError("Canonical instance challenge does not match --challenge")
        value = value.get("challenge")
    if not isinstance(value, dict):
        raise AcademicImportError("Canonical Challenge JSON must be an object")
    return value


def _parse_dimacs(encoded: bytes) -> dict[str, Any]:
    text = encoded.decode("utf-8")
    tokens: list[int] = []
    num_variables: int | None = None
    expected_clauses: int | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("c") or stripped.startswith("%"):
            continue
        if stripped.startswith("p"):
            parts = stripped.split()
            if len(parts) != 4 or parts[1].lower() != "cnf":
                raise AcademicImportError(f"Invalid DIMACS header on line {line_number}")
            num_variables, expected_clauses = int(parts[2]), int(parts[3])
            continue
        try:
            tokens.extend(int(part) for part in stripped.split())
        except ValueError as exc:
            raise AcademicImportError(f"Invalid DIMACS integer on line {line_number}") from exc
    if num_variables is None or expected_clauses is None:
        raise AcademicImportError("DIMACS file is missing `p cnf` header")
    clauses: list[list[int]] = []
    current: list[int] = []
    for literal in tokens:
        if literal == 0:
            if len(current) != 3:
                raise AcademicImportError(
                    "TIG satisfiability algorithms require exactly three literals per clause"
                )
            clauses.append(current)
            current = []
        else:
            if abs(literal) > num_variables:
                raise AcademicImportError(f"DIMACS literal {literal} exceeds variable count")
            current.append(literal)
    if current:
        raise AcademicImportError("DIMACS final clause is not terminated by 0")
    if len(clauses) != expected_clauses:
        raise AcademicImportError(
            f"DIMACS declares {expected_clauses} clauses but contains {len(clauses)}"
        )
    return {"seed": ZERO_SEED, "num_variables": num_variables, "clauses": clauses}


def _parse_solomon(encoded: bytes) -> dict[str, Any]:
    lines = _nonempty_lines(encoded.decode("utf-8"))
    vehicle_index = next(
        (index for index, line in enumerate(lines) if line.upper() == "VEHICLE"), None
    )
    if vehicle_index is None or vehicle_index + 2 >= len(lines):
        raise AcademicImportError("Solomon instance is missing the VEHICLE section")
    try:
        fleet_size, max_capacity = [
            int(value) for value in lines[vehicle_index + 2].split()[:2]
        ]
    except (ValueError, IndexError) as exc:
        raise AcademicImportError("Invalid Solomon fleet size/capacity line") from exc
    customer_index = next(
        (
            index
            for index in range(vehicle_index + 3, len(lines))
            if lines[index].upper() == "CUSTOMER"
        ),
        None,
    )
    if customer_index is None:
        raise AcademicImportError("Solomon instance is missing the CUSTOMER section")

    rows = []
    for line in lines[customer_index + 1 :]:
        parts = line.split()
        if len(parts) < 7 or not re.fullmatch(r"[+-]?\d+", parts[0]):
            continue
        try:
            rows.append(tuple(int(value) for value in parts[:7]))
        except ValueError as exc:
            raise AcademicImportError(f"Invalid Solomon customer row: {line}") from exc
    if not rows:
        raise AcademicImportError("Solomon instance contains no customer rows")
    if [row[0] for row in rows] != list(range(len(rows))):
        raise AcademicImportError("Solomon customer IDs must be contiguous and start at zero")
    positions = [(row[1], row[2]) for row in rows]
    services = [row[6] for row in rows[1:]] or [rows[0][6]]
    if len(set(services)) != 1:
        raise AcademicImportError("Canonical TIG vehicle routing requires uniform service times")
    distances = [
        [int(math.hypot(left[0] - right[0], left[1] - right[1]) + 0.5) for right in positions]
        for left in positions
    ]
    return {
        "seed": ZERO_SEED,
        "num_nodes": len(rows),
        "demands": [row[3] for row in rows],
        "node_positions": positions,
        "distance_matrix": distances,
        "max_capacity": max_capacity,
        "fleet_size": fleet_size,
        "service_time": services[0],
        "ready_times": [row[4] for row in rows],
        "due_times": [row[5] for row in rows],
        "greedy_baseline_total_distance": 0,
    }


def _parse_tig_qkp(encoded: bytes) -> dict[str, Any]:
    lines = _nonempty_lines(encoded.decode("utf-8"))
    try:
        header = lines[0].split()
        num_items, edge_count = int(header[0]), int(header[1])
    except (ValueError, IndexError) as exc:
        raise AcademicImportError("QKP header must be `num_items edge_count type`") from exc
    if len(lines) < edge_count + 3:
        raise AcademicImportError("QKP file is truncated")
    values = [0] * num_items
    interactions = [[0] * num_items for _ in range(num_items)]
    seen_edges = set()
    for line in lines[1 : edge_count + 1]:
        try:
            left, right, value = (int(item) for item in line.split()[:3])
        except ValueError as exc:
            raise AcademicImportError(f"Invalid QKP edge: {line}") from exc
        if not (0 <= left < num_items and 0 <= right < num_items):
            raise AcademicImportError(f"QKP edge index is out of range: {left}, {right}")
        edge = (min(left, right), max(left, right))
        if edge in seen_edges:
            raise AcademicImportError(f"Duplicate QKP edge: {left}, {right}")
        seen_edges.add(edge)
        if left == right:
            if value < 0:
                raise AcademicImportError("Canonical TIG knapsack does not support negative item values")
            values[left] = value
        else:
            interactions[left][right] = value
            interactions[right][left] = value
    try:
        weights = [int(value) for value in lines[edge_count + 1].split()]
        max_weight = int(lines[edge_count + 2].split()[0])
    except (ValueError, IndexError) as exc:
        raise AcademicImportError("Invalid QKP weights or budget") from exc
    if len(weights) != num_items or any(value < 0 for value in weights):
        raise AcademicImportError(f"QKP must contain {num_items} non-negative weights")
    return {
        "seed": ZERO_SEED,
        "num_items": num_items,
        "weights": weights,
        "values": values,
        "interaction_values": interactions,
        "max_weight": max_weight,
    }


def _parse_brandimarte(encoded: bytes) -> dict[str, Any]:
    lines = _nonempty_lines(encoded.decode("utf-8"))
    try:
        header = [int(value) for value in lines[0].split()]
        num_jobs, num_machines = header[:2]
        tokens = [int(value) for line in lines[1:] for value in line.split()]
    except (ValueError, IndexError) as exc:
        raise AcademicImportError("Invalid Brandimarte header or token") from exc
    position = 0
    jobs = []
    for _ in range(num_jobs):
        if position >= len(tokens):
            raise AcademicImportError("Brandimarte file ends before all jobs are defined")
        operation_count = tokens[position]
        position += 1
        operations = []
        for _ in range(operation_count):
            if position >= len(tokens):
                raise AcademicImportError("Brandimarte operation is truncated")
            machine_count = tokens[position]
            position += 1
            operation: dict[str, int] = {}
            for _ in range(machine_count):
                if position + 1 >= len(tokens):
                    raise AcademicImportError("Brandimarte machine/time pair is truncated")
                machine, duration = tokens[position], tokens[position + 1]
                position += 2
                if not (1 <= machine <= num_machines) or duration < 0:
                    raise AcademicImportError("Invalid Brandimarte machine or duration")
                operation[str(machine - 1)] = duration
            if not operation:
                raise AcademicImportError("Brandimarte operation has no eligible machine")
            operations.append(operation)
        jobs.append(operations)
    if position != len(tokens):
        raise AcademicImportError("Brandimarte file contains extra tokens after its jobs")
    return _job_challenge(num_jobs, num_machines, jobs)


def _parse_classic_jsp(encoded: bytes) -> dict[str, Any]:
    lines = _nonempty_lines(encoded.decode("utf-8"))
    try:
        num_jobs, num_machines = (int(value) for value in lines[0].split()[:2])
    except (ValueError, IndexError) as exc:
        raise AcademicImportError("Classic JSP header must contain jobs and machines") from exc
    if len(lines) - 1 < num_jobs:
        raise AcademicImportError("Classic JSP file contains fewer job rows than declared")
    jobs = []
    for line in lines[1 : num_jobs + 1]:
        try:
            tokens = [int(value) for value in line.split()]
        except ValueError as exc:
            raise AcademicImportError(f"Invalid classic JSP row: {line}") from exc
        if len(tokens) != 2 * num_machines:
            raise AcademicImportError(
                f"Classic JSP row must contain {2 * num_machines} integers"
            )
        operations = []
        for position in range(0, len(tokens), 2):
            machine, duration = tokens[position], tokens[position + 1]
            if not (0 <= machine < num_machines) or duration < 0:
                raise AcademicImportError("Invalid classic JSP machine or duration")
            operations.append({str(machine): duration})
        jobs.append(operations)
    return _job_challenge(num_jobs, num_machines, jobs)


def _job_challenge(
    num_jobs: int, num_machines: int, jobs: list[list[dict[str, int]]]
) -> dict[str, Any]:
    products: list[list[dict[str, int]]] = []
    jobs_per_product: list[int] = []
    product_keys: dict[str, int] = {}
    for operations in jobs:
        key = json.dumps(operations, separators=(",", ":"), sort_keys=True)
        product_index = product_keys.get(key)
        if product_index is None:
            product_index = len(products)
            product_keys[key] = product_index
            products.append(operations)
            jobs_per_product.append(0)
        jobs_per_product[product_index] += 1
    return {
        "seed": ZERO_SEED,
        "num_jobs": num_jobs,
        "num_machines": num_machines,
        "num_operations": max((len(value) for value in products), default=0),
        "jobs_per_product": jobs_per_product,
        "product_processing_times": products,
    }


def _prepare(helper: Path, challenge_id: str, challenge: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "schema_version": "tig.instance/v1",
        "instance_id": "prepare",
        "challenge_id": challenge_id,
        "challenge": challenge,
        "provenance": {},
    }
    with tempfile.TemporaryDirectory(prefix="tig-import-") as temporary_dir:
        path = Path(temporary_dir) / "instance.json"
        path.write_bytes(canonical_json_bytes(envelope))
        try:
            result = subprocess.run(
                [str(helper), "prepare", "--instance", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise AcademicImportError(f"Canonical challenge rejected imported instance: {detail}") from exc
    try:
        value = json.loads(result.stdout)
        challenge_value = value["challenge"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AcademicImportError("Canonical prepare helper returned invalid JSON") from exc
    if not isinstance(challenge_value, dict):
        raise AcademicImportError("Canonical prepare helper returned a non-object Challenge")
    return challenge_value


def _collect_inputs(inputs: list[Path | str]) -> list[Path]:
    collected = []
    likely_instance_suffixes = {".txt", ".cnf", ".fjs", ".jsp", ".json", ".dat"}
    for value in inputs:
        path = Path(value).expanduser()
        if path.is_dir():
            collected.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and candidate.suffix.lower() in likely_instance_suffixes
            )
        elif path.is_file():
            collected.append(path)
        else:
            raise AcademicImportError(f"Input does not exist: {path}")
    return sorted(set(path.resolve() for path in collected), key=lambda path: str(path))


def _nonempty_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise AcademicImportError("Instance file is empty")
    return lines
