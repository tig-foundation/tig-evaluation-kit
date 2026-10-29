#!/usr/bin/env python3
"""Run TIG vehicle-routing algorithms on the Homberger-Gehring 1000-customer RC2 set.

Steps (each is skipped when its output already exists; see --force-*):
  1. download the SINTEF archive into data/ and verify its SHA-256;
  2. extract only the RC2_10_* members listed in config.json;
  3. import them with `tig.py import --format solomon-vrptw` into data/dataset;
  4. run every algorithm in config.json with the configured solver timeout,
     saving the final solution of each instance under runs/<label>/;
  5. hand over to analyze.py, which writes results/ (CSV, JSON, figure).

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


def tig(*arguments: str) -> None:
    command = [sys.executable, str(REPO / "tig.py"), *arguments]
    log(" ".join(command[1:]))
    subprocess.run(command, check=True, cwd=REPO)


def import_dataset(config: dict, instances: Path, force: bool) -> Path:
    dataset = DATA / "dataset"
    if (dataset / "dataset.json").exists() and not force:
        log(f"using existing dataset {dataset.relative_to(HERE)}")
        return dataset
    if dataset.exists():
        shutil.rmtree(dataset)
    tig(
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


def run_algorithm(config: dict, algorithm: dict, dataset: Path, force: bool) -> Path:
    output = RUNS / algorithm["label"]
    results = output / "results.jsonl"
    wanted = expected_results(config, dataset)
    if force and output.exists():
        shutil.rmtree(output)
    if results.exists():
        done = sum(1 for line in results.read_text(encoding="utf-8").splitlines() if line.strip())
        if done >= wanted:
            log(f"{algorithm['label']}: {done} results already present, skipping")
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
    tig(*arguments)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force-download", action="store_true", help="download the archive again")
    parser.add_argument("--force-import", action="store_true", help="re-import the dataset")
    parser.add_argument("--force-run", action="store_true", help="discard existing runs and solve again")
    parser.add_argument("--skip-analysis", action="store_true", help="do not run analyze.py afterwards")
    parser.add_argument("--only", metavar="LABEL", action="append", help="run only this algorithm label (repeatable)")
    options = parser.parse_args(argv)

    config = load_config()
    archive = download(config, options.force_download)
    instances = extract(config, archive)
    dataset = import_dataset(config, instances, options.force_import)
    for algorithm in config["algorithms"]:
        if options.only and algorithm["label"] not in options.only:
            continue
        run_algorithm(config, algorithm, dataset, options.force_run)
    if options.skip_analysis:
        return 0
    sys.path.insert(0, str(HERE))
    import analyze  # noqa: E402  (sibling module)

    return analyze.main([])


if __name__ == "__main__":
    sys.exit(main())
