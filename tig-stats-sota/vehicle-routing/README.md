# Vehicle routing: TIG algorithms vs best-known solutions on Homberger-Gehring RC2

This folder is a self-contained experiment that benchmarks official TIG
vehicle-routing (`c002`, VRPTW) algorithms on the ten RC2 instances of the
Homberger-Gehring 1000-customer benchmark and reports how far each final
solution is from the published best-known solution (BKS).

RC2 was chosen because it is the hardest class in the sets surveyed: a long
scheduling horizon, wide time windows and only about 18 vehicles, so every
route carries 50 or more customers.

## Setup

| Item | Value |
| --- | --- |
| Instances | `RC2_10_1` to `RC2_10_10` from the SINTEF archive (`config.json` lists the URL and its SHA-256) |
| Import | `tig.py import --format solomon-vrptw` |
| Algorithms | `hgs_explore5` = `c002_a089` (`hgs_v1`) with `{"exploration_level": 5}`; `hgs_advance` = `c002_a110` as shipped; `hgs_epure` = `c002_a120` with the hyperparameters TIG benchmarkers use on the `n_nodes=1000` track (next section) |
| Budget | 30 s solver time per instance (`--timeout 30`), one worker, one repetition, algorithm seed `default_seed` |
| Monorepo | `d33946ddeaf89240254b0661aba70859f7868fe0`, tig-monorepo `main` on 2026-10-29, the commit recorded in `config.json` |
| BKS | `bks.json`, copied from the SINTEF best-known table on 2026-10-29 |

`hgs_v1` is run at exploration level 5 rather than as shipped because its
default preset is level 0, a single local search that finishes in about 0.3 s
and therefore ignores the 30 s budget. Level 5 is the author's deepest preset.

### hgs_epure hyperparameters

`hgs_epure` accepts hyperparameters, and TIG benchmarkers choose them per
precommit, so the values "currently used on TIG" were read from the network
rather than from the code. `tig_hyperparameters.json` holds every `c002_a120`
precommit returned by the mainnet API for the ten players listed as its
qualifiers at block height 1405410 (round 140, retrieved 2026-10-29), grouped
by track. On the `n_nodes=1000` track, the closest to these instances:

| Hyperparameters in the precommit | Precommits |
| --- | --- |
| `{"allow_swap3": true, "exploration_level": 4}` | 56 |
| none (algorithm defaults) | 14 |
| a custom set (`mu`, `lambda`, iteration caps, ...) | 2 |

The majority setting is used here. One detail matters for academic instances:
inside `hgs_epure`, the defaults for exploration level 4 add a built-in track
profile when the instance has exactly 1000 nodes (`TRACK_PROFILES` in
`params.rs`: `allow_swap3`, `granularity` 30/40, `swapstar_capa_filter` 0.5,
`max_credit_deterioration` 20, decomposition 200 nodes in 12 phases,
`l4_permille` 600). A Homberger instance has 1001 nodes (depot plus 1000
customers), so that profile would silently not apply. `config.json` therefore
passes the profile values explicitly together with the majority precommit
values, which reproduces the parameter set the algorithm actually runs with
on TIG's 1000-node track.

## Metric

For every instance the final saved solution is decoded, its routes are
re-measured with real-valued Euclidean distances from the instance
coordinates, and the gap is

```
gap % = (solution distance - BKS distance) / BKS distance * 100
```

Lower is better. The chart shows one dot per instance, a diamond at the mean
over the ten instances and a whisker of plus or minus one sample standard
deviation. The mean number of vehicles in the final solutions is noted under
each configuration, next to the BKS mean in the subtitle, so it is visible at
a glance that a negative gap is reached with a larger fleet.

Two caveats apply when reading the numbers against the literature:

- TIG rounds distances to integers and uses them as travel times, so a TIG
  solution can be slightly infeasible under real-valued time windows. The
  effect is small on RC2 because its windows are wide.
- TIG minimises distance only, subject to the fleet cap in the file (250
  vehicles here). The BKS minimises vehicles first. Negative gaps are
  obtained with more vehicles than the BKS uses; the table lists both.

## Results

![Gap to BKS](results/gap_to_bks.png)

