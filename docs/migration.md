# Migration from the standalone copied-code runner

The authoritative pipeline uses `generate`, `import`, and `run`. The former
`generate_dataset`, `run_algorithm`, and `evaluate_solutions` commands and the
standalone Rust crate were removed because they compiled copied challenge,
baseline, and evaluator code that could drift from `tig-monorepo`.

## Existing text instances

Existing instance files remain usable. Normalize the directory once:

```bash
# Former knapsack graph text
python3 tig.py import --challenge c003 --input OLD_DATASET \
  --format tig-qkp --out datasets/my-knapsack

# Former Solomon-style vehicle-routing text
python3 tig.py import --challenge c002 --input OLD_DATASET \
  --format solomon-vrptw --out datasets/my-vrptw

# Standard old-style FJSP text, including Brandimarte and Hurink
python3 tig.py import --challenge c007 --input OLD_DATASET \
  --format standard-fjsp --out datasets/my-fjsp
```

`brandimarte-fjsp` remains accepted as a compatibility alias for
`standard-fjsp`.

`--format auto` recognizes these formats too, but an explicit value is useful
for a migration because it prevents ambiguous text from being guessed.

The import records the original file SHA-256 and normalizes derived fields with
the selected canonical monorepo commit. Imported academic/legacy problem data
has no generation seed; algorithm randomness is supplied separately at run
time.

## Generate new exact TIG datasets

An old generation configuration looked like:

```json
{
  "seed": "train",
  "n_nodes=600": 100,
  "n_nodes=700": 100
}
```

Generate one versioned dataset per track:

```bash
python3 tig.py generate --challenge c002 --track n_nodes=600 \
  --count 100 --seed train --out datasets/vehicle-routing/train-600
python3 tig.py generate --challenge c002 --track n_nodes=700 \
  --count 100 --seed train --out datasets/vehicle-routing/train-700
```

This intentionally does not reproduce the old files. The old generator used a
repository-local Blake3 recipe and copied challenge code. The new generator
uses canonical `BenchmarkSettings::calc_seed` and
`Challenge::generate_instance`, and records enough provenance to reproduce the
new dataset exactly.

## Run algorithms

Choose a submitted algorithm by stable code:

```bash
python3 tig.py run --dataset datasets/my-fjsp/dataset.json \
  --algorithm c007_a001 --workers 4 --timeout 120
```

Or move a locally developed `solve_challenge` module outside the removed
`src/<challenge>/algorithm/` tree and run it directly:

```bash
python3 tig.py run --dataset datasets/my-fjsp/dataset.json \
  --local algorithms/my_scheduler.rs
```

See [`local-algorithms.md`](local-algorithms.md) for the module contract and
template.

## Old solutions and CSV output

Old `.solution` files use challenge-specific text serialization, while the new
pipeline stores optional canonical JSON solution sidecars. Old evaluator
outputs may also be TIG quality values rather than raw objectives. They are not
silently imported or combined with new results.

Rerun the desired solver on the normalized dataset to obtain a versioned raw
result. `results.jsonl` can be loaded directly by Python, R, DuckDB, Polars, or
other analysis tools. Comparisons against an old solver, BKS, or TIG baseline
should be joined explicitly by instance ID/source hash.

## Source updates

Normal commands reuse the pinned cached commit. Updating source is always
explicit:

```bash
python3 tig.py source status
python3 tig.py source sync
```

Previously generated datasets continue to request their recorded commit after
a sync, so source updates do not silently change an existing experiment.
