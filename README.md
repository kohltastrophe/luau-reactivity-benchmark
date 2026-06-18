<a href="#acknowledgements"><img src="https://raw.githubusercontent.com/kohltastrophe/luau-reactivity-benchmark/assets/chart.svg" width="100%"/></a>

A benchmark of fine-grained reactivity libraries for Luau, run on identical workloads and rendered to one chart. It's a port of [milomg/js-reactivity-benchmark](https://github.com/milomg/js-reactivity-benchmark). Every library with an adapter is included automatically, the set is discovered from `adapters/`, so nothing outside an adapter (and `wally.toml`) decides what's compared.

Everything runs headless under the `luau` CLI, no Roblox, no Studio, so the same numbers come out in CI and on any machine with `luau` on its `PATH`. Add an adapter and the hard graphs double as a diagnostic, showing where a core scales cleanly and where it breaks down.

## Running it

The whole pipeline is one command, [`benchmark.py`](benchmark.py):

```sh
python3 benchmark.py                      # 1 pass -> bench/results.json + bench/memory.json + chart.svg
python3 benchmark.py --passes 2           # take the per-(framework, test) min across 2 passes
python3 benchmark.py --flag=--codegen     # measure native codegen instead of the interpreter
python3 benchmark.py --verify             # correctness gate only (no timing, no chart)
python3 benchmark.py --filter signals vide  # restrict the run to the named frameworks
```

It runs in order:

1. **Assemble** (first run, or whenever `lib/` is missing): `wally install`, then each adapter's `setup(ctx)` assembles its core into `lib/<name>/`. Delete `lib/` to re-assemble. Everything under `lib/` is generated and git-ignored.
2. **Measure** every `(adapter, bench)` in its own `luau` process, so there's no heap/GC carryover between them, timing each fastest-of-N.
3. **Combine** passes into [`bench/results.json`](bench/results.json) by the per-`(framework, test)` min, write the per-framework memory table to [`bench/memory.json`](bench/memory.json), and print both with a ranked geomean.
4. **Render**: [`chart.py`](chart.py) writes `chart.svg` (it also runs standalone against an existing `results.json`).

One pass matches upstream's fastest-of-N; `--passes K` takes the min across K passes to filter more noise.

## What's measured

A common adapter interface ([`adapters/types.luau`](adapters/types.luau)) exposes each library through the same `ReactiveFramework` contract as the reference (`signal`, `computed`, `effect`, `withBatch`, `withBuild`, `cleanup`). Every framework runs the same workload, same graph shape, iteration counts, and seeded RNG, so the times line up directly.

