from pathlib import Path
import subprocess
import tempfile
import unittest

from tig_bench.source import DEFAULT_MONOREPO_URL, SourceManager


class SourceManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        root = Path(self.temporary_dir.name)
        self.remote = root / "remote"
        self.cache = root / "cache"
        self.remote.mkdir()
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        self.write_catalog("first_solver", "c002_a001")
        self.git("add", ".")
        self.git("commit", "-m", "initial")

    def tearDown(self) -> None:
        self.temporary_dir.cleanup()

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.remote,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def write_catalog(self, name: str, algorithm_id: str) -> None:
        module_root = self.remote / "tig-algorithms" / "src" / "vehicle_routing"
        module_root.mkdir(parents=True, exist_ok=True)
        module_path = module_root / "mod.rs"
        existing = module_path.read_text(encoding="utf-8") if module_path.exists() else ""
        module_path.write_text(
            existing + f"pub mod {name};\npub use {name} as {algorithm_id};\n",
            encoding="utf-8",
        )
        (module_root / f"{name}.rs").write_text("// test algorithm\n", encoding="utf-8")

    def test_first_use_is_clean_and_updates_only_on_sync(self) -> None:
        manager = SourceManager._from_test_repository(self.cache, self.remote)
        first = manager.ensure()
        first_commit = self.git("rev-parse", "HEAD")
        self.assertEqual(first.commit, first_commit)
        self.assertEqual(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=first.checkout,
                check=True,
                capture_output=True,
                text=True,
            ).stdout,
            "",
        )

        # An uncommitted source edit never appears in the clean checkout.
        dirty_file = self.remote / "uncommitted.txt"
        dirty_file.write_text("do not copy\n", encoding="utf-8")
        self.assertFalse((first.checkout / "uncommitted.txt").exists())

        # A later main commit is invisible until the explicit sync operation.
        dirty_file.unlink()
        self.write_catalog("second_solver", "c002_a002")
        self.git("add", ".")
        self.git("commit", "-m", "second")
        second_commit = self.git("rev-parse", "HEAD")

        still_first = manager.ensure()
        self.assertEqual(still_first.commit, first_commit)

        second = manager.sync()
        self.assertEqual(second.commit, second_commit)
        self.assertNotEqual(second.checkout, first.checkout)
        self.assertTrue(
            (
                second.checkout
                / "tig-algorithms"
                / "src"
                / "vehicle_routing"
                / "second_solver.rs"
            ).exists()
        )

    def test_dirty_managed_checkout_is_recreated(self) -> None:
        manager = SourceManager._from_test_repository(self.cache, self.remote)
        snapshot = manager.ensure()
        marker = snapshot.checkout / "untracked.txt"
        marker.write_text("dirty\n", encoding="utf-8")

        repaired = manager.ensure()

        self.assertEqual(repaired.commit, snapshot.commit)
        self.assertFalse(marker.exists())
        self.assertEqual(repaired.checkout, snapshot.checkout)

    def test_status_does_not_initialize_cache(self) -> None:
        manager = SourceManager(cache_dir=self.cache)
        status = manager.status()
        self.assertFalse(status["initialized"])
        self.assertEqual(status["source"], DEFAULT_MONOREPO_URL)
        self.assertFalse(manager.mirror.exists())


if __name__ == "__main__":
    unittest.main()


class GitInvocationTests(unittest.TestCase):
    def test_git_commands_skip_lfs_smudge(self) -> None:
        from unittest.mock import patch

        with patch("tig_bench.source.subprocess.run") as run:
            run.return_value.stdout = "ok\n"
            SourceManager._git(["status"])
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment.get("GIT_LFS_SKIP_SMUDGE"), "1")
