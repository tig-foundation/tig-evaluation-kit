# tig-challenges agent guide

## Purpose and boundaries

`tig-challenges` is an offline benchmark runner. It generates or imports
datasets, runs official TIG or local Rust algorithms, validates their solutions,
and records raw objectives and solver-only wall-clock time.

Keep these boundaries intact:

- The official `tig-monorepo` is authoritative for Challenge, Solution,
  evaluator, baseline, and submitted-algorithm code. Do not copy those
  implementations into this repository.
- Version 1 uses algorithms exported from the official monorepo's `main`
  branch. Historical branches are future work.
- Version 1 is CPU-only. GPU/CUDA support is planned but not implemented.
- This is not `tig-runtime`: do not add fuel metering or live-network instance
  replay to the offline runner.
- Report raw objectives. Comparisons with a TIG baseline, a best-known
  solution, or TIG quality scaling belong to the user or an integration layer.
- Academic benchmark collections are user data. Do not commit downloaded,
  imported, or generated datasets.
- Local algorithms are trusted native code. Process isolation handles ordinary
  crashes and timeouts; it is not a security sandbox.

## Repository map

- `tig.py` — public command-line interface.
- `tig_bench/source.py` — managed official-monorepo synchronization and pinned
  clean checkouts.
- `tig_bench/catalog.py` — stable algorithm-code discovery and challenge names.
- `tig_bench/build.py` — content-addressed Rust helper and solver builds.
- `tig_bench/dataset.py` — deterministic TIG instance generation and dataset
  manifests.
- `tig_bench/importers.py` — academic-format detection, parsing, normalization,
  and import provenance.
- `tig_bench/runner.py` — run configuration, work scheduling, result creation,
  and strict resume validation.
- `tig_bench/process.py` — native solver supervision, timeout handling, callback
  collection, and snapshots.
- `tig_bench/templates/` — Rust harnesses compiled against the pinned monorepo.
- `schemas/` — canonical JSON contracts.
- `examples/local/` — local Rust algorithm examples and template.
- `docs/local-algorithms.md` — local algorithm contract and RNG behavior.
- `docs/benchmark-runner-plan.md` — detailed design decisions and deferred work.
- `docs/migration.md` — migration from the former commands.
- `tests/fixtures/` — small format fixtures used only by tests.
- `tests/` — unit, schema, repository-boundary, and opt-in native integration
  tests.

## Where to make changes

- For a CLI option, update `tig.py`, its help text, relevant tests, and README
  examples together.
- For an academic format, update `tig_bench/importers.py`, add a minimal fixture
  and parser tests, and update the supported-formats table in `README.md`.
- For generation or dataset identity, update `tig_bench/dataset.py` and preserve
  deterministic seeds, atomic output, provenance, and schema compatibility.
- For algorithm discovery or source behavior, preserve the official-repository
  authority boundary in `source.py` and `catalog.py`. Public source overrides
  are intentionally unsupported in version 1.
- For execution, timing, solution capture, or resume behavior, inspect
  `runner.py`, `process.py`, the Rust harness templates, and the corresponding
  JSON schemas as one contract.
- For a Challenge or algorithm interface change, inspect the exact pinned
  official monorepo source instead of guessing from copied code or memory.

## User-facing behavior to preserve

- `source sync` is optional: commands initialize the source cache on first use.
  Normal commands do not silently advance an existing pinned commit.
- Generation defaults to the known seed `default_seed`; `--start-nonce`
  selects the first deterministic instance index.
- Dataset-generation randomness and algorithm randomness are separate.
- Missing hyperparameters are left to the algorithm's own defaults and
  validation.
- Solver timing excludes synchronization, compilation, process startup,
  instance parsing, and objective evaluation.
- `--workers 1` is the default and the recommended setting for clean timing;
  higher values run separate instances concurrently.
- Solutions are not saved by default. Requested solutions and snapshots must
  retain their recorded hashes.
- Resume must never trust incomplete, mismatched, or corrupt work.
- `peak_memory_bytes` remains nullable until portable memory measurement is
  implemented.

## Data and working-tree safety

`datasets/` is ignored by Git. Users may also keep untracked benchmark and run
artifacts in the working tree. Treat all such files as user-owned: do not edit,
delete, move, or commit them unless explicitly asked. Stage only files relevant
to the requested change.

Importers must preserve problem semantics, reject malformed or ambiguous input,
validate normalized instances with official TIG code, and record adapter
version, relative source path, and source SHA-256.

## Verification

Run the full normal suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

Also run `git diff --check` and review `git status` before handing work back.

The native integration suite is opt-in because it compiles Rust against a local
monorepo checkout:

```bash
TIG_INTEGRATION_MONOREPO=/path/to/clean/tig-monorepo \
python3 -m unittest tests.test_integration -v
```

For changes affecting a challenge pipeline, exercise a small end-to-end case:
create or import a dataset, run a baseline or local/submitted algorithm, and
check the result status, raw objective direction/value, solver timing, and any
saved sidecar hashes. If a full benchmark or GPU test is not practical, report
exactly what was and was not verified.
