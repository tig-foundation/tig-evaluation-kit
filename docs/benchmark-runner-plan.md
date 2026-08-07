# Exact-source benchmark runner plan

Status: the core CPU v1 runner and Phase E migration are implemented on
`feature/exact-tig-benchmarking`. Phase F/CUDA is explicitly deferred until much
later; remaining convenience and verification follow-ons are documented below.

Branch: `feature/exact-tig-benchmarking`

Current phase state:

- Phase A: complete for the CPU runner.
- Phase B: complete for CPU v1, with native objective smoke coverage across all
  five CPU challenges.
- Phase C: core import boundary and listed adapters complete; additional
  downloaders and adapters remain optional follow-ons.
- Phase D: core durable results, capture, workers, repetitions, and resume
  complete; convenience exports remain optional.
- Phase E: complete, including removal of the copied Rust crate and explicit
  migration guidance.
- Phase F: deferred until much later and excluded from the current completion
  target.

## 1. Goal

Turn this repository into the offline benchmark runner for TIG algorithms. A user should be able to select an algorithm from the current `main` branch of `tig-monorepo` by its stable code (for example, `c001_a002`) or select a local algorithm, run it natively on exact TIG-generated or imported academic instances, and receive reproducible raw benchmark results.

The runner is not a reimplementation of the TIG runtime. It will not instrument fuel, reproduce runtime signatures, or include build time in solver time. It executes trusted algorithm source as native code.

This repository remains named `tig-challenges`, but it must not own copies of TIG challenge, verification, algorithm, or baseline implementations. `tig-monorepo` is authoritative for all of those.

## 2. Resolved product decisions

The following are requirements, not open design questions:

1. Run native source code, without `tig-runtime` and without fuel accounting.
2. Resolve monorepo algorithms by stable `cNNN_aNNN` code. Friendly module names may be accepted as aliases, but the stable code is always recorded.
3. Initially expose algorithms present on the selected `main` commit. The resolver and cache must be revision-aware so historical algorithm branches can be added later without redesigning the run pipeline.
4. Treat a clean, committed `tig-monorepo` revision as the only authority. Never compile copied challenge or algorithm code, and never accidentally compile uncommitted changes from a user's monorepo working tree.
5. Generate deterministic TIG instances with the canonical seed derivation and
   `Challenge::generate_instance`. Generated instances can be retained as
   reusable datasets. The offline interface uses a simple `--seed` string,
   defaulting to `default_seed`; replaying live-network instances is out of
   scope.
6. Make default generation deterministic: the same tool/monorepo revision, challenge, track, generation seed, starting nonce, and count produce byte-identical normalized instances and the same manifest.
7. Document a canonical instance envelope and provide built-in adapters for common academic formats. Users may also convert their data to the canonical envelope themselves.
8. Report the raw challenge objective. Do not calculate TIG quality or silently compare against a baseline or BKS.
9. Permit a canonical TIG baseline to be run as another solver where the authoritative challenge exposes one. Any comparison or TIG scaling is a separate user operation.
10. Preserve the existing local algorithm contract for CPU challenges:

   ```rust
   solve_challenge(
       challenge: &Challenge,
       save_solution: &dyn Fn(&Solution) -> anyhow::Result<()>,
       hyperparameters: &Option<serde_json::Map<String, serde_json::Value>>,
   ) -> anyhow::Result<()>
   ```

