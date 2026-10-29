# Local algorithm modules

A local algorithm is one Rust module implementing the same function used by
submitted TIG CPU algorithms:

```rust
pub fn solve_challenge(
    challenge: &Challenge,
    save_solution: &dyn Fn(&Solution) -> anyhow::Result<()>,
    hyperparameters: &Option<serde_json::Map<String, serde_json::Value>>,
) -> anyhow::Result<()>;
```

Start from [`examples/local/template.rs`](../examples/local/template.rs). A
small compilable satisfiability example is in
[`examples/local/satisfiability.rs`](../examples/local/satisfiability.rs). The
[`job_scheduling_delayed.rs`](../examples/local/job_scheduling_delayed.rs)
example intentionally publishes a feasible schedule worse than the baseline,
illustrating that the runner accepts it and reports its raw makespan.

Run a module with:

```bash
python3 tig.py run \
  --dataset path/to/dataset.json \
  --local path/to/my_algorithm.rs \
  --hyperparameters '{"effort": 10}'
```

The dataset determines the challenge type. The generated harness provides
`Challenge`, `Solution`, `anyhow`, `serde`, `serde_json`, `rand`,
`rand_distr`, `ndarray`, and `ahash`, matching the common imports used by TIG
CPU modules. A standalone `Cargo.toml` is not required.

All sibling and nested `*.rs` files under the selected file's directory are
included in the local-source hash. This allows either `mod.rs` or another Rust
entry filename to use submodules without stale cached builds. Put unrelated
Rust modules in separate directories when they should have independent build
identities.

## Hyperparameters

Omitting `--hyperparameters` passes `None`. Passing `{}` passes an explicitly
empty map. The module owns its defaults and validation; the runner does not
invent defaults.

A typical parser is:

```rust
#[derive(serde::Deserialize)]
struct Hyperparameters {
    #[serde(default = "default_effort")]
    effort: usize,
}

let parameters: Hyperparameters = serde_json::from_value(
    serde_json::Value::Object(hyperparameters.clone().unwrap_or_default()),
)?;
```

## Randomness

The runner derives a deterministic 32-byte seed from `--algorithm-seed`, the
normalized instance hash, and the repetition index. Immediately before the
call it places that value in the private in-memory `challenge.seed`. The saved
instance and evaluator input are never changed.

Algorithms should initialize their RNG from `challenge.seed`. Changing
`--algorithm-seed` then changes algorithm randomness without changing the
problem instance.

## Publishing solutions

Call `save_solution` whenever a candidate should be observable. Every callback
is counted and transported while the solver clock is running. The latest valid
callback is evaluated even if the process later times out.

By default, solution data is discarded after evaluation. Use
`--save-solution`, `--save-all-solutions`, `--snapshot-times`, or
`--snapshot-interval` when retention is required.

Returning without a callback produces `no_solution`. Returning an error or
panicking produces `runtime_error`; a previously published valid solution can
still have a raw objective in that result.

Local modules execute as trusted native code. Process isolation contains
ordinary crashes and timeouts but is not a security sandbox.

## Fuel

The runner does not meter fuel. The solver harness exports the
`__fuel_remaining` symbol that `tig-runtime` provides, so official or local
code declaring `extern "C" { static __fuel_remaining: u64; }` links, but the
value stays at a constant full budget (the network's maximum, 5 × 10^12) for
the whole run. Fuel-based pacing therefore never triggers; use the solver
timeout to bound running time.
