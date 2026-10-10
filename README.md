<a href="#acknowledgements"><img src="https://raw.githubusercontent.com/kohltastrophe/luau-reactivity-benchmark/assets/chart.svg" width="100%"/></a>

A benchmark of fine-grained reactivity libraries for Luau, a port of [milomg/js-reactivity-benchmark](https://github.com/milomg/js-reactivity-benchmark). Every library with an adapter in `adapters/` runs the same workloads, and the results render to one chart. Everything runs headless under the `luau` CLI (no Roblox, no Studio), so the same numbers come out in CI and on any machine with `luau` and `wally` on its `PATH`.

## Running it

```sh
python3 benchmark.py                        # -> bench/results.json, bench/memory.json, chart.svg, chart-small.svg
python3 benchmark.py --passes 3             # keep each row's fastest across 3 passes
python3 benchmark.py --flag=--codegen       # native codegen instead of the interpreter
python3 benchmark.py --verify               # correctness gate only
python3 benchmark.py --filter signals vide  # only these adapters
python3 chart.py                            # re-render an existing results.json
```

1. **Assemble** ([`assemble.py`](assemble.py), whenever `wally.toml` or it changes): `wally install`, then each package is made requirable headless in place. Its Roblox instance-requires are rewritten to string requires through its rojo tree, and a generated `.luaurc` aliases it as `@<name>`.
2. **Measure** every `(adapter, bench)` in its own `luau` process through [`bench/main.luau`](bench/main.luau), so there is no heap or GC carryover. Timed rows run one at a time.
3. **Count** each row's node evaluations and each adapter's memory, in separate untimed processes that run in parallel.
4. **Report**: write the results, print them with a ranked geomean, and render the chart with [`chart.py`](chart.py).

## What's measured

Each adapter exposes its library through upstream's `ReactiveFramework` contract ([`adapters/types.luau`](adapters/types.luau)). Every framework runs the same graph shapes, iteration counts and RNG (upstream's `sfc32`/`xmur3a`, ported bit-for-bit), so the times line up directly.

