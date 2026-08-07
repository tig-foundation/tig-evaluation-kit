#!/usr/bin/env python3

"""Command-line interface for the monorepo-authoritative TIG benchmark runner."""

from __future__ import annotations

import argparse
import json
import logging
from typing import Callable

from tig_bench import (
    AcademicImportError,
    AlgorithmCatalog,
    BuildError,
    CatalogError,
    DatasetError,
    RunError,
    SourceError,
    SourceManager,
    generate_exact_dataset,
    import_dataset,
    parse_hyperparameters,
    parse_snapshot_times,
    run_dataset,
)


logger = logging.getLogger("tig")

CHALLENGE_CHOICES = [
    "c001",
    "satisfiability",
    "c002",
    "vehicle_routing",
    "c003",
    "knapsack",
    "c007",
    "job_scheduling",
    "c008",
    "energy_arbitrage",
]

FORMAT_CHOICES = [
    "auto",
    "canonical-json",
    "dimacs-cnf",
    "solomon-vrptw",
    "tig-qkp",
    "brandimarte-fjsp",
    "classic-jsp",
]

KNOWN_ERRORS = (
    AcademicImportError,
    BuildError,
    CatalogError,
    DatasetError,
    RunError,
    SourceError,
    ValueError,
)


def setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _source_manager(args: argparse.Namespace) -> SourceManager:
    return SourceManager(cache_dir=args.cache_dir)


def source_status(args: argparse.Namespace) -> None:
    status = _source_manager(args).status()
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    elif not status["initialized"]:
        print(f"Monorepo cache: not initialized ({status['cache_dir']})")
        print("It will be initialized automatically on first use.")
    else:
        print(f"Monorepo commit: {status['commit']}")
        print(f"Source: {status['source']}")
        print(f"Clean checkout: {status['checkout']}")
        print(f"Last synced: {status.get('last_synced_at') or 'unknown'}")


def source_sync(args: argparse.Namespace) -> None:
    snapshot = _source_manager(args).sync()
    if args.json:
        print(json.dumps(snapshot.to_json(), indent=2, sort_keys=True))
    else:
        print(f"Synced tig-monorepo main at {snapshot.commit}")
        print(f"Clean checkout: {snapshot.checkout}")