11. Pass hyperparameters with TIG's JSON-map semantics. If values are omitted, pass `None` or the partial map and let the canonical algorithm apply its own defaults. If an algorithm has no applicable default, its normal error becomes `runtime_error`; the runner will not invent one.
12. Give solver randomness a separate `--algorithm-seed` string, defaulting to `default_seed`. Changing it changes an algorithm's randomized choices on the same stored instance; it does not regenerate or modify the problem data.
13. Measure wall-clock time around solver execution only. Exclude source resolution, checkout, build, process startup, instance parsing/materialization, and objective evaluation. A solver may finish before its timeout because of its own stopping condition.
14. Support every `save_solution` callback event. By default, retain no solution files. Options will retain the final solution, every callback solution, and/or wall-clock snapshots.
15. Default to `--workers 1`; workers parallelize independent instance/repetition processes. The runner does not parallelize an algorithm internally.
16. Support repetitions, defaulting to one.
17. Use JSON as the canonical result format.
18. Cache builds by source and environment identity. Build time is never included in solver wall-clock time.
19. Resume interrupted runs safely.
20. Record minimal useful environment provenance, not an exhaustive system inventory.
21. Defer portable peak-memory measurement. Reserve a nullable result field so it can be added without changing the result schema.
22. Leave `tig-SOTA-metrics` unchanged. Selectively port its academic dataset adapters, source metadata, and BKS organization; do not port its copied evaluators or baselines.

## 3. Scope and staging

### Version 1: CPU challenges

The first complete vertical slice should cover all CPU challenges currently in monorepo `main`:

| ID | Challenge | Raw objective | Direction |
| --- | --- | --- | --- |
| `c001` | satisfiability | satisfied (`1` or `0`) | maximize |
| `c002` | vehicle routing | total distance | minimize |
| `c003` | knapsack | total value | maximize |
| `c007` | job scheduling | makespan | minimize |
| `c008` | energy arbitrage | total profit | maximize |

The evaluator must call the canonical raw objective/validation method, not `evaluate_solution` when that method applies TIG baseline thresholds or quality scaling. In particular, a feasible job-scheduling solution worse than the greedy baseline is still a valid raw benchmark result.

### Version 2: CUDA challenges

The architecture must leave room for `c004`, `c005`, and `c006`, but they should follow the CPU vertical slice:

- Their canonical `Challenge` values contain device allocations and cannot currently be serialized like CPU challenges.
- Saved datasets need a host-side representation or a reproducible generation recipe that can be materialized on a chosen GPU.
- Solver timing must synchronize the CUDA stream before stopping the clock.
- `c004` and `c005` expose usable raw objectives. `c006` currently exposes TIG quality but not a public raw test-loss method, so exact raw scoring needs a small canonical API addition in `tig-monorepo` rather than duplicated evaluator logic here.

CUDA support is an implementation phase, not a reason to couple the CPU runner to TIG runtime/fuel.

### Later extensions

- Historical algorithm branches using the same revision-aware source resolver and build cache.
- Portable peak process-tree RSS and GPU-memory measurements.
- Additional academic adapters.
- Optional reporting/export commands for comparisons and TIG-quality scaling. These consume raw results and do not alter run records.

## 4. Authority and source resolution

### Clean source snapshots

The runner resolves a Git commit on `main`, creates or reuses an isolated clean checkout in its cache, and compiles only that snapshot. On first use this happens automatically from the official `https://github.com/tig-foundation/tig-monorepo`. Users do not need to clone or prepare the monorepo themselves. Version 1 does not expose arbitrary repository or fork selection.

Normal benchmark execution must not silently fetch a newer commit. After automatic first-use initialization, an explicit source-sync operation updates the cached view of official `main`; every dataset, build, and run records the resolved commit SHA. Resume requires the original SHA. Dataset runs already resolve their recorded commit rather than current `main`; historical revision selection can therefore be added later without changing the pipeline.

Suggested commands:

```text
python3 tig.py source sync
python3 tig.py algorithms list [--challenge c002]
```

### Algorithm catalog

For each challenge module, the catalog derives mappings from canonical declarations such as:

```rust
pub mod fast_lane_v4;
pub use fast_lane_v4 as c002_a103;
```

Both `c002_a103` and `fast_lane_v4` may select the algorithm. Ambiguous friendly names fail. Result records always use `c002_a103`, `fast_lane_v4`, and the monorepo SHA. Catalog parsing is tested against all modules on the selected commit and fails closed when it encounters an unsupported declaration.

