// Regenerates the `expected` leaf sums in bench/benches/dynamic.luau by running upstream's own
// makeGraph/runGraph against a trivially correct pull-based framework (every computed re-evaluates once
// after any write). Run it whenever a config's shape or iteration count changes:
//
//     git clone https://github.com/milomg/js-reactivity-benchmark ../js-reactivity-benchmark
//     bun run bench/golden.ts [path/to/js-reactivity-benchmark]
//
// It checks upstream's config.ts expectations first, then every config in dynamic.luau against the
// `expected` it carries (on a MISMATCH, paste the printed value into that config).

import { resolve } from "path";

const upstream = resolve(process.argv[2] ?? "../js-reactivity-benchmark");
const { makeGraph, runGraph } = await import(
  resolve(upstream, "packages/core/src/benches/reactively/dependencyGraph.ts")
);

let version = 0;
const ref = {
  name: "reference",
  signal: (v: unknown) => ({
    read: () => v as any,
    write: (next: unknown) => {
      v = next;
      version++;
    },
  }),
  computed: (fn: () => unknown) => {
    let cached: unknown, seen = -1;
    return {
      read: () => {
        if (seen !== version) {
          seen = version;
          cached = fn();
        }
        return cached as any;
      },
    };
  },
  effect: (fn: () => void) => fn(),
  withBatch: (fn: () => void) => void fn(),
  withBuild: <T>(fn: () => T) => fn(),
  cleanup: () => {},
};

type Config = {
  width: number;
  totalLayers: number;
  staticFraction: number;
  nSources: number;
  readFraction: number;
  iterations: number;
  expected: number;
  name?: string;
};

function report(configs: Config[]) {
  for (const c of configs) {
    const { graph } = makeGraph(ref, c.readFraction, { ...c, expected: {} });
    const got = runGraph(graph, c.iterations, ref);
    const label = c.name ?? `${c.nSources}-${c.width}x${c.totalLayers} @${c.iterations}`;
    if (got !== c.expected) process.exitCode = 1;
    console.log(`${label}: ${got}${got === c.expected ? "  OK" : `  MISMATCH (want ${c.expected})`}`);
  }
}

// upstream's config.ts expectations, a self-check of the reference framework
report([
  { width: 10, totalLayers: 5, staticFraction: 1, nSources: 2, readFraction: 0.2, iterations: 600000, expected: 19199968 },
  { width: 10, totalLayers: 10, staticFraction: 3 / 4, nSources: 6, readFraction: 0.2, iterations: 15000, expected: 302310782860 },
  { width: 1000, totalLayers: 12, staticFraction: 0.95, nSources: 4, readFraction: 1, iterations: 7000, expected: 29355933696000 },
  { width: 1000, totalLayers: 5, staticFraction: 1, nSources: 25, readFraction: 1, iterations: 3000, expected: 1171484375000 },
  { width: 5, totalLayers: 500, staticFraction: 1, nSources: 3, readFraction: 1, iterations: 500, expected: 3.0239642676898464e241 },
  { width: 100, totalLayers: 15, staticFraction: 0.5, nSources: 6, readFraction: 1, iterations: 2000, expected: 15664996402790400 },
]);

// dynamic.luau's `tests` and `configs` tables, one `{ key = value, ... }` line per config
const luau = await Bun.file(resolve(import.meta.dir, "benches/dynamic.luau")).text();
const value = (v: string) =>
  v.startsWith('"') ? v.slice(1, -1) : v.includes("/") ? v.split("/").map(Number).reduce((a, b) => a / b) : Number(v);
function table(name: string): Config[] {
  const start = luau.indexOf(`local ${name} = {`);
  const block = luau.slice(start, luau.indexOf("\n}", start));
  const configs = [...block.matchAll(/^\t\{(.*)\},?$/gm)].map(
    (m) => Object.fromEntries(m[1].split(",").map((kv) => kv.split("=").map((s) => s.trim())).map(([k, v]) => [k, value(v)])) as Config,
  );
  if (configs.length === 0) throw new Error(`no configs parsed from dynamic.luau's ${name}`);
  return configs;
}
report(table("tests"));
report(table("configs"));
