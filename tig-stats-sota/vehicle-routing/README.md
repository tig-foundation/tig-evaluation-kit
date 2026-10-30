# Vehicle routing: TIG algorithms vs best-known solutions on Homberger-Gehring sets

This folder is a self-contained experiment that benchmarks official TIG
vehicle-routing (`c002`, VRPTW) algorithms on four sets of Homberger-Gehring
instances and reports how far each final solution is from the published
best-known solution (BKS). Every set lives in its own subfolder with its
instance list (`config.json`), best-known values (`bks.json`), results and a
README; the algorithms, budget and monorepo commit are shared in
`experiment.json`, and `run_experiment.py` and `analyze.py` serve all sets.

## Instance sets

| Set | Folder | Instances | BKS vehicles | What makes it different |
| --- | --- | --- | --- | --- |
| 1000-customer RC2 | [`hg1000-rc2`](hg1000-rc2/README.md) | `RC2_10_1` to `RC2_10_10` | 18 to 20 | Mixed clustered and random customers, wide time windows, capacity 1000: 50 or more customers per route |
| 1000-customer R2 | [`hg1000-r2`](hg1000-r2/README.md) | `R2_10_1` to `R2_10_10` | 19 | Random customers, wide time windows, capacity 1000: about 53 customers per route |
| 1000-customer R1 | [`hg1000-r1`](hg1000-r1/README.md) | `R1_10_1` to `R1_10_10` | 91 to 100 | Random customers, tight time windows, capacity 200: about 10 customers per route, so the fleet is fixed by the constraints |
| 600-customer RC2 | [`hg600-rc2`](hg600-rc2/README.md) | `RC2_6_1` to `RC2_6_10` | 11 to 14 | The RC2 class at the smallest size in TIG's active vehicle-routing tracks |

RC2 and R2 are the two hardest classes in a sweep of all sixty 1000-customer
instances with these algorithms: a long scheduling horizon, wide time windows
and very long routes. R1 is the opposite regime, where tight windows and a
small capacity pin the fleet near the best-known value. The 600-customer RC2
set shows how the gap scales from the bottom of TIG's active track range (600
to 1000 nodes) to the top. The clustered classes C1 and C2 were left out
because the algorithms were already close to the best-known values there.

## Setup

| Item | Value |
| --- | --- |
| Import | `tig.py import --format solomon-vrptw`, one dataset per set |
| Algorithms | `hgs_explore5` = `c002_a089` (`hgs_v1`) with `{"exploration_level": 5}`; `hgs_advance` = `c002_a110` as shipped |
| Budget | 30 s solver time per instance (`--timeout 30`), one worker, one repetition, algorithm seed `default_seed` |
| Monorepo | `d33946ddeaf89240254b0661aba70859f7868fe0`, tig-monorepo `main` on 2026-10-29, the commit recorded in `experiment.json` |
| BKS | each set's `bks.json`, copied from the SINTEF best-known tables for 1000 and 600 customers on 2026-10-29 and 2026-10-30 |

`hgs_v1` is run at exploration level 5 rather than as shipped because its
default preset is level 0, a single local search that finishes in about 0.3 s
and therefore ignores the 30 s budget. Level 5 is the author's deepest preset.

## Metric

For every instance the final saved solution is decoded, its routes are
re-measured with real-valued Euclidean distances from the instance
coordinates, and the gap is

```
gap % = (solution distance - BKS distance) / BKS distance * 100
```

Lower is better. Each set's chart shows one dot per instance, a diamond at
the mean over the ten instances and a whisker of plus or minus one sample
standard deviation. The mean number of vehicles in the final solutions is
noted under each configuration, next to the BKS mean in the subtitle, so it
is visible at a glance when a negative gap is reached with a larger fleet.

Two caveats apply when reading the numbers against the literature:

- TIG rounds distances to integers and uses them as travel times, so a route
  TIG accepts can arrive slightly after a due time once travel times are
  real-valued. The analysis re-checks every route against the real-valued
  windows; `†` in the tables marks a solution with at least one late arrival,
  and `late_arrivals` and `max_lateness` in `per_instance.csv` give the count
  and the largest lateness in time units. The lateness is a rounding residue,
  typically under two time units on horizons of thousands, but such a
  solution is not strictly feasible by the literature's definition.
