"""Clean, pinned tig-monorepo source management."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows retains the atomic install checks.
    fcntl = None


DEFAULT_MONOREPO_URL = "https://github.com/tig-foundation/tig-monorepo"
DEFAULT_REF = "main"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class SourceError(RuntimeError):
    """Raised when a clean monorepo snapshot cannot be prepared."""


@dataclass(frozen=True)
class SourceSnapshot:
    commit: str
    checkout: Path
    source: str
    ref: str = DEFAULT_REF

    def to_json(self) -> dict[str, str]:
        data = asdict(self)
        data["checkout"] = str(self.checkout)
        return data


CACHE_DIR_ENV = "TIG_EVALUATION_KIT_CACHE_DIR"
LEGACY_CACHE_DIR_ENV = "TIG_CHALLENGES_CACHE_DIR"
CACHE_DIR_NAME = "tig-evaluation-kit"
LEGACY_CACHE_DIR_NAME = "tig-challenges"


def default_cache_dir() -> Path:
    """Managed cache location, honouring the repository's former name."""
    for variable in (CACHE_DIR_ENV, LEGACY_CACHE_DIR_ENV):
        explicit = os.environ.get(variable)
        if explicit:
            return Path(explicit).expanduser()

    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    cache_root = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
    preferred = cache_root / CACHE_DIR_NAME
    legacy = cache_root / LEGACY_CACHE_DIR_NAME
    if not preferred.exists() and legacy.is_dir():
        return legacy
    return preferred


class SourceManager:
    """Maintains a bare mirror and immutable clean checkouts by commit SHA."""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self._initial_source = DEFAULT_MONOREPO_URL
        self.source_root = self.cache_dir / "sources" / "tig-monorepo"
        self.mirror = self.source_root / "mirror.git"
        self.checkouts = self.source_root / "checkouts"
        self.state_path = self.source_root / "state.json"

    @classmethod
    def _from_test_repository(
        cls, cache_dir: Path | str, source: Path | str
    ) -> "SourceManager":
        """Create a manager backed by a test fixture instead of the official repo."""
        manager = cls(cache_dir=cache_dir)
        manager._initial_source = str(source)
        return manager

    def status(self) -> dict[str, object]:
        if not self.mirror.exists():
            return {
                "initialized": False,
                "cache_dir": str(self.cache_dir),
                "source": self._initial_source,
            }

        commit = self._resolve_ref(DEFAULT_REF)
        state = self._read_state()
        checkout = self.checkouts / commit
        return {
            "initialized": True,
            "cache_dir": str(self.cache_dir),
            "source": self._origin_url(),
            "ref": DEFAULT_REF,
            "commit": commit,
            "checkout": str(checkout),
            "checkout_ready": self._checkout_is_clean(checkout, commit),
            "last_synced_at": state.get("last_synced_at"),
        }

    def ensure(self, ref: str = DEFAULT_REF) -> SourceSnapshot:
        """Return a clean snapshot, initializing from main on first use."""
        with self._exclusive_source_lock():
            if not self.mirror.exists():
                self._sync_unlocked()

            commit = self._resolve_ref(ref)
            checkout = self._ensure_checkout(commit)
            return SourceSnapshot(
                commit=commit,
                checkout=checkout,
                source=self._origin_url(),
                ref=ref,
            )

    def sync(self) -> SourceSnapshot:
        """Initialize or explicitly advance the cached main reference."""
        with self._exclusive_source_lock():
            return self._sync_unlocked()

    def _sync_unlocked(self) -> SourceSnapshot:
        self.source_root.mkdir(parents=True, exist_ok=True)
        if not self.mirror.exists():
            self._clone_mirror(self._initial_source)
        else:
            self._git(
                [
                    f"--git-dir={self.mirror}",
                    "fetch",
                    "--prune",
                    "origin",
                    "+refs/heads/main:refs/heads/main",
                ]
            )

        commit = self._resolve_ref(DEFAULT_REF)
        checkout = self._ensure_checkout(commit)
        self._write_state(
            {
                "schema_version": 1,
                "source": self._origin_url(),
                "ref": DEFAULT_REF,
                "commit": commit,
                "last_synced_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return SourceSnapshot(
            commit=commit,
            checkout=checkout,
            source=self._origin_url(),
        )

    @contextmanager
    def _exclusive_source_lock(self):
        self.source_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.source_root / ".lock"
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _clone_mirror(self, source: str) -> None:
        parent = self.mirror.parent
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="mirror-", dir=parent
        ) as temporary_dir:
            temporary_mirror = Path(temporary_dir) / "repository.git"
            self._git(["clone", "--mirror", source, str(temporary_mirror)])
            try:
                temporary_mirror.replace(self.mirror)
            except OSError as exc:
                if self.mirror.exists():
                    return
                raise SourceError(f"Could not install monorepo mirror: {exc}") from exc

    def _ensure_checkout(self, commit: str) -> Path:
        checkout = self.checkouts / commit
        if self._checkout_is_clean(checkout, commit):
            return checkout

        if checkout.exists():
            shutil.rmtree(checkout)
        self.checkouts.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(
            prefix=f"{commit[:12]}-", dir=self.checkouts
        ) as temporary_dir:
            temporary_checkout = Path(temporary_dir) / "checkout"
            self._git(
                [
                    "clone",
                    "--shared",
                    "--no-checkout",
                    str(self.mirror),
                    str(temporary_checkout),
                ]
            )
            self._git(
                ["checkout", "--detach", commit],
                cwd=temporary_checkout,
            )
            temporary_checkout.replace(checkout)

        if not self._checkout_is_clean(checkout, commit):
            raise SourceError(f"Prepared checkout is not clean at {commit}")
        return checkout

    def _checkout_is_clean(self, checkout: Path, commit: str) -> bool:
        if not (checkout / ".git").exists():
            return False
        try:
            head = self._git(["rev-parse", "HEAD"], cwd=checkout).strip()
            status = self._git(
                ["status", "--porcelain", "--untracked-files=all"], cwd=checkout
            ).strip()
        except SourceError:
            return False
        return head == commit and not status

    def _resolve_ref(self, ref: str) -> str:
        candidates = [ref]
        if ref == DEFAULT_REF:
            candidates = ["refs/heads/main", "main"]
        for candidate in candidates:
            try:
                commit = self._git(
                    [f"--git-dir={self.mirror}", "rev-parse", f"{candidate}^{{commit}}"]
                ).strip()
            except SourceError:
                continue
            if COMMIT_RE.fullmatch(commit):
                return commit
        raise SourceError(
            f"Could not resolve monorepo ref {ref!r}. Run `python3 tig.py source sync`."
        )

    def _origin_url(self) -> str:
        return self._git(
            [f"--git-dir={self.mirror}", "remote", "get-url", "origin"]
        ).strip()

    def _read_state(self) -> dict[str, object]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _write_state(self, state: dict[str, object]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.state_path.parent,
            prefix="state-",
            suffix=".json",
            delete=False,
        ) as temporary_file:
            json.dump(state, temporary_file, indent=2, sort_keys=True)
            temporary_file.write("\n")
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, self.state_path)

    @staticmethod
    def _git(args: Sequence[str], cwd: Path | None = None) -> str:
        # Large files tracked with Git LFS (prebuilt algorithm archives) are not
        # needed by the runner, and the shared mirror cannot serve them. Keep the
        # pointer files so checkouts succeed with or without git-lfs installed.
        environment = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
        except FileNotFoundError as exc:
            raise SourceError("Git is required but was not found on PATH") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise SourceError(f"Git command failed: {detail}") from exc
        return result.stdout
