#!/usr/bin/env python3
"""Run TIG vehicle-routing algorithms on Homberger-Gehring instance sets.

Every subfolder holding a config.json is one instance set (hg1000-rc2,
hg1000-r2, hg1000-r1, hg600-rc2); experiment.json holds what the sets share:
the algorithms, the solver budget and the monorepo commit. For each set the
script does these steps, skipping any whose output already exists (see
--force-*):
  1. initialise the kit's monorepo cache under data/cache (or --cache-dir) and
     check that its pinned commit is the one recorded in experiment.json;
  2. download the set's SINTEF archive into data/ and verify its SHA-256;
  3. extract the members listed in the set's config.json to <set>/data/instances;
  4. import them with `tig.py import --format solomon-vrptw` into <set>/data/dataset;
  5. run every algorithm in experiment.json with the configured solver timeout,
     saving the final solution of each instance under <set>/runs/<label>/;
  6. hand over to analyze.py, which writes <set>/results/ (CSV, JSON, figure).

    python3 run_experiment.py                 # every set
    python3 run_experiment.py hg1000-r2       # one or more named sets

The experiment keeps its own cache so the monorepo commit it runs at is the
one recorded in experiment.json, whatever the default cache is pinned to, and
so that `tig.py source sync` never has to touch the default cache (which would
move every other dataset you generate afterwards). Pass --cache-dir to use
another cache, for example the default one.

Only <set>/results/ is meant to be committed. data/, <set>/data/ and
<set>/runs/ are ignored by Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EXPERIMENT_PATH = HERE / "experiment.json"
SHARED_DATA = HERE / "data"
DEFAULT_CACHE = SHARED_DATA / "cache"


def log(message: str) -> None:
    print(f"[experiment] {message}", flush=True)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def relative(path: Path) -> str:
    try:
        return str(path.relative_to(HERE))
    except ValueError:
        return str(path)


def discover_sets() -> list[Path]:
    return sorted(path.parent for path in HERE.glob("*/config.json"))


def resolve_set(name: str) -> Path:
    for candidate in (Path(name), HERE / name):
        if (candidate / "config.json").is_file():
            return candidate.resolve()
    raise SystemExit(f"{name} is not an instance set: expected a folder holding config.json under {HERE}")


def download(collection: dict, force: bool) -> Path:
    url = collection["url"]
    archive = SHARED_DATA / Path(url).name
    SHARED_DATA.mkdir(parents=True, exist_ok=True)
    if archive.exists() and not force:
        log(f"using existing archive {relative(archive)}")
    else:
        log(f"downloading {url}")
        # SINTEF answers 403 to Python's default User-Agent, so send a generic one.
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (tig-evaluation-kit experiment)"})
        with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    expected = collection["zip_sha256"]
    if digest != expected:
        raise SystemExit(
            f"SHA-256 mismatch for {archive}: got {digest}, expected {expected}. "
            "Delete the file to download it again, or update the set's config.json if SINTEF "
            "republished the archive."
        )
    return archive


def extract(set_dir: Path, collection: dict, archive: Path) -> Path:
    instances = set_dir / "data" / "instances"
    instances.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        by_name = {Path(name).name.upper(): name for name in bundle.namelist()}
        for member in collection["members"]:
            source = by_name.get(member.upper())
            if source is None:
                raise SystemExit(f"{member} is not in {archive.name}")
            (instances / member).write_bytes(bundle.read(source))
    log(f"extracted {len(collection['members'])} instances to {relative(instances)}")
    return instances


def tig(cache_dir: Path, *arguments: str, capture: bool = False) -> str:
    command = [sys.executable, str(REPO / "tig.py"), *arguments, "--cache-dir", str(cache_dir)]
    log(" ".join(command[1:]))
    completed = subprocess.run(command, check=True, cwd=REPO, capture_output=capture, text=True)
    return completed.stdout if capture else ""


def ensure_source(experiment: dict, cache_dir: Path, sync: bool) -> str:
    """Initialise or sync the cache and return the commit the runs will use."""
    status = json.loads(tig(cache_dir, "source", "status", "--json", capture=True))
    if sync or not status.get("initialized"):
        tig(cache_dir, "source", "sync")
        status = json.loads(tig(cache_dir, "source", "status", "--json", capture=True))
    commit = status["commit"]
    expected = experiment["monorepo_commit"]
    if commit != expected:
        log(f"WARNING: cache {cache_dir} is pinned to {commit}, experiment.json records {expected}; "
            "results will be produced at the pinned commit (pass --sync to move the cache to current main)")
    else:
        log(f"monorepo commit {commit}")
    return commit


def import_dataset(set_dir: Path, config: dict, experiment: dict, instances: Path, cache_dir: Path, commit: str, force: bool) -> Path:
    dataset = set_dir / "data" / "dataset"
    manifest = dataset / "dataset.json"
    if manifest.exists() and not force:
        imported_at = load_json(manifest).get("monorepo_commit")
        if imported_at == commit:
            log(f"using existing dataset {relative(dataset)}")
            return dataset
        log(f"dataset was imported at {imported_at}, re-importing at {commit}")
    if dataset.exists():
        shutil.rmtree(dataset)
    tig(
        cache_dir,
        "import",
        "--challenge", experiment["challenge"],
        "--input", str(instances),
        "--format", config["collection"]["format"],
        "--name", config["dataset_name"],
        "--out", str(dataset),
    )
    return dataset


def expected_results(experiment: dict, dataset: Path) -> int:
    manifest = load_json(dataset / "dataset.json")
    return len(manifest["instances"]) * int(experiment["repetitions"])


def run_algorithm(set_dir: Path, experiment: dict, algorithm: dict, dataset: Path, cache_dir: Path, commit: str, force: bool) -> Path:
    output = set_dir / "runs" / algorithm["label"]
    results = output / "results.jsonl"
    wanted = expected_results(experiment, dataset)
    if force and output.exists():
        shutil.rmtree(output)
    if results.exists():
        records = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines() if line.strip()]
        stale = {r["algorithm"]["monorepo_commit"] for r in records} - {commit}
        if stale:
            raise SystemExit(
                f"{relative(output)} holds results from monorepo commit {', '.join(sorted(stale))}, "
                f"not {commit}; rerun with --force-run to replace them"
            )
        if len(records) >= wanted:
            log(f"{set_dir.name}/{algorithm['label']}: {len(records)} results already present, skipping")
            return output
    arguments = [
        "run",
        "--dataset", str(dataset),
        "--algorithm", algorithm["id"],
        "--timeout", str(experiment["timeout_seconds"]),
        "--workers", str(experiment["workers"]),
        "--repetitions", str(experiment["repetitions"]),
        "--algorithm-seed", experiment["algorithm_seed"],
        "--save-solution",
        "--out", str(output),
    ]
    if algorithm.get("hyperparameters"):
        arguments += ["--hyperparameters", json.dumps(algorithm["hyperparameters"])]
    if output.exists():
        log(f"{set_dir.name}/{algorithm['label']}: resuming interrupted run")
        arguments.append("--resume")
    tig(cache_dir, *arguments)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sets", nargs="*", metavar="SET",
                        help="instance-set folders to run (default: every subfolder holding a config.json)")
    parser.add_argument("--force-download", action="store_true", help="download the archives again")
    parser.add_argument("--force-import", action="store_true", help="re-import the datasets")
    parser.add_argument("--force-run", action="store_true", help="discard existing runs and solve again")
    parser.add_argument("--skip-analysis", action="store_true", help="do not run analyze.py afterwards")
    parser.add_argument("--only", metavar="LABEL", action="append", help="run only this algorithm label (repeatable)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE,
                        help=f"kit cache to build and run from (default: {DEFAULT_CACHE.relative_to(HERE)})")
    parser.add_argument("--sync", action="store_true", help="move the cache to current monorepo main before running")
    options = parser.parse_args(argv)

    experiment = load_json(EXPERIMENT_PATH)
    sets = [resolve_set(name) for name in options.sets] or discover_sets()
    if not sets:
        raise SystemExit(f"no instance sets found under {HERE}")
    commit = ensure_source(experiment, options.cache_dir, options.sync)
    for set_dir in sets:
        config = load_json(set_dir / "config.json")
        log(f"=== {set_dir.name}: {config['title']}")
        archive = download(config["collection"], options.force_download)
        instances = extract(set_dir, config["collection"], archive)
        dataset = import_dataset(set_dir, config, experiment, instances, options.cache_dir, commit, options.force_import)
        for algorithm in experiment["algorithms"]:
            if options.only and algorithm["label"] not in options.only:
                continue
            run_algorithm(set_dir, experiment, algorithm, dataset, options.cache_dir, commit, options.force_run)
    if options.skip_analysis:
        return 0
    sys.path.insert(0, str(HERE))
    import analyze  # noqa: E402  (sibling module)

    return analyze.main([str(set_dir) for set_dir in sets])


if __name__ == "__main__":
    sys.exit(main())