| Suite       | Exercises                                                                                                     |
| ----------- | ------------------------------------------------------------------------------------------------------------- |
| **kairo**   | 9 propagation patterns: avoidable, broad, deep, diamond, mux, repeated, triangle, unstable, mol (heavy nodes) |
| **cellx**   | the [cellx](https://github.com/Riim/cellx) interlinked-layer lattice, at 1000 and 2500 layers                  |
| **dynamic** | rectangular graphs mixing static and runtime-varying dependencies                                             |
| **sbench**  | create / update throughput, each summed over fan-in/out patterns (1→1 … 1000→1, 1→2 … 1→1000)                  |
| **gc**      | teardown, collection and live-graph tracing time, plus memory footprint and retention                         |

Each suite is one file in [`bench/benches/`](bench/benches) that owns its timed cases and its correctness checks.

### Teardown, GC, and memory

Upstream says it intends to track GC overhead but never does: V8 makes that awkward, so it only force-collects _between_ tests. Luau's `collectgarbage` offers a full `"collect"` and a `"count"` of live KB, which makes a graph's lifecycle measurable. Over a flat forest of 20k signal→computed→effect units:

- **gc/teardown** times `cleanup()`. An owner-tree teardown unlinks eagerly and pays here.
- **gc/collect** times one full collection reclaiming the disposed graph. A teardown that only drops handles defers its cost to this row.
- **gc/live** times one full collection while the graph is alive: the tracing cost every GC cycle pays for as long as the graph exists. Designs that track observers through weak tables pay here.
- **memory** (chart section and `bench/memory.json`):
  - **node footprint**: bytes per node, relative to the leanest framework.
  - **build retained**: KB still held after the first build and `cleanup()`, including one-time module caches.
  - **steady retained**: mean KB retained per further build/teardown cycle; ~0 means no per-build leak.

  None of the memory figures count toward the geomean.

Frameworks divide the cost differently: a core can be cheap to tear down but expensive to collect, or the reverse. These rows are split to show that.

### Correctness

`--verify` runs one capped process per `(adapter, check)`. A wrong value fails the gate; a check the framework can't finish (overflow, deadline) is reported as a DNF.

- **kairo**: upstream's value assertions, run over two passes to catch state carried between iterations. Upstream's exact effect counts are checked as floors, so a framework that isn't glitch-free passes but an adapter whose effects never re-run fails.
- **sbench**: every pattern, at a small size, must run its effects at least as often as a framework that skips equal writes would (upstream checks nothing here).
- **cellx**: upstream's golden leaf values.
- **dynamic**: upstream's unit-test graphs and every timed config must produce their golden leaf sums. The sums come from running upstream's own `makeGraph`/`runGraph` at this port's iteration counts ([`bench/golden.ts`](bench/golden.ts) regenerates them).

The timed runs check their own values too, as upstream's do. A run that produces a wrong value is charted as `wrong value`, not timed.

## Methodology

- **One harness for everyone** ([`bench/harness.luau`](bench/harness.luau)). Every bench runs upstream's builds, warm-ups and timed repeats, each repeat starting from a collected heap. The reported time is the fastest repeat; cellx reports the mean (upstream sums its runs, which ranks the same). Repeats stop early once their timed seconds pass a budget or their wall time passes a limit, so a slow core that still finishes is measured from fewer repeats. Every bench runs under `luau -O2`, the optimisation level Roblox ships.
- **Each library runs in its production configuration.** Adapters use the library's own idioms: eager or deferred effects, batching or none, strict mode off where that's how it ships. Where the off-Roblox fallback differs from what ships, the adapter restores the shipped behaviour:
  - signals' fast flags are pinned to their Roblox defaults.
  - Fusion and Flux raise effect errors rather than printing them.
  - Fusion's infinite-loop timer is relaxed, so a lattice overflow is reported as an overflow.
  - Fusion's `use()` scans its scope on every read, and each bench puts its whole graph in one scope. That cost is Fusion's own, not the adapter's.
- **Instrumentation stays out of timed code.** Node evaluations (shown on each bar) and the effect floors come from separate untimed runs through a counting bridge (`harness.count`).
- **Recompute counts are reported, not asserted.** Frameworks that aren't glitch-free, or have no batching, re-run more. That is a real difference, not a failure, so the extra work is shown next to the time it cost.
- **DNF** means a run exceeded the shared per-run deadline, overflowed, or produced a wrong value; the bar shows which. Every DNF is charged exactly the deadline in the geomean, in the row's own units, so a core that blows up is penalised the same on every row. Only an `unexpected error` is left out, since it points at the harness.
- **Treat gaps under about 10% as noise.** CI pins the luau release and stamps it on the chart.

> **Port fidelity.** Graph shapes, bodies, RNG, warm-ups, timed windows and golden values are upstream's. What differs, on purpose:
>
> - **Sizes** are scaled down for headless luau, identically for every framework. kairo times 50 iterations per repeat (upstream 500) and reports per iteration. sbench uses 20 000 / 1 500 for its create / update patterns (upstream 1e5), keeping each pattern's ratios. The dynamic iteration counts are in `dynamic.luau`.
> - **cellx** reports the mean per run and warms up at each size. **sbench** cleans up after each warm-up build, and each row shares one repeat budget across its patterns.
> - **Limits**: a per-run deadline and a repeat budget, so a core that blows up becomes a DNF instead of stalling the run. Upstream runs every framework in one process with no limits.
> - **Bodies** written with `map`/`reduce` upstream are plain loops here: the same reads in the same order.
> - **Checks** fail the gate on a wrong value; upstream only logs.
> - **The `gc` suite** is new.
>
> So the numbers compare these frameworks against each other, not this port against upstream's published figures.

### Why not Iris or React Luau?

Like upstream, this covers only fine-grained reactivity. [Iris](https://github.com/SirMallard/Iris) is immediate-mode, with no persistent graph to drive. [react-luau](https://github.com/Roblox/react-luau) is virtual-DOM reconciliation, where a state change re-renders a whole component. Each would need its own "render N, update M" benchmark.

## Adding a framework

Create `adapters/<name>.luau` (or `adapters/<name>/init.luau`) returning the contract in [`adapters/types.luau`](adapters/types.luau). Its file name is its chart label, and there's nothing to register. The core can come from anywhere `require` reaches:

- **A wally package**: declare it in [`wally.toml`](wally.toml) under the adapter's name and `require("@<name>")`. A Roblox-only root module can be bypassed with `@<name>/sub/module`, and a rojo monorepo's sibling mounts are `@<name>.<Child>`. The chart labels the bar with the package's scope, resolved version and repository.
- **A local source**: `require` it by path.

## Acknowledgements

A Luau port of [milomg/js-reactivity-benchmark](https://github.com/milomg/js-reactivity-benchmark): the `ReactiveFramework` contract, all four upstream suites (kairo, cellx, dynamic, sbench), and their methodology come from there. That project in turn draws on the [cellx](https://github.com/Riim/cellx) and [SolidJS](https://github.com/solidjs/solid) benchmarks.

The libraries measured: [Charm](https://github.com/littensy/charm), [Flux](https://github.com/kohltastrophe/flux), [Fusion](https://github.com/dphfox/Fusion), [signals](https://github.com/Roblox/signals), [Vide](https://github.com/centau/vide).
