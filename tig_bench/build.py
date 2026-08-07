"""Content-addressed Rust helper builds against a pinned monorepo snapshot."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

from .catalog import Algorithm, CPU_CHALLENGES, normalize_challenge
from .source import SourceSnapshot, default_cache_dir


class BuildError(RuntimeError):
    """Raised when a native helper cannot be prepared."""


OBJECTIVE_SNIPPETS = {
    "c001": """let scaled = challenge.evaluate_solution(&solution)?;
    Ok(serde_json::json!(if scaled > 0 { 1 } else { 0 }))""",
    "c002": "Ok(serde_json::json!(challenge.evaluate_total_distance(&solution)?))",
    "c003": "Ok(serde_json::json!(challenge.evaluate_total_value(&solution)?))",
    "c007": "Ok(serde_json::json!(challenge.evaluate_makespan(&solution)?))",
    "c008": "Ok(serde_json::json!(challenge.evaluate_total_profit(&solution)?))",
}

OBJECTIVE_DIRECTIONS = {
    "c001": "maximize",
    "c002": "minimize",
    "c003": "maximize",
    "c007": "minimize",
    "c008": "maximize",
}

BASELINE_SNIPPETS = {
    "c001": 'let _ = challenge; Err(anyhow::anyhow!("No canonical baseline is exposed for c001"))',
    "c002": "Ok(challenge.compute_greedy_baseline()?)",
    "c003": "Ok(challenge.compute_greedy_baseline()?)",
    "c007": "Ok(challenge.compute_greedy_baseline()?)",
    "c008": "Ok(challenge.compute_baseline()?.0)",
}

PREPARE_SNIPPETS = {
    "c001": "Ok(challenge)",
    "c002": """let mut challenge = challenge;
    let fleet_size = challenge.fleet_size;
    challenge.fleet_size = u32::MAX as usize;
    let baseline = challenge.compute_greedy_baseline()?;
    challenge.greedy_baseline_total_distance = challenge.evaluate_total_distance(&baseline)? as u32;
    challenge.fleet_size = fleet_size;
    Ok(challenge)""",
    "c003": "Ok(challenge)",
    "c007": "Ok(challenge)",
    "c008": "Ok(challenge)",
}


class RustBuildManager:
    def __init__(self, cache_dir: Path | str | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.template_root = Path(__file__).with_name("templates")

    def instance_helper(
        self, snapshot: SourceSnapshot, challenge: str
    ) -> Path:
        challenge_id = normalize_challenge(challenge)
        challenge_name = CPU_CHALLENGES[challenge_id]
        template = (self.template_root / "instance_helper.rs").read_text(
            encoding="utf-8"
        )
        source = (
            template.replace("__CHALLENGE_MODULE__", challenge_name)
            .replace("__CHALLENGE_ID__", challenge_id)
            .replace("__OBJECTIVE_DIRECTION__", OBJECTIVE_DIRECTIONS[challenge_id])
            .replace("__OBJECTIVE_EXPRESSION__", OBJECTIVE_SNIPPETS[challenge_id])
            .replace("__BASELINE_EXPRESSION__", BASELINE_SNIPPETS[challenge_id])
            .replace("__PREPARE_EXPRESSION__", PREPARE_SNIPPETS[challenge_id])
        )
        manifest = self._instance_helper_manifest(snapshot, challenge_id)
        lockfile = snapshot.checkout / "Cargo.lock"
        environment_identity = self._build_environment_identity()
        # Target directories are shared by commit so dependencies compile once. Give
        # every generated crate a content-specific binary name so concurrent helper
        # builds cannot overwrite one another's release artifact.
        crate_name = "tig-instance-helper-" + self._identity(
            snapshot.commit,
            challenge_id,
            source,
            manifest,
            lockfile.read_bytes(),
            environment_identity,
        )[:16]
        manifest = manifest.replace(
            'name = "tig-instance-helper"', f'name = "{crate_name}"', 1
        )
        identity = self._identity(
            snapshot.commit,
            challenge_id,
            source,
            manifest,
            lockfile.read_bytes(),
            environment_identity,
        )
        build_dir = self.cache_dir / "builds" / "instance-helper" / identity
        binary = build_dir / "bin" / "tig-instance-helper"
        if binary.is_file():
            return binary

        source_dir = build_dir / "src"
        source_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "Cargo.toml").write_text(manifest, encoding="utf-8")
        (source_dir / "main.rs").write_text(source, encoding="utf-8")
        shutil.copy2(lockfile, build_dir / "Cargo.lock")

        command = [
            "cargo",
            "build",
            "--quiet",
            "--release",
            "--manifest-path",
            str(build_dir / "Cargo.toml"),
        ]
        target_dir = self._target_dir(snapshot)
        environment = os.environ.copy()
        environment["CARGO_TARGET_DIR"] = str(target_dir)
        try:
            subprocess.run(command, check=True, cwd=build_dir, env=environment)
        except FileNotFoundError as exc:
            raise BuildError("Cargo is required but was not found on PATH") from exc
        except subprocess.CalledProcessError as exc:
            raise BuildError(
                f"Could not build canonical {challenge_id} instance helper"
            ) from exc
        built_binary = target_dir / "release" / crate_name
        if not built_binary.is_file():
            raise BuildError(f"Cargo completed without producing {built_binary}")
        binary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built_binary, binary)
        return binary

    def solver_harness(
        self,
        snapshot: SourceSnapshot,
        *,
        challenge: str,
        algorithm: Algorithm | None = None,
        local: Path | str | None = None,
    ) -> tuple[Path, str | None]:
        challenge_id = normalize_challenge(challenge)
        challenge_name = CPU_CHALLENGES[challenge_id]
        if (algorithm is None) == (local is None):
            raise BuildError("Select exactly one monorepo or local algorithm")

        template = (self.template_root / "solver_harness.rs").read_text(
            encoding="utf-8"
        )
        local_hash: str | None = None
        if algorithm is not None:
            if algorithm.challenge_id != challenge_id:
                raise BuildError(
                    f"Algorithm {algorithm.id} is for {algorithm.challenge_id}, not {challenge_id}"
                )
            algorithm_import = (
                f"use tig_algorithms::{challenge_name}::{algorithm.id} as algorithm;"
            )
            algorithm_identity = algorithm.id
            manifest = self._solver_manifest(
                snapshot, challenge_id, include_algorithms=True
            )
        else:
            local_path = Path(local).expanduser().resolve()
            if not local_path.is_file():
                raise BuildError(f"Local algorithm does not exist: {local_path}")
            local_hash = self._local_source_hash(local_path)
            algorithm_import = (
                f"#[path = {json.dumps(str(local_path))}]\n"
                "mod algorithm;"
            )
            algorithm_identity = f"local-{local_hash}"
            manifest = self._solver_manifest(
                snapshot, challenge_id, include_algorithms=False
            )

        source = (
            template.replace("__CHALLENGE_MODULE__", challenge_name)
            .replace("__ALGORITHM_IMPORT__", algorithm_import)
        )
        lockfile = snapshot.checkout / "Cargo.lock"
        environment_identity = self._build_environment_identity()
        crate_name = "tig-solver-harness-" + self._identity(
            snapshot.commit,
            challenge_id,
            algorithm_identity,
            source,
            manifest,
            lockfile.read_bytes(),
            environment_identity,
        )[:16]
        manifest = manifest.replace(
            'name = "tig-solver-harness"', f'name = "{crate_name}"', 1
        )
        identity = self._identity(
            snapshot.commit,
            challenge_id,
            algorithm_identity,
            source,
            manifest,
            lockfile.read_bytes(),
            environment_identity,
        )
        build_dir = self.cache_dir / "builds" / "solver-harness" / identity
        binary = build_dir / "bin" / "tig-solver-harness"
        if binary.is_file():
            return binary, local_hash

        source_dir = build_dir / "src"
        source_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "Cargo.toml").write_text(manifest, encoding="utf-8")
        (source_dir / "main.rs").write_text(source, encoding="utf-8")
        shutil.copy2(lockfile, build_dir / "Cargo.lock")

        target_dir = self._target_dir(snapshot)
        environment = os.environ.copy()
        environment["CARGO_TARGET_DIR"] = str(target_dir)
        try:
            subprocess.run(
                [
                    "cargo",
                    "build",
                    "--quiet",
                    "--release",
                    "--manifest-path",
                    str(build_dir / "Cargo.toml"),
                ],
                check=True,
                cwd=build_dir,
                env=environment,
            )
        except FileNotFoundError as exc:
            raise BuildError("Cargo is required but was not found on PATH") from exc
        except subprocess.CalledProcessError as exc:
            label = algorithm.id if algorithm else str(local)
            raise BuildError(f"Could not build algorithm {label}") from exc

        built_binary = target_dir / "release" / crate_name
        if not built_binary.is_file():
            raise BuildError(f"Cargo completed without producing {built_binary}")
        binary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built_binary, binary)
        return binary, local_hash

    @staticmethod
    def _instance_helper_manifest(
        snapshot: SourceSnapshot, challenge_id: str
    ) -> str:
        challenge_path = json.dumps(str(snapshot.checkout / "tig-challenges"))
        structs_path = json.dumps(str(snapshot.checkout / "tig-structs"))
        return f"""[package]
