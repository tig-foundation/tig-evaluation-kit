#!/usr/bin/env python3
"""Run TIG vehicle-routing algorithms on the Homberger-Gehring 1000-customer RC2 set.

Steps (each is skipped when its output already exists; see --force-*):
  1. initialise the kit's monorepo cache under data/cache (or --cache-dir) and
     check that its pinned commit is the one recorded in config.json;
  2. download the SINTEF archive into data/ and verify its SHA-256;
  3. extract only the RC2_10_* members listed in config.json;
  4. import them with `tig.py import --format solomon-vrptw` into data/dataset;
  5. run every algorithm in config.json with the configured solver timeout,
     saving the final solution of each instance under runs/<label>/;
  6. hand over to analyze.py, which writes results/ (CSV, JSON, figure).

The experiment keeps its own cache so the monorepo commit it runs at is the
one recorded in config.json, whatever the default cache is pinned to, and so
that `tig.py source sync` never has to touch the default cache (which would
move every other dataset you generate afterwards). Pass --cache-dir to use
another cache, for example the default one.

Only results/ is meant to be committed. data/ and runs/ are ignored by Git.
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
CONFIG_PATH = HERE / "config.json"
DATA = HERE / "data"
RUNS = HERE / "runs"
DEFAULT_CACHE = DATA / "cache"


def log(message: str) -> None:
    print(f"[experiment] {message}", flush=True)


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def download(config: dict, force: bool) -> Path:
    url = config["collection"]["url"]
    archive = DATA / Path(url).name
    DATA.mkdir(parents=True, exist_ok=True)
    if archive.exists() and not force:
        log(f"using existing archive {archive.relative_to(HERE)}")
    else:
        log(f"downloading {url}")
        # SINTEF answers 403 to Python's default User-Agent, so send a generic one.
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (tig-evaluation-kit experiment)"})
        with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    expected = config["collection"]["zip_sha256"]
    if digest != expected:
        raise SystemExit(
            f"SHA-256 mismatch for {archive}: got {digest}, expected {expected}. "
            "Delete the file to download it again, or update config.json if SINTEF "
            "republished the archive."
        )
    return archive


def extract(config: dict, archive: Path) -> Path:
    instances = DATA / "instances"
    instances.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        by_name = {Path(name).name.upper(): name for name in bundle.namelist()}
        for member in config["collection"]["members"]:
            source = by_name.get(member.upper())
            if source is None:
                raise SystemExit(f"{member} is not in {archive.name}")
            (instances / member).write_bytes(bundle.read(source))
    log(f"extracted {len(config['collection']['members'])} instances to {instances.relative_to(HERE)}")
    return instances


def tig(cache_dir: Path, *arguments: str, capture: bool = False) -> str:
    command = [sys.executable, str(REPO / "tig.py"), *arguments, "--cache-dir", str(cache_dir)]
    log(" ".join(command[1:]))
    completed = subprocess.run(command, check=True, cwd=REPO, capture_output=capture, text=True)
    return completed.stdout if capture else ""


def ensure_source(config: dict, cache_dir: Path, sync: bool) -> str:
    """Initialise or sync the cache and return the commit the run will use."""
    status = json.loads(tig(cache_dir, "source", "status", "--json", capture=True))
    if sync or not status.get("initialized"):
        tig(cache_dir, "source", "sync")
        status = json.loads(tig(cache_dir, "source", "status", "--json", capture=True))
    commit = status["commit"]
    expected = config["monorepo_commit"]
    if commit != expected:
        log(f"WARNING: cache {cache_dir} is pinned to {commit}, config.json records {expected}; "
            "results will be produced at the pinned commit (pass --sync to move the cache to current main)")
    else:
        log(f"monorepo commit {commit}")
    return commit


def import_dataset(config: dict, instances: Path, cache_dir: Path, commit: str, force: bool) -> Path:
    dataset = DATA / "dataset"
    manifest = dataset / "dataset.json"
    if manifest.exists() and not force:
        imported_at = json.loads(manifest.read_text(encoding="utf-8")).get("monorepo_commit")
        if imported_at == commit:
            log(f"using existing dataset {dataset.relative_to(HERE)}")
            return dataset
        log(f"dataset was imported at {imported_at}, re-importing at {commit}")
    if dataset.exists():
        shutil.rmtree(dataset)
    tig(
        cache_dir,
        "import",
        "--challenge", config["challenge"],
        "--input", str(instances),
        "--format", config["collection"]["format"],
        "--name", "homberger-gehring-1000-rc2",
        "--out", str(dataset),
    )
    return dataset


def expected_results(config: dict, dataset: Path) -> int:
    manifest = json.loads((dataset / "dataset.json").read_text(encoding="utf-8"))
    return len(manifest["instances"]) * int(config["repetitions"])


def run_algorithm(config: dict, algorithm: dict, dataset: Path, cache_dir: Path, commit: str, force: bool) -> Path:
    output = RUNS / algorithm["label"]
    results = output / "results.jsonl"
    wanted = expected_results(config, dataset)
    if force and output.exists():
        shutil.rmtree(output)
    if results.exists():
        records = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines() if line.strip()]
        stale = {r["algorithm"]["monorepo_commit"] for r in records} - {commit}
        if stale:
            raise SystemExit(
                f"{output.relative_to(HERE)} holds results from monorepo commit {', '.join(sorted(stale))}, "
                f"not {commit}; rerun with --force-run to replace them"
            )
        if len(records) >= wanted:
            log(f"{algorithm['label']}: {len(records)} results already present, skipping")
            return output
    arguments = [
        "run",
        "--dataset", str(dataset),
        "--algorithm", algorithm["id"],
        "--timeout", str(config["timeout_seconds"]),
        "--workers", str(config["workers"]),
        "--repetitions", str(config["repetitions"]),
        "--algorithm-seed", config["algorithm_seed"],
        "--save-solution",
        "--out", str(output),
    ]
    if algorithm.get("hyperparameters"):
        arguments += ["--hyperparameters", json.dumps(algorithm["hyperparameters"])]
    if output.exists():
        log(f"{algorithm['label']}: resuming interrupted run")
        arguments.append("--resume")
    tig(cache_dir, *arguments)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force-download", action="store_true", help="download the archive again")
    parser.add_argument("--force-import", action="store_true", help="re-import the dataset")
    parser.add_argument("--force-run", action="store_true", help="discard existing runs and solve again")
    parser.add_argument("--skip-analysis", action="store_true", help="do not run analyze.py afterwards")
    parser.add_argument("--only", metavar="LABEL", action="append", help="run only this algorithm label (repeatable)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE,
                        help=f"kit cache to build and run from (default: {DEFAULT_CACHE.relative_to(HERE)})")
    parser.add_argument("--sync", action="store_true", help="move the cache to current monorepo main before running")
    options = parser.parse_args(argv)

    config = load_config()
    commit = ensure_source(config, options.cache_dir, options.sync)
    archive = download(config, options.force_download)
    instances = extract(config, archive)
    dataset = import_dataset(config, instances, options.cache_dir, commit, options.force_import)
    for algorithm in config["algorithms"]:
        if options.only and algorithm["label"] not in options.only:
            continue
        run_algorithm(config, algorithm, dataset, options.cache_dir, commit, options.force_run)
    if options.skip_analysis:
        return 0
    sys.path.insert(0, str(HERE))
    import analyze  # noqa: E402  (sibling module)

    return analyze.main([])


if __name__ == "__main__":
    sys.exit(main())
