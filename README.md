# tig-challenges

`tig-challenges` is an offline benchmark runner for CPU algorithms from [TIG's monorepo](https://github.com/tig-foundation/tig-monorepo) and for local Rust algorithms using the same interface.

It can generate reproducible TIG instances, normalize academic instances, run an algorithm identified only by its stable code (for example `c002_a103`), and report raw objective values and solver-only wall-clock time. It does not emulate `tig-runtime`, meter fuel, or calculate TIG quality.

The authoritative Challenge, Solution, objective, baseline, and submitted-algorithm code comes from a clean pinned `tig-monorepo` commit. Copied challenge implementations and the former standalone Rust crate have been removed.

## Requirements

- Python 3.10 or newer
- Git
- Rust/Cargo

The first native build downloads Rust dependencies and can take a while. Builds are cached and build time is never included in solver timing.

## Quick start

The monorepo source cache initializes automatically on first use. To initialize or explicitly update it yourself:

```bash
python3 tig.py source sync
python3 tig.py source status
```

Version 1 always synchronizes from the official TIG monorepo. The runner
mirrors its Git objects and builds a clean pinned checkout; forks and arbitrary
source repositories cannot be selected. `--cache-dir` remains available when
the managed source and build cache should live somewhere else.

List current CPU algorithms from the pinned `main` commit:

```bash
python3 tig.py algorithms list
python3 tig.py algorithms list --challenge c002
```

Generate 100 reproducible TIG vehicle-routing instances:

```bash
python3 tig.py generate \
  --challenge c002 \
  --track n_nodes=1000 \
  --count 100
```

Generation has a friendly seed flag that defaults to `default_seed`:

```bash
python3 tig.py generate \
  --challenge c002 \
  --track n_nodes=1000 \
  --count 100 \
  --seed training_set_2
```

`--start-nonce` selects the first deterministic instance index and defaults to
zero. This is useful for creating another non-overlapping batch from the same
seed and track. For example, this generates indices 100 through 149:

```bash
python3 tig.py generate \
  --challenge c002 \
  --track n_nodes=1000 \
  --seed training_set_2 \
  --start-nonce 100 \
  --count 50
```

Generation is intentionally offline-only. There is no interface for replaying
instances from the live TIG network.

Run a submitted algorithm by stable code or friendly name:

```bash
python3 tig.py run \
  --dataset datasets/c002/<dataset-id>/dataset.json \
  --algorithm c002_a103 \
  --workers 4 \
  --timeout 120
```

The friendly equivalent is `--algorithm fast_lane_v4`. The result always records both names and the monorepo commit.

## Academic instances

Normalize one file or a directory into a versioned dataset:

```bash
python3 tig.py import \
  --challenge c001 \
  --input benchmarks/sat \
  --format dimacs-cnf \
  --name my-sat-benchmark \
  --metadata benchmark-metadata.json
```

Version 1 supports:

| Challenge | Formats |
| --- | --- |
| `c001` satisfiability | DIMACS CNF containing exactly three literals per clause; canonical JSON |
| `c002` vehicle routing | Solomon and Homberger/Gehring VRPTW; canonical JSON |
| `c003` knapsack | normalized TIG/QKP graph text; canonical JSON |
| `c007` job scheduling | Brandimarte FJSP, classic JSP; canonical JSON |
| `c008` energy arbitrage | canonical JSON |

`--format auto` is the default and uses conservative format detection. Specify the format if a text file is ambiguous. Users can support other sources by converting them to the versioned canonical instance JSON described in [`schemas/instance.schema.json`](schemas/instance.schema.json).

Imported and generated datasets are local benchmark artifacts. The repository's
`datasets/` directory is ignored by Git; only the small files under
`tests/fixtures/` are retained for automated testing.

Academic problem data has no generation seed. Before each algorithm call, the runner supplies a separate deterministic algorithm RNG seed without modifying the stored instance.

`--metadata` accepts an inline JSON object or file for attribution, licensing, BKS maps, or other joinable analysis fields. Metadata is recorded but never changes validation, objectives, or solver status. The same option and `--name` are available on `generate`.

## Algorithm randomness and repetitions

Generation randomness and algorithm randomness are deliberately separate:

- `generate --seed` selects the generated challenge instances.
- `run --algorithm-seed` changes randomized solver behavior on the same stored instances.

`--algorithm-seed` defaults to `default_seed`. Its effective 32-byte value is derived from the supplied string, instance hash, and repetition index. Thus repetitions receive different but reproducible random streams:

```bash
python3 tig.py run \
  --dataset path/to/dataset.json \
  --algorithm c001_a001 \
  --algorithm-seed experiment_4 \
  --repetitions 5
```

## Local algorithms

Run a local module against the same canonical Challenge and evaluator:

```bash
python3 tig.py run \
  --dataset path/to/dataset.json \
  --local path/to/algorithm/mod.rs \
  --hyperparameters '{"effort": 10}'
```

CPU modules use the TIG callback contract:

```rust
pub fn solve_challenge(
    challenge: &Challenge,
    save_solution: &dyn Fn(&Solution) -> anyhow::Result<()>,
    hyperparameters: &Option<serde_json::Map<String, serde_json::Value>>,
) -> anyhow::Result<()>;
```

Omitting `--hyperparameters` passes `None`; passing `{}` passes an explicitly empty map. Canonical algorithms apply and validate their own defaults. A missing required value therefore produces a normal `runtime_error` instead of a runner-invented default.

See the [local algorithm guide](docs/local-algorithms.md) for a reusable template, RNG behavior, callback semantics, and build-cache boundaries.

## Baselines and raw objectives

Where the canonical challenge exposes a baseline:

```bash
python3 tig.py run \
  --dataset path/to/dataset.json \
  --baseline
```

The baseline produces an ordinary raw result. The runner never automatically compares it to another solver, a BKS, or TIG quality scaling.

| Challenge | Raw objective | Direction |
| --- | --- | --- |
| `c001` satisfiability | satisfied (`1` or `0`) | maximize |
| `c002` vehicle routing | total distance | minimize |
| `c003` knapsack | total value | maximize |
| `c007` job scheduling | makespan | minimize |
| `c008` energy arbitrage | total profit | maximize |

## Timing, solutions, and snapshots

`solver_wallclock_ms` covers only the call that produces solutions. Source synchronization, builds, process startup, instance parsing, and objective evaluation are excluded. `--timeout` applies after the native solver reports that its instance is ready.

Solution files are not retained by default. The latest callback is kept temporarily so it can be evaluated, including after a timeout.

```bash
# Retain the final callback
python3 tig.py run ... --save-solution

# Retain every callback
python3 tig.py run ... --save-all-solutions

# Sample the latest callback at requested solver times
python3 tig.py run ... --snapshot-times 1,5,10
python3 tig.py run ... --snapshot-interval 5
```

Repeated snapshots of the same callback are not duplicated.

## Results and resume

A run directory contains:

```text
runs/<run-id>/
  manifest.json
  results/<result-id>.json
  results.jsonl
  solutions/                 # only when requested
  snapshots/                 # only when requested
  logs/                      # only when a solver writes logs
```

Every result contains the raw objective, direction, solver wall-clock time, status, callback count, algorithm and instance identities, hyperparameters, RNG seed, and nullable `peak_memory_bytes`. Memory measurement is reserved for a later release.

Resume a matching interrupted run with:

```bash
python3 tig.py run ... --out runs/my-run --resume
```

Completed atomic result files are reused only after their complete structure,
run identity, algorithm settings, terminal status, and any saved solution or
snapshot hashes have been validated. Invalid or missing work is rerun. Resume
also rejects changes to source, dataset hashes, parameters, timeout, workers,
repetitions, or capture settings.

Canonical JSON contracts are in [`schemas/`](schemas/). The full design and verification plan is in [`docs/benchmark-runner-plan.md`](docs/benchmark-runner-plan.md).

Users of the former `generate_dataset`, `run_algorithm`, or `evaluate_solutions` commands should follow the [migration guide](docs/migration.md). Existing knapsack, Solomon VRPTW, and Brandimarte FJSP text instances can be normalized by `import`; old quality outputs are deliberately not mixed with raw-objective results.

## Current scope

Version 1 supports CPU challenges `c001`, `c002`, `c003`, `c007`, and `c008`.
Submitted-algorithm availability is discovered from the pinned `main` commit;
local algorithms and canonical baselines do not depend on a submitted algorithm
being exported for that challenge.

CUDA challenges, historical algorithm branches, and portable process-tree memory measurement are deferred follow-on work. CUDA in particular is outside the current completion target and will be revisited much later.