name = "tig-instance-helper"
version = "0.1.0"
edition = "2021"

[dependencies]
anyhow = "1.0.81"
serde = {{ version = "1.0.196", features = ["derive"] }}
serde_json = "1.0.113"
tig-challenges = {{ path = {challenge_path}, features = ["{challenge_id}"] }}
tig-structs = {{ path = {structs_path} }}

[workspace]
"""

    @staticmethod
    def _solver_manifest(
        snapshot: SourceSnapshot,
        challenge_id: str,
        *,
        include_algorithms: bool,
    ) -> str:
        challenge_path = json.dumps(str(snapshot.checkout / "tig-challenges"))
        dependencies = f"""tig-challenges = {{ path = {challenge_path}, features = ["{challenge_id}", "hide_verification"] }}
"""
        if include_algorithms:
            algorithms_path = json.dumps(str(snapshot.checkout / "tig-algorithms"))
            dependencies += (
                f"tig-algorithms = {{ path = {algorithms_path}, "
                f'features = ["{challenge_id}"] }}\n'
            )
        return f"""[package]
name = "tig-solver-harness"
version = "0.1.0"
edition = "2021"

[dependencies]
ahash = "0.8.12"
anyhow = "1.0.81"
ndarray = "0.15.6"
rand = {{ version = "0.8.5", default-features = false, features = ["std_rng", "small_rng"] }}
rand_distr = "0.4.3"
serde = {{ version = "1.0.196", features = ["derive"] }}
serde_json = "1.0.113"
{dependencies}
[workspace]
"""

    @staticmethod
    def _local_source_hash(path: Path) -> str:
        digest = hashlib.sha256()
        # A Rust module can use sibling and nested source files regardless of
        # whether its entry point is named mod.rs. Conservatively hash the full
        # local module tree so dependency edits cannot reuse a stale executable.
        source_files = sorted(path.parent.rglob("*.rs"))
        for source_file in source_files:
            relative = source_file.relative_to(path.parent).as_posix().encode("utf-8")
            contents = source_file.read_bytes()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(len(contents).to_bytes(8, "big"))
            digest.update(contents)
        return digest.hexdigest()

    @staticmethod
    def _identity(*parts: object) -> str:
        digest = hashlib.sha256()
        for part in parts:
            if isinstance(part, bytes):
                value = part
            else:
                value = str(part).encode("utf-8")
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        return digest.hexdigest()

    def _target_dir(self, snapshot: SourceSnapshot) -> Path:
        # Cargo keys dependency artifacts by feature set and profile. Sharing a
        # target per monorepo revision avoids rebuilding the common dependency
        # graph for evaluator, submitted-algorithm, and local harnesses.
        return self.cache_dir / "build-targets" / snapshot.commit

    @staticmethod
    def _build_environment_identity() -> str:
        try:
            rustc = subprocess.run(
                ["rustc", "-vV"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise BuildError("A working Rust compiler is required") from exc
        return f"{platform.system()}\n{platform.machine()}\n{rustc}"