### Local algorithms

A local algorithm is selected with its source path; its challenge comes from the dataset. The CLI accepts any explicit Rust module file, with reusable examples under `examples/local/`. The module and its sibling Rust files are hashed into the build identity. Local code uses canonical monorepo `Challenge` and `Solution` types and the same callback/hyperparameter contract.

The former standalone package also used the name `tig-challenges` and contained
copied challenge implementations. It has been removed. Generated helper crates
have distinct names, so `tig-challenges` in a harness now unambiguously means
the canonical monorepo crate.

## 5. Process architecture

Use Python for CLI orchestration, process supervision, worker scheduling, manifests, resume, and build-cache selection. Use small Rust helpers for canonical type-dependent work.

There must be two Rust process roles:

1. **Solver harness** — depends on the selected canonical algorithm source and therefore on `tig-challenges` with `hide_verification`. It deserializes/materializes one instance, announces that it is ready, invokes `solve_challenge`, and streams callback events and a completion event to the parent.
2. **Instance/evaluator helper** — depends on canonical `tig-challenges` without `tig-algorithms` and without `hide_verification`. It generates/imports instances, validates solutions, computes raw objectives, and invokes canonical baselines.

Cargo features are unified across a dependency graph. A single binary depending on `tig-algorithms` would enable `hide_verification` and lose access to canonical objective/baseline methods. Separate processes avoid that conflict without copying code or changing TIG semantics.

The parent starts the timeout only after the solver has parsed/materialized the instance and emitted `ready`. For completed CPU calls, the harness measures an internal monotonic duration immediately around `solve_challenge`. For a killed process, the parent uses the monotonic duration since `ready`.

Builds happen before any instance timer starts. Build cache keys include at least:

- tool revision;
- monorepo commit SHA;
- challenge and algorithm ID, or local source/dependency hashes;
- Cargo lockfile and Rust toolchain identity;
- release/debug profile;
- target architecture; and
- CUDA toolchain/device target when CUDA is added.

## 6. Instance and dataset contracts

### Canonical instance envelope

Normalized CPU instances are UTF-8 JSON with a versioned envelope:

```json
{
  "schema_version": "tig.instance/v1",
  "instance_id": "000000",
  "challenge_id": "c007",
  "challenge": {},
  "provenance": {
    "kind": "tig_generated",
    "monorepo_commit": "<git sha>",
    "source": null,
    "source_sha256": null,
    "adapter": null,
    "generation": {}
  }
}
```

`challenge` is the canonical monorepo `Challenge` serialization at the recorded commit. The envelope prevents a bare JSON object from being interpreted against the wrong challenge or revision. Each normalized file has a SHA-256 recorded in the dataset manifest.

An imported academic instance has no generation seed in its public provenance. When a canonical CPU `Challenge` requires a `seed` field for serialization, the adapter uses `[0; 32]` as an inert compatibility value and records it as such. The solver replaces this field in its private in-memory copy with the effective algorithm RNG seed immediately before calling the algorithm. The normalized instance file and the evaluator's instance remain unchanged.

### Dataset manifest

Every generated or imported collection has a `dataset.json` manifest containing:

- schema version, dataset ID/name, and challenge ID;
- monorepo commit used for normalization;
- ordered instance IDs, relative paths, and SHA-256 hashes;
- dataset provenance and license/source fields when known;
- exact TIG generation recipe, if applicable; and
- optional joinable metadata such as BKS, without using it during evaluation.

The ordered manifest, rather than a recursive `*.txt` glob, defines a dataset. A convenience command can create a manifest from individual supported files.

### Exact TIG generation

The normal offline generator accepts a friendly seed string, starting nonce, count, and track:

