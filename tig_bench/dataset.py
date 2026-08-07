"""Deterministic exact-TIG dataset generation."""

from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from .build import RustBuildManager
from .catalog import CPU_CHALLENGES, normalize_challenge
from .jsonio import atomic_directory, atomic_write, canonical_json_bytes, sha256_hex
from .source import COMMIT_RE, SourceManager


class DatasetError(RuntimeError):
    """Raised when a dataset cannot be generated or validated."""


def generate_dataset(
    *,
    challenge: str,
    track: str,
    count: int = 1,
    seed: str = "default_seed",
    start_nonce: int = 0,
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
    output: Path | str | None = None,
    cache_dir: Path | str | None = None,
    source: Path | str | None = None,
) -> Path:
    if metadata is not None and not isinstance(metadata, dict):
        raise DatasetError("Dataset metadata must be a JSON object")
    if count < 1:
        raise DatasetError("--count must be at least 1")
    if start_nonce < 0 or start_nonce + count - 1 > (2**64 - 1):
        raise DatasetError("The requested nonce range does not fit in u64")
    if not track:
        raise DatasetError("Generation requires --track")

    challenge_id = normalize_challenge(challenge)
    challenge_name = CPU_CHALLENGES[challenge_id]
    source_manager = SourceManager(cache_dir=cache_dir, source=source)
    snapshot = source_manager.ensure()
    helper = RustBuildManager(cache_dir).instance_helper(snapshot, challenge_id)

    generation_arguments = ["--track", track, "--seed", seed]
    first_generated = _run_helper(
        helper, ["generate", *generation_arguments, "--nonce", str(start_nonce)]
    )
    first_generation = first_generated["generation"]
    canonical_track_id = first_generation["benchmark_settings"]["track_id"]
    identity = {
        "schema_version": "tig.dataset-generation/v1",
        "challenge_id": challenge_id,
        "monorepo_commit": snapshot.commit,
        "track_id": canonical_track_id,
        "seed": first_generation["seed"],
        "start_nonce": start_nonce,
        "count": count,
        "benchmark_settings": first_generation["benchmark_settings"],
    }
    identity_hash = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    dataset_id = f"{challenge_id}-{identity_hash[:16]}"
    output_dir = Path(output) if output else Path("datasets") / challenge_id / dataset_id
    if (
        output_dir.exists()
        and any(output_dir.iterdir())
        and not (output_dir / "dataset.json").exists()
    ):
        raise DatasetError(
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
            raise DatasetError(f"Could not validate existing dataset: {exc}") from exc
        if existing_manifest.get("dataset_id") != dataset_id:
            raise DatasetError(
                f"Output directory contains a different dataset: {output_dir}"
            )

    if output_dir.exists() and not use_existing_output:
        # The non-empty case was rejected above. Removing an explicitly selected
        # empty directory lets the complete dataset be installed atomically.
        output_dir.rmdir()

    output_context = (
        nullcontext(output_dir)
        if use_existing_output
        else atomic_directory(output_dir)
    )
    with output_context as write_dir:
        instances: list[dict[str, Any]] = []
        generation_settings: dict[str, Any] = first_generation["benchmark_settings"]
        for offset in range(count):
            nonce = start_nonce + offset
            generated = (
                first_generated
                if offset == 0
                else _run_helper(
                    helper,
                    ["generate", *generation_arguments, "--nonce", str(nonce)],
                )
            )
            generation = generated["generation"]
            settings = generation["benchmark_settings"]
            if settings["track_id"] != canonical_track_id:
                raise DatasetError(
                    "Canonical track serialization changed during generation"
                )

            instance_id = f"{nonce:06d}"
            relative_path = Path("instances") / f"{instance_id}.json"
            envelope = {
                "schema_version": "tig.instance/v1",
                "instance_id": instance_id,
                "challenge_id": challenge_id,
                "challenge": generated["challenge"],
                "provenance": {
                    "kind": "tig_generated",
                    "monorepo_commit": snapshot.commit,
                    "source": None,
                    "source_sha256": None,
                    "adapter": None,
                    "generation": generation,
                },
            }
            encoded = canonical_json_bytes(envelope)
            atomic_write(write_dir / relative_path, encoded, require_same=True)
            instances.append(
                {
                    "id": instance_id,
                    "path": relative_path.as_posix(),
                    "sha256": sha256_hex(encoded),
                    "nonce": nonce,
                }
            )

        manifest = {
            "schema_version": "tig.dataset/v1",
            "dataset_id": dataset_id,
            "name": name or dataset_id,
            "challenge_id": challenge_id,
            "challenge": challenge_name,
            "monorepo_commit": snapshot.commit,
            "instances": instances,
            "provenance": {
                "kind": "tig_generated",
                "generation": {
                    "seed": first_generation["seed"],
                    "start_nonce": start_nonce,
                    "count": count,
                    "track_id": canonical_track_id,
                    "benchmark_settings": generation_settings,
                },
            },
            "metadata": metadata or {},
        }
        atomic_write(
            write_dir / "dataset.json",
            canonical_json_bytes(manifest),
            require_same=True,
        )
    return output_dir / "dataset.json"


def load_dataset(path: Path | str) -> tuple[Path, dict[str, Any]]:
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / "dataset.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"Could not read dataset manifest {manifest_path}: {exc}") from exc
    if manifest.get("schema_version") != "tig.dataset/v1":
        raise DatasetError(f"Unsupported dataset schema in {manifest_path}")
    required_strings = (
        "dataset_id",
        "name",
        "challenge_id",
        "challenge",
        "monorepo_commit",
    )
    for field in required_strings:
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise DatasetError(
                f"Dataset manifest {manifest_path} is missing string field {field!r}"
            )
    challenge_id = normalize_challenge(manifest["challenge_id"])
    if manifest["challenge_id"] != challenge_id:
        raise DatasetError("Dataset manifests must use the stable cNNN challenge ID")
    if manifest["challenge"] != CPU_CHALLENGES[challenge_id]:
        raise DatasetError(
            f"Dataset challenge name does not match {challenge_id}: "
            f"{manifest['challenge']!r}"
        )
    if COMMIT_RE.fullmatch(manifest["monorepo_commit"]) is None:
        raise DatasetError("Dataset monorepo_commit must be a full lowercase Git SHA")
    if not isinstance(manifest.get("instances"), list) or not manifest["instances"]:
        raise DatasetError("Dataset manifest contains no instances")
    if not isinstance(manifest.get("provenance"), dict):
        raise DatasetError("Dataset provenance must be a JSON object")
    if not isinstance(manifest.get("metadata"), dict):
        raise DatasetError("Dataset metadata must be a JSON object")
    return manifest_path, manifest


def _run_helper(helper: Path, arguments: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [str(helper), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise DatasetError(f"Canonical instance helper failed: {detail}") from exc
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DatasetError("Canonical instance helper returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise DatasetError("Canonical instance helper returned a non-object")
    return value