| Suite       | Source                       | Exercises                                                                                                       |
| ----------- | ---------------------------- | --------------------------------------------------------------------------------------------------------------- |
| **kairo**   | `bench/benches/kairo.luau`   | 9 propagation patterns: avoidable, broad, deep, diamond, mux, repeated, triangle, unstable, mol (heavy nodes)   |
| **cellx**   | `bench/benches/cellx.luau`   | the classic [cellx](https://github.com/Riim/cellx) interlinked-layer lattice (1000 and 2500 layers)             |
| **dynamic** | `bench/benches/dynamic.luau` | rectangular graphs mixing static and runtime-varying (dynamic) dependencies                                     |
| **sbench**  | `bench/benches/sbench.luau`  | create / update throughput, each summed over fan-in/out patterns (1→1 … 1000→1, 1→2 … 1→1000), as upstream does |
| **gc**      | `bench/benches/gc.luau`      | teardown: `cleanup()` cost, and collect: full-GC reclaim time, on a 60k-node graph (see below)                  |

### Teardown, GC, and memory

The `gc` suite realises an intent upstream states but never finished ("tracks garbage collection overhead per test ... we're also working on enabling efficient tracking of GC time"): in V8 that's awkward, so the JS benchmark only force-collects _between_ tests to keep GC out of its numbers, never reporting it. Luau's `collectgarbage` exposes a full `"collect"` and an exact `"count"`, which makes a graph's lifecycle directly measurable. Over a fixed flat forest (20k signal→computed→effect units, 60k nodes), built in its own process:

- **gc/teardown**: wall-clock of `cleanup()` disposing the graph. An owner-tree teardown unlinks eagerly and pays here; a teardown that only drops effect handles is cheap here and defers the cost to the collector.
- **gc/collect**: wall-clock of one full collection reclaiming the disposed graph, the cost a lazy teardown defers, roughly proportional to how much pointer-dense garbage the framework leaves.
- **memory & retention** (a `memory` section on the chart, plus `bench/memory.json` and a console table). `count` gives a deterministic footprint (bytes/node) and a retained-after-`cleanup()` figure: KB still live once the graph is disposed and collected, where ~0 is ideal and a positive number is memory the framework leaks or over-retains. These rows normalise to the leanest framework (lower = less memory) but stay out of the timed geomean.

These are split because frameworks divide the cost differently: a core can be cheap to tear down but expensive to collect, or the reverse. The split is the point.

### Correctness

`python3 benchmark.py --verify` runs a timing-independent gate: kairo asserts every computed value, cellx checks the canonical golden results at 1000 layers, and dynamic checks the all-static golden sum plus cross-framework agreement on every configured graph. Like the timed pass, each adapter runs in its own capped process, so a framework that can't finish a lattice/wide graph is recorded as a DNF instead of hanging CI.

### Why not Iris or React Luau?

Like the [upstream reference](https://github.com/milomg/js-reactivity-benchmark), this only covers fine-grained reactivity (the JS version compares Solid, Preact Signals, Reactively, and Vue reactivity, **not** React). Two libraries people expect fall outside that scope:

- **[Iris](https://github.com/SirMallard/Iris)** is immediate-mode (a Dear ImGui port). You re-declare the UI every frame and Iris owns the instances; there's no persistent signal/computed/effect graph to drive.
- **[react-luau](https://github.com/Roblox/react-luau)** is virtual-DOM reconciliation. Its primitives are component-scoped and a state change re-renders the whole component, so there's no natural way to express the suite's 1000-signal or 50-deep graphs, forcing it would just measure reconciler overhead.

Either would need its own "render N, update M" benchmark to compare fairly.

## Methodology & fairness

- **Same harness for everyone.** Warm up, then take the fastest of N timed repeats (`bench/suite.luau`), collecting garbage between runs, the way the reference does. It all runs under `luau -O2`, since that's what Roblox executes in production.
- **Each library uses its own idioms and production config.** Eager or deferred effects, batching or none, strict mode on or off; each adapter wires its framework up the way it actually ships, rather than forcing one model.
- **Timing isolates the work being measured.** Graph construction is left out of the propagation timings, and sbench's create/update numbers leave teardown out; the `gc` suite then measures teardown and collection on their own, so disposal cost is reported rather than hidden in (or absent from) throughput.
- **Recompute counts aren't asserted.** Some patterns (triangle, unstable) re-run more often on frameworks that aren't glitch-free. That's a real difference, not a failure, so the verifier checks computed values, not re-run counts.
- **DNF means a framework couldn't finish a benchmark**, and the bar says why: `stack overflow`, `table overflow`, `timeout`, `unexpected error`, or an adapter's own reason. Some invalidation algorithms (e.g. ones that don't deduplicate nodes during traversal) grow super-linearly on lattice graphs and can't finish at benchmark sizes, a property of the algorithm, which is what these benches expose. Any failure to complete is charged the time cap in the geomean (timeout and stack/table overflow alike), so a core that blows up is penalised, not excused from those rows. Only an `unexpected error` is left out, since it points at the harness; per-row bars still normalise to the fastest framework that _did_ finish.

> **Port fidelity.** The benches mirror upstream's graph shapes, iteration structure, and golden values; sizes are scaled down for headless luau but stay identical across frameworks. A few details differ on purpose: the dynamic graph uses its own seeded RNG, and the `gc` suite is new; so the numbers compare the frameworks here against each other, not this port against upstream's published figures.

## Adding a framework

There's no list to edit; each framework is one folder under `adapters/`:

```
adapters/
  <name>/
    init.luau     -- the ReactiveFramework adapter, required by folder name (also its chart label)
    setup.py      -- optional: assembles this framework's core (see below)
  types.luau      -- the shared interface (a file, skipped by discovery)
```

Create `adapters/<name>/init.luau` returning a `ReactiveFramework` (contract in [`adapters/types.luau`](adapters/types.luau)); the folder name is its chart label. `benchmark.py` discovers folders directly (for both timing and `--verify`), so there's nothing to register. To skip benches a core can't finish, map their keys to short reasons in `M.DNF` (e.g. `["cellx/cellx1000"] = "timeout"`); they show on the chart but never run to the cap. Each adapter is loaded defensively, one whose core isn't available is skipped rather than breaking the run.

A core can come from anywhere `require` reaches:

- **A wally package.** Declare it in [`wally.toml`](wally.toml) and add `adapters/<name>/setup.py` with a `setup(ctx)` that pulls the core into `lib/<name>/`, rewriting Roblox `require(script.X)` / `require(Packages.X)` calls into headless string requires (via the `ctx` helpers `benchmark.py` provides). The adapter then requires the assembled modules from `../lib/<name>/`.
- **A local source.** `require` it from `init.luau`, with no `setup.py`.

## Acknowledgements

A Luau port of [milomg/js-reactivity-benchmark](https://github.com/milomg/js-reactivity-benchmark): the shared `ReactiveFramework` contract, all four upstream suites (kairo, cellx, dynamic, sbench), and the fastest-of-N methodology come from there. That project in turn draws on the [cellx](https://github.com/Riim/cellx) and [SolidJS](https://github.com/solidjs/solid) benchmarks.

The libraries measured: [Charm](https://github.com/littensy/charm), [Fusion](https://github.com/dphfox/Fusion), [signals](https://github.com/Roblox/signals), [Vide](https://github.com/centau/vide).
