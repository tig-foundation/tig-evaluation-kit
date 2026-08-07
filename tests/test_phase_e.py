from inspect import signature
from pathlib import Path
import unittest

import tig


class RepositoryAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent.parent

    def test_no_standalone_copied_challenge_crate_remains(self) -> None:
        self.assertFalse((self.root / "Cargo.toml").exists())
        self.assertFalse((self.root / "Cargo.lock").exists())
        self.assertFalse((self.root / "src").exists())

    def test_cli_exposes_only_authoritative_commands(self) -> None:
        parser = tig.build_parser()
        subparsers = next(
            action
            for action in parser._actions
            if action.__class__.__name__ == "_SubParsersAction"
        )
        self.assertEqual(
            set(subparsers.choices),
            {"source", "algorithms", "generate", "import", "run"},
        )

        generate_parser = subparsers.choices["generate"]
        generation_flags = {
            option
            for action in generate_parser._actions
            for option in action.option_strings
        }
        self.assertNotIn("--rand-hash", generation_flags)
        self.assertNotIn("--settings", generation_flags)
        self.assertIn("--track", generation_flags)

        parsers = [parser]
        for current in parsers:
            for action in current._actions:
                if action.__class__.__name__ == "_SubParsersAction":
                    parsers.extend(action.choices.values())
        for current in parsers:
            flags = {
                option
                for action in current._actions
                for option in action.option_strings
            }
            self.assertNotIn("--source", flags)

    def test_migration_and_local_module_guides_exist(self) -> None:
        self.assertTrue((self.root / "docs" / "migration.md").is_file())
        self.assertTrue((self.root / "docs" / "local-algorithms.md").is_file())
        self.assertTrue((self.root / "examples" / "local" / "template.rs").is_file())

    def test_public_api_has_no_monorepo_source_override(self) -> None:
        for entry_point in (
            tig.SourceManager,
            tig.generate_exact_dataset,
            tig.import_dataset,
            tig.run_dataset,
        ):
            self.assertNotIn("source", signature(entry_point).parameters)

    def test_local_datasets_are_ignored(self) -> None:
        ignore_rules = (self.root / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/datasets/", ignore_rules)


if __name__ == "__main__":
    unittest.main()