- TIG minimises distance only, subject to the fleet cap in the file. The BKS
  minimises vehicles first. On the wide-window sets, negative gaps are
  obtained with more vehicles than the BKS uses; the tables list both.

## Results

Mean gap to the BKS distance (± one sample standard deviation over the ten
instances) and mean fleet, per set. Each set's README has the chart and the
per-instance table.

| Set | Instances | BKS vehicles | hgs_explore5 gap | hgs_explore5 vehicles | hgs_advance gap | hgs_advance vehicles |
|---|---|---|---|---|---|---|
| [Homberger-Gehring 1000-customer RC2](hg1000-rc2/README.md) | 10 | 18.2 | +5.69% ± 4.23 | 25.7 | -0.57% ± 3.23 | 23.0 |
| [Homberger-Gehring 1000-customer R2](hg1000-r2/README.md) | 10 | 19.0 | +5.56% ± 5.85 | 27.9 | -0.38% ± 4.14 | 24.6 |
| [Homberger-Gehring 1000-customer R1](hg1000-r1/README.md) | 10 | 91.9 | +6.34% ± 0.88 | 94.4 | +1.10% ± 0.53 | 92.4 |
| [Homberger-Gehring 600-customer RC2](hg600-rc2/README.md) | 10 | 11.4 | -1.45% ± 5.04 | 17.8 | -5.00% ± 4.91 | 15.9 |

Across the four sets:

- `hgs_advance` is 5 to 7 percentage points ahead of `hgs_explore5` on every
  set.
- On the wide-window sets (RC2 at both sizes and R2) the negative gaps are
  bought with 4 to 9 extra vehicles. At 600 customers `hgs_advance` stops on
  its own criteria before the budget on every instance and is 5% below the
  BKS distance on average; at 1000 customers it is level with the BKS.
- On R1, where the fleet is fixed by the constraints, the gap is a plain
  distance gap: `hgs_advance` is about 1% above the BKS with one to three
  extra vehicles, with the smallest spread of the four sets.
- Arrivals after a due time under real-valued travel times occur in most
  solutions on every set, by at most two time units. On R1 the one
  `hgs_advance` solution below the BKS distance is infeasible for that
  reason, so it is not a new best-known solution.

`*` in the per-set tables marks runs stopped by the 30 s timeout, where the
last solution saved before the stop is the one measured.

## Reproducing

From the repository root, with Rust and `cargo` installed:

```bash
python3 -m pip install -r tig-stats-sota/vehicle-routing/requirements.txt   # matplotlib, chart only
python3 tig-stats-sota/vehicle-routing/run_experiment.py                    # every set
python3 tig-stats-sota/vehicle-routing/run_experiment.py hg1000-r1          # one set
```

`run_experiment.py` initialises a kit cache under `data/cache` (a mirror of
the monorepo plus the Rust builds, under a gigabyte), downloads each set's
archive into `data/`, extracts and imports its instances into
`<set>/data/`, runs each configuration from `experiment.json` into
`<set>/runs/<label>/`, then calls `analyze.py`, which writes `<set>/results/`
and prints the per-set and cross-set tables. Steps whose output already
exists are skipped; use `--force-run` to solve again, `--only <label>` to run
one configuration, and `python3 analyze.py [SET ...]` on its own to rebuild
the tables and charts.

The experiment uses its own cache rather than the kit's default one so that
the monorepo commit it runs at is recorded here, independent of whatever the
default cache is pinned to, and so that `tig.py source sync` never has to
touch the default cache, which would also move every dataset generated
afterwards. The cache is pinned to monorepo `main` at the time it is created;
the script warns when that differs from the commit in `experiment.json`. Pass
`--cache-dir` to run from another cache and `--sync` to move the cache to
current `main`. `data/`, `<set>/data/` and `<set>/runs/` are ignored by Git;
only `<set>/results/` and the small provenance files are committed.

A set takes about 10 minutes on one core at the 30 s budget, plus a few
minutes of Rust builds the first time. Solver time is wall-clock per
instance, so results depend on the machine.