| Instance | BKS vehicles | BKS distance | hgs_explore5 gap | hgs_explore5 vehicles | hgs_advance gap | hgs_advance vehicles | hgs_epure gap | hgs_epure vehicles |
|---|---|---|---|---|---|---|---|---|
| rc2_10_1 | 20 | 30276.27 | -0.91%* | 34 | -5.86% | 28 | -5.87%* | 29 |
| rc2_10_2 | 18 | 26104.09 | +0.42%* | 28 | -5.43%* | 25 | -4.63%* | 26 |
| rc2_10_3 | 18 | 19911.48 | +7.53%* | 27 | +1.22%* | 23 | +0.49%* | 23 |
| rc2_10_4 | 18 | 15693.28 | +12.56%* | 21 | +3.42%* | 20 | +5.20%* | 20 |
| rc2_10_5 | 18 | 27067.04 | +2.76%* | 26 | -2.89%* | 25 | -2.79%* | 25 |
| rc2_10_6 | 18 | 26741.27 | +3.60%* | 26 | -1.32% | 24 | -2.04%* | 25 |
| rc2_10_7 | 18 | 24999.66 | +5.17%* | 25 | +0.47%* | 23 | -0.63%* | 22 |
| rc2_10_8 | 18 | 23595.33 | +7.77%* | 24 | +0.40% | 21 | +1.89%* | 21 |
| rc2_10_9 | 18 | 22919.42 | +8.78%* | 23 | +1.39%* | 21 | +1.33%* | 21 |
| rc2_10_10 | 18 | 21834.94 | +9.19%* | 23 | +2.85%* | 20 | +2.27%* | 20 |
| **mean ± sd** | **18.2** | | **+5.69% ± 4.23** | **25.7 ± 3.6** | **-0.57% ± 3.23** | **23.0 ± 2.6** | **-0.48% ± 3.39** | **23.2 ± 3.0** |

`*` marks runs that were stopped by the 30 s timeout; the last solution saved
before the stop is the one measured. `hgs_explore5` and `hgs_epure` ran to the
timeout on every instance; `hgs_advance` stopped on its own criteria on three
(mean solver time 29.7 s). `hgs_epure` paces itself on TIG's fuel counter,
which this runner exports as a constant (see `docs/local-algorithms.md`), so
offline its running time is bounded only by the timeout.

Every configuration uses more vehicles than the BKS on every instance, which
is how `hgs_advance` and `hgs_epure` land below the BKS distance on
4 and 5 instances
respectively. With one run and one seed per instance the two are
indistinguishable on this set; their per-instance gaps track each other
closely.

`results/per_instance.csv` has every row, including TIG's integer objective
and the run status, and `results/summary.json` has the aggregates.

## Reproducing

From the repository root, with Rust and `cargo` installed:

```bash
python3 -m pip install -r tig-stats-sota/vehicle-routing/requirements.txt   # matplotlib, chart only
python3 tig-stats-sota/vehicle-routing/run_experiment.py
```

`run_experiment.py` initialises a kit cache under `data/cache` (a mirror of
the monorepo plus the Rust builds, under a gigabyte), downloads the archive
into `data/`, extracts and imports the ten instances, runs each configuration
from `config.json` into `runs/<label>/`, then calls `analyze.py`, which writes
`results/`. Steps whose output already exists are skipped; use `--force-run`
to solve again, `--only <label>` to run one configuration, and
`python3 analyze.py` on its own to rebuild the tables and chart.

The experiment uses its own cache rather than the kit's default one because
`hgs_epure` only exists in monorepo commits newer than the one most
installations are pinned to, and `tig.py source sync` on the default cache
would also move every dataset generated afterwards. The cache is pinned to
monorepo `main` at the time it is created; the script warns when that differs
from the commit in `config.json`. Pass `--cache-dir` to run from another cache
and `--sync` to move the cache to current `main`. `data/` and `runs/` are
ignored by Git; only `results/` and the small provenance files are committed.

A full run takes about 20 minutes on one core: a few minutes of Rust builds,
then the 30 s budgets. Solver time is wall-clock per instance, so results
depend on the machine.