```text
python3 tig.py generate --challenge c002 --track n_nodes=1000 --count 100
python3 tig.py generate --challenge c002 --track n_nodes=1000 --count 100 --seed experiment_2
```

`--seed` defaults to `default_seed`. The runner combines it with each nonce
through canonical `BenchmarkSettings::calc_seed`; the other settings fields are
fixed and versioned for this offline tool:

```text
player_id   = "tig-challenges"
block_id    = "offline-v1"
algorithm_id = "offline"
generation_seed = value of --seed (default "default_seed")
start_nonce = 0
```

`challenge_id` and canonical `track_id` come from the command. This gives the
simple seed behavior expected of an offline dataset tool while still using
TIG's canonical seed calculation and challenge generator. `--start-nonce`
selects the first deterministic instance index, allowing a dataset sequence to
be split into non-overlapping batches. Live-network instance replay is not
supported.

Generation writes instances in nonce order with stable IDs and canonical JSON serialization. Tests pin representative derived seeds and normalized file hashes.

### Academic adapters

Version 1 should provide these built-in input paths:

- any CPU challenge: already-normalized `tig.instance/v1` JSON;
- `c001`: DIMACS CNF;
- `c002`: Solomon and Homberger/Gehring VRPTW text;
- `c003`: the QKP variants already handled by the `origin/dev` download/conversion work, normalized into canonical knapsack fields;
- `c007`: Brandimarte FJSP and classic JSP text;
- `c008`: canonical JSON initially.

Format selection may be explicit (`--format`) with conservative auto-detection for distinctive formats. Auto-detection must report the selected adapter and fail on ambiguity. Every adapter validates dimensions and indices, populates derived canonical fields consistently, and records its name/version plus the original file hash. Challenge-internal greedy baseline fields are computed with canonical code; BKS values are metadata and are never substituted for those fields.

The adapter boundary is public and documented so users can contribute another adapter or emit the canonical envelope themselves. Academic downloaders are convenience tooling, not required at run time.

Both generated and imported datasets accept an optional JSON metadata object at
creation time. It can hold attribution, licensing, BKS maps, and external
identifiers without participating in evaluation or solver status.

## 7. Solver selection and hyperparameters

Suggested run forms:

```text
python3 tig.py run --algorithm c002_a103 --dataset path/to/dataset.json
python3 tig.py run --algorithm fast_lane_v4 --dataset path/to/dataset.json
python3 tig.py run --local src/vehicle_routing/algorithm/mod.rs --dataset path/to/dataset.json
python3 tig.py run --baseline --dataset path/to/dataset.json
```

Hyperparameters accept either an inline JSON object or a JSON file. An omitted option is passed as `None`; `{}` remains an explicitly empty map. The run manifest records the exact distinction. The runner only validates that the value is a JSON object. Algorithm-specific defaults and validation stay in authoritative algorithm source.

There is currently no machine-readable default/schema registry in `tig-monorepo`; `help()` is human-readable and algorithms apply defaults internally. Proactive required-field validation would therefore drift. A later monorepo metadata API could improve preflight validation without changing this runner contract.

### Algorithm randomness

`--algorithm-seed` is independent of the dataset-generation `--seed` and defaults to `default_seed`. For each work item, the runner deterministically derives the 32-byte value passed in the solver's private `Challenge.seed` from:

```text
algorithm seed string + normalized instance hash + repetition index
```

Domain separation and an explicitly versioned hash recipe prevent collisions with instance generation. This gives different deterministic random streams per instance and per repetition. Re-running with the same algorithm seed, dataset, and repetition count reproduces those streams; changing only `--algorithm-seed` reruns randomized algorithms differently against byte-identical stored challenge data.

The dataset retains its original TIG generation seed, if any. The evaluator always uses the unmodified stored instance. Run results record both the user-facing algorithm seed string and each effective 32-byte seed, so this controlled departure from live TIG execution is explicit and reproducible.

## 8. Callback capture, snapshots, and solution retention

