"""Algorithm discovery from the authoritative tig-monorepo source tree."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Iterable


CPU_CHALLENGES = {
    "c001": "satisfiability",
    "c002": "vehicle_routing",
    "c003": "knapsack",
    "c007": "job_scheduling",
    "c008": "energy_arbitrage",
}

CHALLENGE_ALIASES = {
    alias: challenge_id
    for challenge_id, name in CPU_CHALLENGES.items()
    for alias in (challenge_id, name)
}

MODULE_RE = re.compile(r"^\s*pub\s+mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$")
EXPORT_RE = re.compile(
    r"^\s*pub\s+use\s+([A-Za-z_][A-Za-z0-9_]*)\s+as\s+"
    r"(c[0-9]{3}_a[0-9]{3})\s*;\s*$"
)


class CatalogError(RuntimeError):
    """Raised when the canonical algorithm catalog is invalid or ambiguous."""


@dataclass(frozen=True, order=True)
class Algorithm:
    challenge_id: str
    challenge: str
    id: str
    name: str
    source: str

    def to_json(self) -> dict[str, str]:
        return asdict(self)


class AlgorithmCatalog:
    def __init__(self, monorepo: Path | str) -> None:
        self.monorepo = Path(monorepo)
        self.algorithms_root = self.monorepo / "tig-algorithms" / "src"

    def list(self, challenge: str | None = None) -> list[Algorithm]:
        challenge_ids: Iterable[str]
        if challenge is None:
            challenge_ids = CPU_CHALLENGES
        else:
            challenge_ids = [normalize_challenge(challenge)]

        algorithms: list[Algorithm] = []
        for challenge_id in challenge_ids:
            algorithms.extend(self._read_challenge(challenge_id))
        return sorted(algorithms, key=lambda algorithm: algorithm.id)

    def resolve(self, selector: str, challenge: str | None = None) -> Algorithm:
        candidates = [
            algorithm
            for algorithm in self.list(challenge)
            if selector in (algorithm.id, algorithm.name)
        ]
        if not candidates:
            scope = f" for {challenge}" if challenge else ""
            raise CatalogError(f"Unknown algorithm {selector!r}{scope}")
        if len(candidates) > 1:
            ids = ", ".join(algorithm.id for algorithm in candidates)
            raise CatalogError(
                f"Algorithm name {selector!r} is ambiguous; use one of: {ids}"
            )
        return candidates[0]

    def _read_challenge(self, challenge_id: str) -> list[Algorithm]:
        challenge_name = CPU_CHALLENGES[challenge_id]
        module_path = self.algorithms_root / challenge_name / "mod.rs"
        try:
            lines = module_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise CatalogError(
                f"Could not read canonical algorithm module {module_path}: {exc}"
            ) from exc

        declared_modules = {
            match.group(1)
            for line in lines
            if (match := MODULE_RE.fullmatch(line)) is not None
        }
        algorithms: list[Algorithm] = []
        seen_ids: set[str] = set()
        for line in lines:
            match = EXPORT_RE.fullmatch(line)
            if match is None:
                continue
            module_name, algorithm_id = match.groups()
            if not algorithm_id.startswith(f"{challenge_id}_"):
                raise CatalogError(
                    f"{module_path} exports {algorithm_id} under {challenge_id}"
                )
            if module_name not in declared_modules:
                raise CatalogError(
                    f"{module_path} exports undeclared module {module_name}"
                )
            if algorithm_id in seen_ids:
                raise CatalogError(f"Duplicate algorithm ID {algorithm_id} in {module_path}")
            seen_ids.add(algorithm_id)

            source = self._module_source(challenge_name, module_name)
            algorithms.append(
                Algorithm(
                    challenge_id=challenge_id,
                    challenge=challenge_name,
                    id=algorithm_id,
                    name=module_name,
                    source=str(source.relative_to(self.monorepo)),
                )
            )
        return algorithms

    def _module_source(self, challenge: str, module_name: str) -> Path:
        module_root = self.algorithms_root / challenge
        candidates = [module_root / f"{module_name}.rs", module_root / module_name / "mod.rs"]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise CatalogError(
            f"Canonical module {challenge}::{module_name} has no source file"
        )


def normalize_challenge(challenge: str) -> str:
    try:
        return CHALLENGE_ALIASES[challenge]
    except KeyError as exc:
        supported = ", ".join(CPU_CHALLENGES)
        raise CatalogError(
            f"Unsupported CPU challenge {challenge!r}; expected one of: {supported}"
        ) from exc