def list_algorithms(args: argparse.Namespace) -> None:
    snapshot = _source_manager(args).ensure()
    algorithms = AlgorithmCatalog(snapshot.checkout).list(args.challenge)
    if args.json:
        print(
            json.dumps(
                {
                    "monorepo_commit": snapshot.commit,
                    "algorithms": [algorithm.to_json() for algorithm in algorithms],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    print(f"tig-monorepo main: {snapshot.commit}")
    if not algorithms:
        print("No submitted algorithms are exported for this challenge on main.")
    for algorithm in algorithms:
        print(f"{algorithm.id}\t{algorithm.name}\t{algorithm.challenge}")


def generate_exact(args: argparse.Namespace) -> None:
    manifest = generate_exact_dataset(
        challenge=args.challenge,
        track=args.track,
        count=args.count,
        seed=args.seed,
        start_nonce=args.start_nonce,
        name=args.name,
        metadata=parse_hyperparameters(args.metadata, label="Dataset metadata"),
        output=args.out,
        cache_dir=args.cache_dir,
    )
    print(f"Generated dataset manifest: {manifest}")


def import_academic(args: argparse.Namespace) -> None:
    manifest = import_dataset(
        challenge=args.challenge,
        inputs=args.input,
        format_name=args.format,
        name=args.name,
        metadata=parse_hyperparameters(args.metadata, label="Dataset metadata"),
        output=args.out,
        cache_dir=args.cache_dir,
    )
    print(f"Imported dataset manifest: {manifest}")


def run_exact(args: argparse.Namespace) -> None:
    output_dir = run_dataset(
        dataset=args.dataset,
        algorithm_selector=args.algorithm,
        local=args.local,
        baseline=args.baseline,
        hyperparameters=parse_hyperparameters(args.hyperparameters),
        algorithm_seed=args.algorithm_seed,
        timeout_seconds=args.timeout,
        workers=args.workers,
        repetitions=args.repetitions,
        output=args.out,
        save_solution=args.save_solution,
        save_all_solutions=args.save_all_solutions,
        snapshot_interval_seconds=args.snapshot_interval,
        snapshot_times_seconds=parse_snapshot_times(args.snapshot_times),
        resume=args.resume,
        cache_dir=args.cache_dir,
    )
    print(f"Run results: {output_dir / 'results.jsonl'}")


def _add_cache_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Advanced: override the managed cache directory",
    )


def _set_handler(
    parser: argparse.ArgumentParser, handler: Callable[[argparse.Namespace], None]
) -> None:
    parser.set_defaults(handler=handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run TIG algorithms reproducibly on TIG or academic instances"
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Logging level",
    )
    commands = parser.add_subparsers(
        dest="command", required=True, help="Available commands"
    )

    source_parser = commands.add_parser(
        "source", help="Manage the clean pinned tig-monorepo source"
    )
    source_commands = source_parser.add_subparsers(
        dest="source_command", required=True
    )
    status_parser = source_commands.add_parser(
        "status", help="Show the pinned monorepo revision"
    )
    _add_cache_option(status_parser)
    status_parser.add_argument("--json", action="store_true", help="Print JSON")
    _set_handler(status_parser, source_status)
    sync_parser = source_commands.add_parser(
        "sync", help="Initialize or explicitly update to current monorepo main"
    )
    _add_cache_option(sync_parser)
    sync_parser.add_argument("--json", action="store_true", help="Print JSON")
    _set_handler(sync_parser, source_sync)

    algorithms_parser = commands.add_parser(
        "algorithms", help="Discover algorithms exported by monorepo main"
    )
    algorithm_commands = algorithms_parser.add_subparsers(
        dest="algorithms_command", required=True
    )
    list_parser = algorithm_commands.add_parser(
        "list", help="List supported CPU algorithms"
    )
    _add_cache_option(list_parser)
    list_parser.add_argument(
        "--challenge",
        choices=CHALLENGE_CHOICES,
        default=None,
        help="Limit the catalog to one CPU challenge",
    )
    list_parser.add_argument("--json", action="store_true", help="Print JSON")
    _set_handler(list_parser, list_algorithms)

    generate_parser = commands.add_parser(
        "generate", help="Generate a deterministic dataset with canonical TIG code"
    )
    generate_parser.add_argument(
        "--challenge",
        required=True,
        choices=CHALLENGE_CHOICES,
        help="CPU challenge ID or name",
    )
    generate_parser.add_argument(
        "--track",
        required=True,
        help="Canonical TIG track, for example n_nodes=1000",
    )
    generate_parser.add_argument(
        "--count", type=int, default=1, help="Number of instances (default: 1)"
    )
    generate_parser.add_argument(
        "--seed",
        default="default_seed",
        help="Dataset generation seed used with --track (default: default_seed)",
    )
    generate_parser.add_argument(
        "--start-nonce",
        type=int,
        default=0,
        help="Index of the first deterministic instance (default: 0)",
    )
    generate_parser.add_argument("--out", default=None, help="Output dataset directory")
    generate_parser.add_argument("--name", default=None, help="Dataset display name")
    generate_parser.add_argument(
        "--metadata", help="Dataset metadata as a JSON object or JSON file"
    )
    _add_cache_option(generate_parser)
    _set_handler(generate_parser, generate_exact)

    import_parser = commands.add_parser(
        "import", help="Normalize academic or legacy text instances"
    )
    import_parser.add_argument(
        "--challenge",
        required=True,
        choices=CHALLENGE_CHOICES,
        help="CPU challenge ID or name",
    )
    import_parser.add_argument(
        "--input", nargs="+", required=True, help="Instance files or directories"
    )
    import_parser.add_argument(
        "--format",
        default="auto",
        choices=FORMAT_CHOICES,
        help="Input format (default: conservative auto-detection)",
    )
    import_parser.add_argument("--name", default=None, help="Dataset display name")
    import_parser.add_argument(
        "--metadata", help="Dataset metadata as a JSON object or JSON file"
    )
    import_parser.add_argument("--out", default=None, help="Output dataset directory")
    _add_cache_option(import_parser)
    _set_handler(import_parser, import_academic)

    run_parser = commands.add_parser(
        "run", help="Run a canonical or local algorithm on a versioned dataset"
    )
    run_parser.add_argument(
        "--dataset", required=True, help="Path to dataset.json or its directory"
    )
    solver_selection = run_parser.add_mutually_exclusive_group(required=True)
    solver_selection.add_argument(
        "--algorithm", help="Stable algorithm ID or canonical friendly name"
    )
    solver_selection.add_argument("--local", help="Path to a local Rust module")
    solver_selection.add_argument(
        "--baseline", action="store_true", help="Run the canonical TIG baseline"
    )
    run_parser.add_argument(
        "--hyperparameters", help="Inline JSON object or path to a JSON file"
    )
    run_parser.add_argument(
        "--algorithm-seed",
        default="default_seed",
        help="Base seed for algorithm randomness (default: default_seed)",
    )
    run_parser.add_argument(
        "--timeout", type=float, default=60.0, help="Solver-only timeout per work item"
    )
    run_parser.add_argument(
        "--workers", type=int, default=1, help="Parallel instance workers (default: 1)"
    )
    run_parser.add_argument(
        "--repetitions", type=int, default=1, help="Runs per instance (default: 1)"
    )
    run_parser.add_argument("--out", default=None, help="Output run directory")
    solution_capture = run_parser.add_mutually_exclusive_group()
    solution_capture.add_argument(
        "--save-solution", action="store_true", help="Retain the final callback solution"
    )
    solution_capture.add_argument(
        "--save-all-solutions",
        action="store_true",
        help="Retain every save_solution callback",
    )
    snapshot_selection = run_parser.add_mutually_exclusive_group()
    snapshot_selection.add_argument(
        "--snapshot-interval",
        type=float,
        default=None,
        help="Retain the latest solution at this wall-clock interval",
    )
    snapshot_selection.add_argument(
        "--snapshot-times",
        default=None,
        help="Comma-separated solver wall-clock snapshot times",
    )
    run_parser.add_argument(
        "--resume", action="store_true", help="Resume an interrupted matching run"
    )
    _add_cache_option(run_parser)
    _set_handler(run_parser, run_exact)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.log_level)
    try:
        args.handler(args)
    except KNOWN_ERRORS as error:
        logger.error("%s", error)
        return 1
    except Exception as error:
        logger.exception("Fatal error: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