The child emits a framed event for every successful `save_solution` callback. The parent drains events continuously, so the latest solution remains available even if the child is killed at timeout. This avoids requiring a retained on-disk solution merely to report a timeout result.

Capture modes:

- default: keep only the latest callback in memory long enough to evaluate it, then discard it;
- `--save-solution`: retain the last callback solution;
- `--save-all-solutions`: retain every callback solution in callback order;
- `--snapshot-interval SECONDS`: retain the latest available callback solution at each interval;
- `--snapshot-times T1,T2,...`: retain the latest available callback solution at those elapsed solver times.

`--snapshot-times` and `--snapshot-interval` are mutually exclusive. A snapshot is associated with the callback it sampled; do not create duplicate files when no newer callback exists unless an explicit future compatibility option requests it.

Solution serialization and callback transport happen inside the solver invocation and therefore contribute to wall-clock time. This is honest: the algorithm chose to publish that solution. Objective evaluation and optional final filesystem retention happen after the timer and are excluded. The result records callback count and capture mode so timing experiments are interpretable.

Solutions are JSON sidecars, never embedded in result rows. This keeps result aggregation manageable when solutions are large.

## 9. Result contract

Each instance/repetition produces one versioned JSON object with at least:

```json
{
  "schema_version": "tig.result/v1",
  "run_id": "...",
  "result_id": "...",
  "challenge_id": "c002",
  "algorithm": {
    "kind": "monorepo",
    "id": "c002_a103",
    "name": "fast_lane_v4",
    "monorepo_commit": "<git sha>",
    "local_source_sha256": null
  },
  "instance": {
    "dataset_id": "...",
    "id": "...",
    "sha256": "..."
  },
  "repetition": 0,
  "algorithm_seed": {
    "base": "default_seed",
    "effective": "<64 lowercase hex characters>"
  },
  "hyperparameters": null,
  "status": "solution",
  "solver_wallclock_ms": 1234.5,
  "objective": {
    "value": 12345,
    "direction": "minimize"
  },
  "callback_count": 3,
  "solution": null,
  "solution_sha256": null,
  "saved_solutions": [],
  "snapshots": [],
  "peak_memory_bytes": null,
  "error": null
}
```

Floating-point objective values are JSON numbers when finite. Any future non-finite value is an evaluation error, not a non-standard JSON token.

The versioned schema reserves these terminal statuses:

- `solution`
- `no_solution`
- `invalid_solution`
- `timeout_with_solution`
- `timeout_without_solution`
- `runtime_error`
- `build_error`
- `unsupported_instance`
- `host_out_of_memory`
- `gpu_out_of_memory`

There is no fuel status. CPU v1 emits solution, no-solution, invalid-solution,
timeout, and runtime-error variants. Build and unsupported-input failures currently
fail during preflight; the build/unsupported statuses allow a future batch mode to
materialize those failures per work item. OOM classification is deferred with
memory measurement and CUDA. Error objects contain a stable category and concise
message; per-process logs may contain details. A timeout with a valid latest
callback retains its raw objective whether or not its solution sidecar is retained.

## 10. Run storage, concurrency, and resume

Use a run directory such as:

```text
runs/<run-id>/
  manifest.json
  results/<result-id>.json
  results.jsonl
  solutions/                 # only when requested
  snapshots/                 # only when requested
  logs/
```

`manifest.json` stores run-wide configuration and minimal provenance: tool commit, monorepo commit, algorithm identity, dataset identity, exact hyperparameters, base algorithm seed and derivation version, timeout, workers, repetitions, capture mode, OS, architecture, Rust version, and a short CPU/GPU description when available.

Each worker writes a temporary per-result JSON file and atomically renames it on completion. Resume verifies the immutable run fingerprint, the complete result structure and identity, and the content hashes of any solution or snapshot sidecars before skipping a terminal result. Missing or corrupt work is rerun. It rejects changed algorithm source, monorepo SHA, dataset hashes, hyperparameters, timeout, repetition count, or capture settings. `results.jsonl` is a deterministic materialized view rebuilt from ordered per-result files, so an interrupted append cannot corrupt the source of truth.

