# tig-evaluation-kit

`tig-evaluation-kit` lets you benchmark TIG algorithms on your own computer.

You can use it to:

- run an official TIG algorithm by its stable code, such as `c002_a103`;
- run your own local Rust algorithm through the same interface;
- generate repeatable TIG challenge instances;
- import supported academic benchmark files; and
- record each solution's raw objective score and solver run time in JSON.

The runner uses the Challenge, Solution, evaluator, baseline, and algorithm code
directly from the official [TIG monorepo](https://github.com/tig-foundation/tig-monorepo).
It downloads and builds that code for you. Algorithms run natively on the CPU,
not in Docker.

This is an offline benchmarking tool, not a copy of the TIG network runtime. It
does not meter fuel, recreate live network instances, or calculate TIG quality.
It reports the raw result so you can compare it with a best-known solution,
another solver, or the TIG baseline in whatever way suits your experiment.

## What you need

- Python 3.10 or newer
- Git
- Rust and Cargo

Clone this repository and enter it:

```bash
git clone https://github.com/tig-foundation/tig-evaluation-kit.git
cd tig-evaluation-kit
```

There is no separate `tig-monorepo` setup step. The first command that needs TIG
source code downloads it automatically. The first Rust build can take a while,
but later builds are cached. Download and build time is never counted as solver
run time.

## Quick start

### 1. See which algorithms are available

```bash
python3 tig.py algorithms list
python3 tig.py algorithms list --challenge c002
```

Algorithms come from the `main` branch of the official TIG monorepo. You can
select one by stable code or by the friendly name shown in the list.

### 2. Create a dataset

You can either generate TIG instances or import existing academic instances.

To generate 100 vehicle-routing instances:

```bash
python3 tig.py generate \
  --challenge c002 \
  --track n_nodes=1000 \
  --count 100 \
  --out datasets/my-vrp-dataset
```

To import a directory of standard flexible job-shop (`.fjs`) files:

```bash
python3 tig.py import \
  --challenge c007 \
  --input path/to/fjs-files \
  --format standard-fjsp \
  --out datasets/my-fjs-dataset
```

Importing does not change your original files. It creates a validated,
versioned dataset in the format used by TIG algorithms.

### 3. Run an algorithm

```bash
python3 tig.py run \
  --dataset datasets/my-fjs-dataset/dataset.json \
  --algorithm c007_a001 \
  --timeout 120 \
  --workers 1 \
  --out runs/my-fjs-run
```

Here, `--timeout 120` gives each solver call up to 120 seconds. One worker is
the default and gives the cleanest timing. You can use more workers to finish a
large batch sooner; see
[Timing, saved solutions, and snapshots](#timing-saved-solutions-and-snapshots).

### 4. Read the results

The command prints the location of `results.jsonl`. A run directory normally
looks like this:

```text
runs/my-fjs-run/
  manifest.json
  results/<result-id>.json
  results.jsonl
  solutions/                 # only when requested
  snapshots/                 # only when requested
  logs/                      # only when a solver writes logs
```

Each result records the raw objective, whether lower or higher is better,
solver-only wall-clock time, status, algorithm, instance, hyperparameters,
random seed, and TIG monorepo commit. `peak_memory_bytes` is currently left
empty; portable memory measurement is planned for a later release.

## Do I need to run `source sync`?

No. Commands such as `algorithms list`, `generate`, `import`, and `run`
automatically set up the managed TIG source cache the first time they need it.

Use these commands when you want to set it up explicitly or update your cached
copy to the latest `main` commit:

```bash
python3 tig.py source sync
python3 tig.py source status
```

Normal commands keep using the already selected commit, which makes results
repeatable. They do not silently move to a newer commit. Version 1 only uses the
official TIG monorepo; choosing a fork or another source repository is not
supported. Advanced users can use `--cache-dir` to put the managed source and
build cache somewhere else.

By default the cache lives in `~/.cache/tig-evaluation-kit` (or under
`$XDG_CACHE_HOME`). The `TIG_EVALUATION_KIT_CACHE_DIR` environment variable
overrides it. A cache created under the repository's former name,
`tig-challenges`, is reused automatically, and the old
`TIG_CHALLENGES_CACHE_DIR` variable is still honoured.

## Generated TIG datasets

Generation is repeatable. With the same challenge, track, seed, and instance
indices, two users get the same instances.

`--seed` defaults to the fixed value `default_seed`, so it is optional:

```bash
python3 tig.py generate \
  --challenge c002 \
  --track n_nodes=1000 \
  --count 100 \
  --seed training_set_2
```

`--start-nonce` simply means “the index of the first instance to generate.” It
defaults to zero. For example, this creates instances 100 through 149 instead
of recreating instances 0 through 49:

```bash
python3 tig.py generate \
  --challenge c002 \
  --track n_nodes=1000 \
  --seed training_set_2 \
  --start-nonce 100 \
  --count 50
```

This seed is only for generating a dataset. It is separate from the random seed
used by an algorithm while solving an instance.

## Academic datasets

Academic benchmark collections are not stored in this repository. You provide
the files, and `tig-evaluation-kit` imports them when their format is supported.
The repository's `datasets/` directory is ignored by Git, so imported and
generated datasets stay local.

Version 1 understands these formats:

| Challenge | Accepted formats |
| --- | --- |
| `c001` satisfiability | DIMACS CNF with exactly three literals per clause; canonical JSON |
| `c002` vehicle routing | Solomon and Homberger/Gehring VRPTW; canonical JSON |
| `c003` knapsack | normalized TIG/QKP graph text; canonical JSON |
| `c007` job scheduling | standard FJSP (`.fjs`, including Brandimarte/Hurink), classic JSP; canonical JSON |
| `c008` energy arbitrage | canonical JSON |

`--format auto` is the default and detects formats only when it can do so
safely. If a plain-text file could mean more than one thing, specify its format
with `--format`.

The responsibility is intentionally simple:

- `tig-evaluation-kit` provides importers for established formats such as standard
  FJSP, DIMACS, and Solomon VRPTW.
- You choose the correct TIG challenge and provide the files. If automatic
  detection is uncertain, you also name the format.
- For a custom or unsupported format, convert it to the canonical JSON defined
  in [`schemas/instance.schema.json`](schemas/instance.schema.json), or
  contribute a new importer.

Internally, the flow is:

```text
your academic files
    -> format importer
    -> TIG-compatible instance
    -> validation using official TIG code
    -> versioned local dataset
    -> TIG or local algorithm
```

The importer checks the file strictly and keeps its relative path, SHA-256 hash,
and importer version for traceability. Academic instances do not have a
generation seed because they already exist as fixed files.

Use `--metadata` with either an inline JSON object or a JSON file to record
attribution, licensing, best-known-solution maps, or other information for
later analysis. Metadata is stored with the dataset but never changes
validation or scoring. `generate` also supports `--metadata` and `--name`.

## Raw objectives and baselines

Run TIG's baseline solver for a challenge with:

```bash
python3 tig.py run \
  --dataset path/to/dataset.json \
  --baseline
```

The baseline produces a normal raw result. The runner does not automatically
compare it with another solver, a best-known solution, or TIG quality scaling.

| Challenge | Raw objective | Better result |
| --- | --- | --- |
| `c001` satisfiability | satisfied (`1` or `0`) | higher |
| `c002` vehicle routing | total distance | lower |
| `c003` knapsack | total value | higher |
| `c007` job scheduling | makespan | lower |
| `c008` energy arbitrage | total profit | higher |

## Randomness and repeated runs

There are two separate seeds:

- `generate --seed` decides which TIG instances are created.
- `run --algorithm-seed` controls random choices made by the solver while
  working on those unchanged instances.

The algorithm seed also defaults to `default_seed`. Change it when you want to
test the same algorithm and instances with different randomness:

```bash
python3 tig.py run \
  --dataset path/to/dataset.json \
  --algorithm c001_a001 \
  --algorithm-seed experiment_4 \
  --repetitions 5
```

Each repetition receives a different but repeatable random stream derived from
the seed, instance, and repetition number. The default is one run per instance.

## Hyperparameters

Hyperparameters are defined by each algorithm, not by `tig-evaluation-kit`. To find
the available settings and defaults, inspect the algorithm's `README.md` and
source code in the TIG monorepo, especially its `help()` function.
`algorithms list` shows the algorithm's friendly source name; for example,
`c007_a001` is named `adaptive_js`:

```bash
python3 tig.py algorithms list --challenge c007
```

Pass the chosen values as inline JSON or as the path to a JSON file. For
example, `c007_a001` documents an `effort` setting:

```bash
python3 tig.py run \
  --dataset path/to/dataset.json \
  --algorithm c007_a001 \
  --hyperparameters '{"effort": "high"}'
```

The runner passes this JSON to the algorithm unchanged. If you omit
`--hyperparameters`, the algorithm receives no custom values and can use its
own defaults. If the algorithm requires a value and has no default, that run
ends with `runtime_error`. The runner does not invent defaults or interpret an
algorithm's settings.

## Timing, saved solutions, and snapshots

`solver_wallclock_ms` measures only the algorithm call that produces solutions.
It excludes source downloads, compilation, process startup, instance parsing,
and objective evaluation. A solver may also stop early because of its own
stopping condition.

For the cleanest timing comparisons, use the default `--workers 1` and avoid
other heavy work on the machine. With a higher worker count, solver processes
run at the same time and compete for CPU time, cache, and memory bandwidth.
This can make individual wall-clock measurements slower or less consistent.
Multiple workers are useful when finishing a batch quickly matters more than
isolating each timing measurement. They do not make an individual algorithm
use multiple CPU cores.

Solutions can be large, so they are not saved by default. The runner still
keeps the latest one temporarily so it can calculate the objective.

```bash
# Save only the final solution
python3 tig.py run ... --save-solution

# Save every solution reported through the callback
python3 tig.py run ... --save-all-solutions

# Save the latest solution at selected solver times
python3 tig.py run ... --snapshot-times 1,5,10
python3 tig.py run ... --snapshot-interval 5
```

Repeated snapshots of an unchanged solution are not duplicated.

## Local algorithms

You can benchmark a local Rust module against the same instances and evaluator:

```bash
python3 tig.py run \
  --dataset path/to/dataset.json \
  --local path/to/algorithm/mod.rs \
  --hyperparameters '{"effort": 10}'
```

The module is source code; it does not need to be compiled beforehand. The
runner builds and caches it. CPU modules use TIG's callback interface:

```rust
pub fn solve_challenge(
    challenge: &Challenge,
    save_solution: &dyn Fn(&Solution) -> anyhow::Result<()>,
    hyperparameters: &Option<serde_json::Map<String, serde_json::Value>>,
) -> anyhow::Result<()>;
```

See the [local algorithm guide](docs/local-algorithms.md) for a template and a
full explanation of random seeds, callbacks, and build caching.

## Resuming an interrupted run

Use the same command and output directory with `--resume`:

```bash
python3 tig.py run ... --out runs/my-run --resume
```

Before reusing any completed result, the runner checks that it belongs to the
same source commit, dataset, algorithm settings, timeout, worker count,
repetitions, and capture settings. It also validates saved solution and
snapshot hashes. Missing, incomplete, or corrupt work is run again instead of
being trusted.

## Further documentation

Canonical JSON contracts are in [`schemas/`](schemas/). The detailed design and
verification plan is in
[`docs/benchmark-runner-plan.md`](docs/benchmark-runner-plan.md).

Users of the former `generate_dataset`, `run_algorithm`, or
`evaluate_solutions` commands should follow the
[migration guide](docs/migration.md). Existing knapsack, Solomon VRPTW, and
standard FJSP text instances can be normalized with `import`. Old quality
outputs are deliberately not mixed with raw-objective results.

## Current scope

Version 1 supports CPU challenges `c001`, `c002`, `c003`, `c007`, and `c008`.
Official algorithm availability is read from the pinned `main` commit. Local
algorithms and official baselines can still be used when a challenge has no
submitted algorithm exported on `main`.

Support for GPU/CUDA challenges is still planned, but it has not been
implemented. Version 1 is CPU-only. Algorithms from historical branches and
portable process-tree memory measurement are also planned for later versions.

Known limitation: monorepo commits from October 2026 onwards no longer let the
energy-arbitrage (`c008`) Challenge be read back from JSON, because it carries
a hidden seed that is deliberately not serialized. A cache synced to such a
commit cannot build the `c008` helper; the other CPU challenges are
unaffected. Supporting this needs `c008` instances to be regenerated from
their seed at run time, which is planned but not implemented.

## AI-assisted development

Coding agents should start with [`AGENTS.md`](AGENTS.md) for a concise map of
the repository, its design boundaries, and the relevant verification commands.
