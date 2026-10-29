#!/usr/bin/env python3
"""Summarise the RC2 runs as distance gaps to the best-known solutions (BKS).

Reads runs/<label>/results.jsonl plus the saved final solutions, recomputes
each solution's real-valued Euclidean length from the dataset coordinates, and
compares it with bks.json. Writes:

  results/per_instance.csv   one row per algorithm and instance
  results/summary.json       per-algorithm mean, standard deviation, extremes
  results/gap_to_bks.png     bar chart (mean gap, +/- 1 standard deviation,
                             one dot per instance)

The chart needs matplotlib; without it the CSV and JSON are still written.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import gzip
import json
import math
import statistics
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
BKS_PATH = HERE / "bks.json"
DATASET = HERE / "data" / "dataset"
RUNS = HERE / "runs"
RESULTS = HERE / "results"

# Reference data-viz palette (light surface).
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e6e5e1"
SERIES = "#2a78d6"


def decode_routes(encoded: str) -> list[list[int]]:
    """Decode a canonical TIG c002 Solution: base64 of gzip of bincode Vec<Vec<usize>>."""
    payload = gzip.decompress(base64.b64decode(encoded))
    offset = 0
    (route_count,) = struct.unpack_from("<Q", payload, offset)
    offset += 8
    routes: list[list[int]] = []
    for _ in range(route_count):
        (length,) = struct.unpack_from("<Q", payload, offset)
        offset += 8
        routes.append(list(struct.unpack_from(f"<{length}Q", payload, offset)))
        offset += 8 * length
    if offset != len(payload):
        raise ValueError("Trailing bytes after decoding the solution")
    return routes


def euclidean_length(positions: list[list[int]], routes: list[list[int]]) -> float:
    total = 0.0
    for route in routes:
        for left, right in zip(route, route[1:]):
            total += math.hypot(positions[left][0] - positions[right][0], positions[left][1] - positions[right][1])
    return total


def load_instances() -> tuple[dict[str, dict], str | None]:
    manifest = json.loads((DATASET / "dataset.json").read_text(encoding="utf-8"))
    instances = {}
    for entry in manifest["instances"]:
        document = json.loads((DATASET / entry["path"]).read_text(encoding="utf-8"))
        instances[entry["id"]] = {
            "name": Path(entry["source"]).stem.lower(),
            "positions": document["challenge"]["node_positions"],
            "fleet_size": document["challenge"]["fleet_size"],
        }
    return instances, manifest.get("monorepo_commit")


def collect(config: dict, bks: dict) -> tuple[list[dict], str | None]:
    instances, commit = load_instances()
    rows = []
    for algorithm in config["algorithms"]:
        run_dir = RUNS / algorithm["label"]
        results = run_dir / "results.jsonl"
        if not results.exists():
            print(f"warning: no results for {algorithm['label']} ({results})", file=sys.stderr)
            continue
        for line in results.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            instance = instances[record["instance"]["id"]]
            best = bks["instances"][instance["name"]]
            row = {
                "algorithm": algorithm["label"],
                "algorithm_id": record["algorithm"]["id"],
                "algorithm_name": record["algorithm"]["name"],
                "hyperparameters": json.dumps(record["hyperparameters"]) if record.get("hyperparameters") else "",
                "instance": instance["name"],
                "repetition": record["repetition"],
                "status": record["status"],
                "bks_vehicles": best["vehicles"],
                "bks_distance": best["distance"],
                "tig_objective": record["objective"]["value"] if record.get("objective") else None,
                "distance": None,
                "vehicles": None,
                "gap_pct": None,
                "solver_wallclock_ms": record.get("solver_wallclock_ms"),
                "error": (record.get("error") or {}).get("message") if record.get("error") else None,
            }
            if record.get("solution"):
                routes = decode_routes(json.loads((run_dir / record["solution"]).read_text(encoding="utf-8")))
                row["distance"] = round(euclidean_length(instance["positions"], routes), 2)
                row["vehicles"] = len(routes)
                row["gap_pct"] = round((row["distance"] - best["distance"]) / best["distance"] * 100, 3)
            rows.append(row)
    rows.sort(key=lambda row: (row["algorithm"], int(row["instance"].rsplit("_", 1)[1]), row["repetition"]))
    return rows, commit


def summarize(config: dict, rows: list[dict], commit: str | None) -> dict:
    summary = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "timeout_seconds": config["timeout_seconds"],
        "workers": config["workers"],
        "repetitions": config["repetitions"],
        "algorithm_seed": config["algorithm_seed"],
        "monorepo_commit": commit,
        "note": "hgs_v1 at its default preset stops after one local search; hgs_explore5 is the same code with exploration_level 5",
        "metric": "percentage by which the real-valued Euclidean length of the final solution exceeds the BKS distance",
        "algorithms": {},
    }
    for algorithm in config["algorithms"]:
        mine = [row for row in rows if row["algorithm"] == algorithm["label"]]
        gaps = [row["gap_pct"] for row in mine if row["gap_pct"] is not None]
        if not gaps:
            continue
        summary["algorithms"][algorithm["label"]] = {
            "id": algorithm["id"],
            "name": algorithm["name"],
            "hyperparameters": algorithm.get("hyperparameters"),
            "instances": len(mine),
            "with_solution": len(gaps),
            "timeouts_with_solution": sum(1 for row in mine if row["status"] == "timeout_with_solution"),
            "failures": sum(1 for row in mine if row["gap_pct"] is None),
            "mean_gap_pct": round(statistics.mean(gaps), 3),
            "std_gap_pct": round(statistics.stdev(gaps), 3) if len(gaps) > 1 else 0.0,
            "median_gap_pct": round(statistics.median(gaps), 3),
            "min_gap_pct": round(min(gaps), 3),
            "max_gap_pct": round(max(gaps), 3),
            "mean_vehicles": round(statistics.mean(row["vehicles"] for row in mine if row["vehicles"]), 2),
            "mean_bks_vehicles": round(statistics.mean(row["bks_vehicles"] for row in mine), 2),
            "mean_solver_wallclock_ms": round(statistics.mean(row["solver_wallclock_ms"] for row in mine if row["solver_wallclock_ms"] is not None), 1),
        }
    return summary


def write_outputs(rows: list[dict], summary: dict) -> None:
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / "per_instance.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def plot(summary: dict, rows: list[dict]) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as error:  # matplotlib missing or broken: tables are still written
        print(f"warning: chart skipped, matplotlib unavailable ({error})", file=sys.stderr)
        return False

    labels = list(summary["algorithms"])
    means = [summary["algorithms"][label]["mean_gap_pct"] for label in labels]
    stds = [summary["algorithms"][label]["std_gap_pct"] for label in labels]
    ticks = []
    for label in labels:
        entry = summary["algorithms"][label]
        detail = f"{entry['id']} {entry['name']}"
        if entry.get("hyperparameters"):
            detail += "\n" + ", ".join(f"{key}={value}" for key, value in entry["hyperparameters"].items())
        ticks.append(f"{label}\n{detail}")

    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    positions = list(range(len(labels)))
    ax.bar(positions, means, width=0.32, color=SERIES, linewidth=0, zorder=2)
    ax.errorbar(positions, means, yerr=stds, fmt="none", ecolor=TEXT_SECONDARY, elinewidth=1.5, capsize=6, capthick=1.5, zorder=3)
    for index, label in enumerate(labels):
        gaps = [row["gap_pct"] for row in rows if row["algorithm"] == label and row["gap_pct"] is not None]
        jitter = [index + 0.24 + 0.012 * (k - len(gaps) / 2) for k in range(len(gaps))]
        ax.scatter(jitter, gaps, s=42, color=SERIES, edgecolor=SURFACE, linewidth=1.5, zorder=4)
        ax.annotate(f"{means[index]:+.1f}%", (index, means[index]), xytext=(-10, 4 if means[index] >= 0 else -4), textcoords="offset points", ha="right", va="bottom" if means[index] >= 0 else "top", fontsize=10, color=TEXT_PRIMARY, fontweight="bold")
    ax.axhline(0, color=TEXT_SECONDARY, linewidth=1, zorder=1)
    ax.set_xticks(positions, ticks, fontsize=9, color=TEXT_PRIMARY)
    ax.set_ylabel("Distance above best-known solution (%)", color=TEXT_PRIMARY)
    ax.yaxis.grid(True, color=GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, length=0)
    n = max(summary["algorithms"][label]["instances"] for label in labels)
    ax.set_title(
        f"Homberger-Gehring 1000-customer RC2: gap to BKS, {summary['timeout_seconds']} s per instance",
        loc="left", fontsize=11, color=TEXT_PRIMARY, pad=14,
    )
    ax.text(0, 1.01, f"bar = mean over {n} instances, whisker = ±1 standard deviation, dots = instances",
            transform=ax.transAxes, fontsize=8.5, color=TEXT_SECONDARY)
    fig.tight_layout()
    fig.savefig(RESULTS / "gap_to_bks.png", facecolor=SURFACE)
    return True


def markdown_table(summary: dict, rows: list[dict]) -> str:
    labels = list(summary["algorithms"])
    lines = ["| Instance | BKS vehicles | BKS distance | " + " | ".join(f"{label} gap | {label} vehicles" for label in labels) + " |",
             "|---|---|---|" + "---|---|" * len(labels)]
    names = sorted({row["instance"] for row in rows}, key=lambda name: int(name.rsplit("_", 1)[1]))
    for name in names:
        cells = []
        for label in labels:
            row = next((r for r in rows if r["algorithm"] == label and r["instance"] == name), None)
            if row is None or row["gap_pct"] is None:
                cells += ["-", "-"]
            else:
                flag = "*" if row["status"] == "timeout_with_solution" else ""
                cells += [f"{row['gap_pct']:+.2f}%{flag}", str(row["vehicles"])]
        sample = next(r for r in rows if r["instance"] == name)
        lines.append(f"| {name} | {sample['bks_vehicles']} | {sample['bks_distance']:.2f} | " + " | ".join(cells) + " |")
    cells = []
    for label in labels:
        s = summary["algorithms"][label]
        cells += [f"**{s['mean_gap_pct']:+.2f}% ± {s['std_gap_pct']:.2f}**", f"{s['mean_vehicles']:.1f}"]
    lines.append("| **mean ± sd** | | | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-plot", action="store_true", help="write tables only")
    options = parser.parse_args(argv)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    bks = json.loads(BKS_PATH.read_text(encoding="utf-8"))
    rows, commit = collect(config, bks)
    if not rows:
        raise SystemExit("No results found under runs/; run run_experiment.py first")
    summary = summarize(config, rows, commit)
    write_outputs(rows, summary)
    print(markdown_table(summary, rows))
    if not options.no_plot:
        if plot(summary, rows):
            print(f"chart written to {RESULTS / 'gap_to_bks.png'}")
    print(f"tables written to {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