`--workers` defaults to one. Each work item is one isolated process for one instance and repetition. Repetitions default to one and have stable result IDs. Results are materialized in dataset/repetition order regardless of completion order.

Algorithms are trusted native code. Process isolation provides timeout and crash containment, not a security sandbox.

## 11. Baselines and external comparisons

Where a challenge exposes a canonical baseline implementation at the selected monorepo SHA, `--baseline` runs it through the same result pipeline and produces the same raw objective schema. It must not automatically subtract, divide, rank, normalize, or calculate TIG quality.

Imported BKS values and external solver results may be attached to dataset metadata keyed by instance ID. A simple export/join command may make analysis convenient, but those fields do not affect validation or the primary objective. This keeps one run record useful for comparisons against a BKS, a TIG baseline, another TIG algorithm, or a user's solver without blessing one comparison.

## 12. Migration from the copied-code runner

Keep `python3 tig.py` as the entry point while splitting implementation into a
Python package and thin top-level wrapper.

The former `generate_dataset`, `run_algorithm`, and `evaluate_solutions`
commands cannot be honest wrappers: generation used a different seed recipe,
the solver compiled an embedded local module, and evaluation often returned TIG
quality rather than a raw objective. They are removed with an explicit
[`migration.md`](migration.md) guide. Old copied `.txt` datasets use the public
import adapters; they are never silently reinterpreted.

The copied challenge, baseline, evaluator, generator, and solver Rust crate is
removed after canonical generation, solving, and raw evaluation pass end-to-end
tests. A repository policy test prevents it from being reintroduced. Useful
academic downloaders and BKS files from `origin/dev` can still be ported
selectively with provenance and licensing notes rather than merging the branch
wholesale.

## 13. Implementation phases

### Phase A — contracts and source authority

- Add versioned JSON schemas/examples for dataset manifests, instance envelopes, run manifests, results, and solver events.
- Add clean monorepo snapshot resolution, explicit sync, SHA pinning, and algorithm catalog/listing.
- Restructure/rename standalone Rust packages so no local crate impersonates canonical `tig-challenges`.
- Add content-addressed build-cache primitives.

Exit criterion: listing algorithms by ID/name comes only from a clean pinned `main` snapshot, including when the sibling monorepo working tree is dirty.

### Phase B — CPU generation, solving, and raw evaluation

- Implement the separate canonical instance/evaluator helper and generated solver harnesses.
- Support monorepo algorithms, local algorithm modules, and canonical baselines for `c001`, `c002`, `c003`, `c007`, and `c008`.
- Implement the simple offline generation seed, starting instance index,
  separate algorithm RNG seed derivation, and deterministic saved datasets.
- Implement hyperparameter pass-through, ready/event protocol, solver-only wall-clock timing, timeout, and the CPU v1 status subset.

Exit criterion: representative submitted algorithms where exported, plus local
or canonical baseline paths across every CPU challenge, run on saved exact TIG
instances and report independently verified raw objectives.

### Phase C — academic datasets

- Implement the canonical import boundary and initial adapters listed above.
- Port useful download/conversion/BKS work from `origin/dev` and, where appropriate, ideas from `tig-SOTA-metrics`.
- Add original-source hashes, adapter versions, attribution/license metadata, and validation fixtures.

Exit criterion: at least one established academic dataset for satisfiability, vehicle routing, knapsack, and job scheduling can be imported and run by both a named TIG algorithm and a local algorithm.

### Phase D — durable experiment data

- Implement capture modes, snapshots, optional sidecars, repetitions, workers, atomic per-result files, deterministic JSONL materialization, logs, and resume.
- Add optional convenience exports without changing canonical JSON.

