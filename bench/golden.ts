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

function parseNumber(raw: string, field: string): number {
  const cleaned = raw.replaceAll("_", "");
  const num = cleaned.includes("/")
    ? cleaned.split("/").map((part) => Number(part.trim())).reduce((a, b) => a / b)
    : Number(cleaned);
  if (!Number.isFinite(num)) {
    throw new Error(`invalid numeric value for ${field}: ${raw}`);
  }
  return num;
}

function parseValue(k: string, v: string): string | number {
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    return v.slice(1, -1);
  }
  return parseNumber(v, k);
}

function table(name: string): Config[] {
  const start = luau.indexOf(`local ${name}`);
  if (start === -1) throw new Error(`missing table ${name} in dynamic.luau`);
  const openBrace = luau.indexOf("=\n", start) !== -1 ? luau.indexOf("{", luau.indexOf("=", start)) : luau.indexOf("{", start);
  const block = luau.slice(openBrace, luau.indexOf("\n}", openBrace));
  const configs: Config[] = [];
  for (const rawLine of block.split("\n")) {
    const line = rawLine.replace(/--.*$/, "").trim();
    const m = line.match(/^\{(.*)\},?$/);
    if (!m) continue;
    const entries = m[1]
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((kv) => {
        const eq = kv.indexOf("=");
        if (eq === -1) throw new Error(`malformed entry in ${name}: ${kv}`);
        const k = kv.slice(0, eq).trim();
        const v = kv.slice(eq + 1).trim();
        return [k, parseValue(k, v)] as const;
      });
    configs.push(Object.fromEntries(entries) as Config);
  }
  if (configs.length === 0) throw new Error(`no configs parsed from dynamic.luau's ${name}`);
  return configs;
}

report(table("tests"));
report(table("configs"));
