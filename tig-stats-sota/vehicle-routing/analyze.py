#!/usr/bin/env python3
"""Summarise each instance set's runs as distance gaps to the best-known solutions (BKS).

For every set folder given (default: every subfolder holding a config.json),
reads <set>/runs/<label>/results.jsonl plus the saved final solutions,
recomputes each solution's real-valued Euclidean length from the dataset
coordinates, re-checks its time windows under real-valued travel times, and
compares it with <set>/bks.json. Writes:

  <set>/results/per_instance.csv   one row per algorithm and instance
  <set>/results/summary.json       per-algorithm mean, standard deviation, extremes
  <set>/results/gap_to_bks.png     chart: one dot per instance for the gap to the
                                   BKS distance, a marker at the mean with a
                                   +/- 1 standard deviation whisker, and the mean
                                   fleet size noted under each configuration

With more than one set it also prints a cross-set summary table. The chart
needs matplotlib; without it the CSV and JSON are still written.
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
EXPERIMENT_PATH = HERE / "experiment.json"

# Reference data-viz palette (light surface).
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e6e5e1"
SERIES = "#2a78d6"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def discover_sets() -> list[Path]:
    return sorted(path.parent for path in HERE.glob("*/config.json"))


def resolve_set(name: str) -> Path:
    for candidate in (Path(name), HERE / name):
        if (candidate / "config.json").is_file():
            return candidate.resolve()
    raise SystemExit(f"{name} is not an instance set: expected a folder holding config.json under {HERE}")


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


def _distance(positions: list[list[int]], left: int, right: int) -> float:
    return math.hypot(positions[left][0] - positions[right][0], positions[left][1] - positions[right][1])


def euclidean_length(positions: list[list[int]], routes: list[list[int]]) -> float:
    return sum(_distance(positions, left, right) for route in routes for left, right in zip(route, route[1:]))


def late_arrivals(instance: dict, routes: list[list[int]]) -> tuple[int, float]:
    """Count arrivals after the due time when travel times are real-valued.

    Follows the schedule of TIG's verifier (wait for the ready time, then add the
    service time), but with Euclidean instead of integer-rounded travel times.
    TIG accepts a route on the rounded times, so this is where a solution can be
    slightly infeasible against the literature's real-valued windows. Returns the
    number of late arrivals (depot returns included) and the largest lateness.
    """
    positions, ready, due, service = instance["positions"], instance["ready_times"], instance["due_times"], instance["service_time"]
    count, worst = 0, 0.0
    for route in routes:
        time = 0.0
        for previous, node in zip(route, route[1:]):
            time += _distance(positions, previous, node)
            if time > due[node] + 1e-9:
                count += 1
                worst = max(worst, time - due[node])
            if node != 0:
                time = max(time, ready[node]) + service
    return count, worst


def load_instances(dataset: Path) -> tuple[dict[str, dict], str | None]:
    manifest = load_json(dataset / "dataset.json")
    instances = {}
    for entry in manifest["instances"]:
        challenge = load_json(dataset / entry["path"])["challenge"]
        instances[entry["id"]] = {
            "name": Path(entry["source"]).stem.lower(),
            "positions": challenge["node_positions"],
            "ready_times": challenge["ready_times"],
            "due_times": challenge["due_times"],
            "service_time": challenge["service_time"],
        }
    return instances, manifest.get("monorepo_commit")


def collect(set_dir: Path, experiment: dict, bks: dict) -> tuple[list[dict], str | None]:
    dataset = set_dir / "data" / "dataset"
    if not (dataset / "dataset.json").exists():
        return [], None
    instances, commit = load_instances(dataset)
    rows = []
    for algorithm in experiment["algorithms"]:
        run_dir = set_dir / "runs" / algorithm["label"]
        results = run_dir / "results.jsonl"
        if not results.exists():
            print(f"warning: no results for {set_dir.name}/{algorithm['label']} ({results})", file=sys.stderr)
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
                "late_arrivals": None,
                "max_lateness": None,
                "solver_wallclock_ms": record.get("solver_wallclock_ms"),
                "error": (record.get("error") or {}).get("message") if record.get("error") else None,
            }
            if record.get("solution"):
                routes = decode_routes(json.loads((run_dir / record["solution"]).read_text(encoding="utf-8")))
                row["distance"] = round(euclidean_length(instance["positions"], routes), 2)
                row["vehicles"] = len(routes)
                row["gap_pct"] = round((row["distance"] - best["distance"]) / best["distance"] * 100, 3)
                late, worst = late_arrivals(instance, routes)
                row["late_arrivals"] = late
                row["max_lateness"] = round(worst, 3)
            rows.append(row)
    rows.sort(key=lambda row: (row["algorithm"], int(row["instance"].rsplit("_", 1)[1]), row["repetition"]))
    return rows, commit


def summarize(set_dir: Path, experiment: dict, config: dict, rows: list[dict], commit: str | None) -> dict:
    summary = {
        "set": set_dir.name,
        "title": config["title"],
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "timeout_seconds": experiment["timeout_seconds"],
        "workers": experiment["workers"],
        "repetitions": experiment["repetitions"],
        "algorithm_seed": experiment["algorithm_seed"],
        "monorepo_commit": commit,
        "note": experiment.get("note"),
        "metric": "percentage by which the real-valued Euclidean length of the final solution exceeds the BKS distance",
        "algorithms": {},
    }
    for algorithm in experiment["algorithms"]:
        mine = [row for row in rows if row["algorithm"] == algorithm["label"]]
        if not mine:
            continue
        gaps = [row["gap_pct"] for row in mine if row["gap_pct"] is not None]
        vehicles = [row["vehicles"] for row in mine if row["vehicles"] is not None]
        times = [row["solver_wallclock_ms"] for row in mine if row["solver_wallclock_ms"] is not None]
        summary["algorithms"][algorithm["label"]] = {
            "id": algorithm["id"],
            "name": algorithm["name"],
            "hyperparameters": algorithm.get("hyperparameters"),
            "chart_note": algorithm.get("chart_note"),
            "instances": len(mine),
            "with_solution": len(gaps),
            "timeouts_with_solution": sum(1 for row in mine if row["status"] == "timeout_with_solution"),
            "failures": sum(1 for row in mine if row["gap_pct"] is None),
            "mean_gap_pct": round(statistics.mean(gaps), 3) if gaps else None,
            "std_gap_pct": round(statistics.stdev(gaps), 3) if len(gaps) > 1 else None,
            "median_gap_pct": round(statistics.median(gaps), 3) if gaps else None,
            "min_gap_pct": min(gaps) if gaps else None,
            "max_gap_pct": max(gaps) if gaps else None,
            "mean_vehicles": round(statistics.mean(vehicles), 1) if vehicles else None,
            "std_vehicles": round(statistics.stdev(vehicles), 3) if len(vehicles) > 1 else None,
            "mean_bks_vehicles": round(statistics.mean(row["bks_vehicles"] for row in mine), 1),
            "instances_with_late_arrivals": sum(1 for row in mine if row["late_arrivals"]),
            "max_lateness": max((row["max_lateness"] or 0.0) for row in mine),
            "mean_solver_wallclock_ms": round(statistics.mean(times), 1) if times else None,
        }
    return summary


def write_outputs(set_dir: Path, rows: list[dict], summary: dict) -> None:
    results = set_dir / "results"
    results.mkdir(exist_ok=True)
    with (results / "per_instance.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def _style_axis(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.yaxis.grid(True, color=GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, length=0)


def plot(set_dir: Path, summary: dict, rows: list[dict]) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as error:  # matplotlib missing or broken: tables are still written
        print(f"warning: chart skipped, matplotlib unavailable ({error})", file=sys.stderr)
        return False

    labels = list(summary["algorithms"])
    entries = [summary["algorithms"][label] for label in labels]
    positions = list(range(len(labels)))
    ticks = []
    for label, entry in zip(labels, entries):
        detail = f"{entry['id']} {entry['name']}"
        if entry.get("chart_note"):
            detail += "\n" + entry["chart_note"]
        elif entry.get("hyperparameters"):
            detail += "\n" + ", ".join(f"{key}={value}" for key, value in entry["hyperparameters"].items())
        ticks.append(f"{label}\n{detail}")
    n = max(entry["instances"] for entry in entries)
    bks_vehicles = statistics.mean(row["bks_vehicles"] for row in rows)
    means = [entry["mean_gap_pct"] for entry in entries]
    stds = [entry["std_gap_pct"] or 0.0 for entry in entries]

    fig, ax = plt.subplots(figsize=(8.0, 5.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style_axis(ax)
    ax.axhline(0, color=TEXT_SECONDARY, linewidth=1, zorder=1)

    # One dot per instance on the configuration's centre line; the surface-coloured
    # edge keeps dots with near-equal gaps distinguishable.
    for index, label in enumerate(labels):
        values = [row["gap_pct"] for row in rows if row["algorithm"] == label and row["gap_pct"] is not None]
        ax.scatter([index] * len(values), values, s=46, color=SERIES, edgecolor=SURFACE, linewidth=1.5, zorder=3)

    # Mean with a +/- 1 standard deviation whisker, drawn over the dots.
    ax.errorbar(positions, means, yerr=stds, fmt="none", ecolor=TEXT_PRIMARY, elinewidth=1.6, capsize=7, capthick=1.6, zorder=4)
    ax.scatter(positions, means, marker="D", s=64, color=TEXT_PRIMARY, edgecolor=SURFACE, linewidth=1.5, zorder=5)
    for index, (mean, entry) in enumerate(zip(means, entries)):
        ax.annotate(f"{mean:+.1f}%", (index, mean), xytext=(-30, 0), textcoords="offset points",
                    ha="right", va="center", fontsize=10, color=TEXT_PRIMARY, fontweight="bold")
        # Fleet size sits between the axis and the tick label of its configuration.
        ax.annotate(f"mean fleet {entry['mean_vehicles']:.1f} vehicles", (index, 0),
                    xycoords=("data", "axes fraction"), xytext=(0, -7), textcoords="offset points",
                    ha="center", va="top", fontsize=9, color=TEXT_PRIMARY)

    ax.set_xlim(-0.6, len(labels) - 0.4)
    ax.set_xticks(positions, ticks, fontsize=9, color=TEXT_SECONDARY)
    ax.tick_params(axis="x", pad=24)
    ax.set_ylabel("Distance above best-known solution (%)", color=TEXT_PRIMARY)
    ax.set_title(
        f"{summary['title']}: gap to BKS, {summary['timeout_seconds']} s per instance",
        loc="left", fontsize=11, color=TEXT_PRIMARY, pad=32,
    )
    ax.text(0, 1.02,
            f"dot = instance, diamond = mean over {n} instances, whisker = ±1 standard deviation\n"
            f"BKS fleet mean {bks_vehicles:.1f} vehicles; the BKS minimises vehicles first, TIG minimises distance only",
            transform=ax.transAxes, fontsize=8.5, color=TEXT_SECONDARY, va="bottom", linespacing=1.5)

    fig.savefig(set_dir / "results" / "gap_to_bks.png", facecolor=SURFACE, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return True


def markdown_table(summary: dict, rows: list[dict]) -> str:
    """Per-instance table; * marks a timeout, † a late arrival under real-valued travel times."""
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
                flag = ("*" if row["status"] == "timeout_with_solution" else "") + ("†" if row["late_arrivals"] else "")
                cells += [f"{row['gap_pct']:+.2f}%{flag}", str(row["vehicles"])]
        sample = next(r for r in rows if r["instance"] == name)
        lines.append(f"| {name} | {sample['bks_vehicles']} | {sample['bks_distance']:.2f} | " + " | ".join(cells) + " |")
    cells = []
    for label in labels:
        s = summary["algorithms"][label]
        cells += [f"**{s['mean_gap_pct']:+.2f}% ± {s['std_gap_pct']:.2f}**", f"**{s['mean_vehicles']:.1f} ± {s['std_vehicles']:.1f}**"]
    bks_vehicles = statistics.mean(row["bks_vehicles"] for row in rows)
    lines.append(f"| **mean ± sd** | **{bks_vehicles:.1f}** | | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def cross_set_table(summaries: list[dict]) -> str:
    labels = list(summaries[0]["algorithms"])
    lines = ["| Set | Instances | BKS vehicles | " + " | ".join(f"{label} gap | {label} vehicles" for label in labels) + " |",
             "|---|---|---|" + "---|---|" * len(labels)]
    for summary in summaries:
        cells = []
        for label in labels:
            s = summary["algorithms"].get(label)
            if s is None or s["mean_gap_pct"] is None:
                cells += ["-", "-"]
            else:
                cells += [f"{s['mean_gap_pct']:+.2f}% ± {s['std_gap_pct']:.2f}", f"{s['mean_vehicles']:.1f}"]
        first = next(iter(summary["algorithms"].values()))
        lines.append(f"| [{summary['title']}]({summary['set']}/README.md) | {first['instances']} | {first['mean_bks_vehicles']:.1f} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def analyze_set(set_dir: Path, experiment: dict, make_plot: bool) -> dict | None:
    config = load_json(set_dir / "config.json")
    bks = load_json(set_dir / "bks.json")
    rows, commit = collect(set_dir, experiment, bks)
    if not rows:
        print(f"warning: no results under {set_dir / 'runs'}; run run_experiment.py {set_dir.name} first", file=sys.stderr)
        return None
    summary = summarize(set_dir, experiment, config, rows, commit)
    write_outputs(set_dir, rows, summary)
    print(f"## {config['title']} ({set_dir.name})\n")
    print(markdown_table(summary, rows))
    if make_plot and plot(set_dir, summary, rows):
        print(f"chart written to {set_dir / 'results' / 'gap_to_bks.png'}")
    print(f"tables written to {set_dir / 'results'}\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sets", nargs="*", metavar="SET",
                        help="instance-set folders to analyse (default: every subfolder holding a config.json)")
    parser.add_argument("--no-plot", action="store_true", help="write tables only")
    options = parser.parse_args(argv)
    experiment = load_json(EXPERIMENT_PATH)
    sets = [resolve_set(name) for name in options.sets] or discover_sets()
    summaries = [summary for summary in (analyze_set(set_dir, experiment, not options.no_plot) for set_dir in sets) if summary]
    if not summaries:
        raise SystemExit("No results found; run run_experiment.py first")
    if len(summaries) > 1:
        print("## All sets\n")
        print(cross_set_table(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