Exit criterion: killing and resuming a multi-worker run neither loses completed results nor duplicates result IDs; timeout snapshots remain evaluable.

### Phase E — migration and documentation

Implemented for CPU v1.

- Resolve former-command compatibility explicitly and add migration documentation.
- Remove copied challenge/baseline code after parity coverage exists.
- Document the local algorithm template, supported formats, objective meanings/directions, reproducibility boundaries, and examples.

### Phase F — CUDA and later work

Explicitly deferred until much later. It is not part of the current completion
target.

- Design canonical host representations/materializers for `c004`-`c006` with the monorepo.
- Expose canonical raw test loss for `c006` upstream.
- Add CUDA build identities, device selection, stream synchronization, statuses, and GPU provenance.
- Add historical branch selection and reliable memory accounting as separate enhancements.

## 14. Verification plan

Automated tests must cover:

1. **No drift:** runner crates import challenge/solution/baseline behavior only from the pinned monorepo snapshot; a repository check prevents new copied implementations.
2. **Catalog integrity:** every exported main-branch algorithm ID maps to the expected module; IDs and friendly aliases select identical source.
3. **Dirty-tree isolation:** uncommitted sibling monorepo edits do not affect catalog, build hash, generated instances, or results.
4. **Exact generation:** known offline seed/nonce vectors match canonical seed
   derivation; repeated dataset generation yields identical ordered hashes.
5. **Academic normalization:** fixtures for every adapter produce canonical fields, derived fields, and source hashes; malformed/ambiguous input fails clearly.
6. **Raw objectives:** hand-checkable fixtures validate value and direction for every supported CPU challenge, including a feasible job schedule worse than baseline.
7. **Algorithm parity:** a named algorithm selected by code and alias produces the same callback sequence under the same source SHA and inputs.
8. **Local interface:** a local algorithm using omitted defaults, partial parameters, explicit `{}`, no callback, multiple callbacks, an error, and a panic receives the correct result.
9. **Independent solver randomness:** changing `--algorithm-seed` changes the effective seed but not the normalized instance hash; identical inputs reproduce effective seeds; repetition indices create deterministic distinct streams.
10. **Timing boundary:** injected build/startup/parse/evaluation delays do not change `solver_wallclock_ms`; injected solver delay does.
11. **Timeout recovery:** both timeout statuses work and the most recent callback can be evaluated after the worker is killed.
12. **Capture semantics:** default runs retain no solution files; final/all/timed capture modes retain exactly the documented events.
13. **Parallel/repeated runs:** workers never share mutable algorithm state, result identities are stable, and output ordering is deterministic.
14. **Resume:** completed atomic results are reused only under an identical run fingerprint; partial files are ignored safely.
15. **Build cache:** identical source identities reuse builds; changed monorepo/local source/toolchain inputs miss the cache; build duration never enters solver time.
16. **Baseline neutrality:** a baseline result is ordinary raw output, and adding BKS metadata cannot change solver status or objective.

End-to-end CI should use small fixtures and fast algorithms. Larger academic and CUDA smoke tests can run separately where the required data/hardware exists.

## 15. Review decisions

Confirmed or revised after initial review:

1. Version 1 supports CPU challenges; CUDA follows later.
2. Offline generation has a simple `--seed` option defaulting to
   `default_seed`; live-network instance reproduction is deliberately not
   supported.
3. Algorithm randomness has an independent `--algorithm-seed` option defaulting to `default_seed`. It can vary randomized solver behavior without changing stored instance data.
4. Optimize source management for minimal user friction: automatically prepare a clean pinned monorepo copy on first use, reuse it for normal runs, and advance it only through explicit `source sync`. Local repository and commit selection are advanced options.
5. Complete the CPU migration and removal of copied implementations before any
   CUDA work. Phase F is deferred until much later.

All product-level assumptions needed to begin implementation are resolved.
