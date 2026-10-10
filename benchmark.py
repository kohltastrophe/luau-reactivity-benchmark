#!/usr/bin/env python3
"""Runs the Luau reactivity benchmark (see README.md). Every (adapter, row) runs in its own luau process
through bench/main.luau; timed rows run one at a time, untimed work (evals, memory, checks) in parallel."""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import assemble
import chart

BENCH = os.path.join(assemble.ROOT, "bench")
ADAPTERS = os.path.join(assemble.ROOT, "adapters")
CAP = float(os.environ.get("BENCH_CAP", 60))  # per-process backstop for a run that never returns
JOBS = os.cpu_count() or 1


def luau(flag, *args):
    """bench/main.luau's JSON result, else (None, the reason)."""
    try:
        p = subprocess.run(
            ["luau", *flag.split(), "main.luau", "-a", *map(str, args)],
            cwd=BENCH, capture_output=True, text=True, timeout=CAP,
        )
    except subprocess.TimeoutExpired:
        return None, f"killed at the {CAP:g}s process cap"
    lines = p.stdout.strip().splitlines()
    if p.returncode == 0 and lines:
        return json.loads(lines[-1]), None
    return None, next((l.strip() for l in p.stderr.splitlines() if l.strip()), "no error output")


def run(flag, *args):
    """A row-shaped result: a process that died becomes a timeout or error row."""
    r, why = luau(flag, *args)
    if r is None:
        r = {"status": "timeout" if why.startswith("killed") else "error", "detail": why}
    return r


def parallel(fn, items):
    with ThreadPoolExecutor(JOBS) as pool:
        return list(pool.map(lambda item: fn(*item), items))


def discover(flag, wanted):
    """The adapters under adapters/ (a <name>.luau file or <name>/init.luau folder) that load as frameworks."""
    names = sorted(
        e.removesuffix(".luau")
        for e in os.listdir(ADAPTERS)
        if os.path.isfile(os.path.join(ADAPTERS, e, "init.luau")) or (e.endswith(".luau") and e != "types.luau")
    )
    if wanted:
        wanted = {w.lower() for w in wanted}
        missing = wanted - {n.lower() for n in names}
        if missing:
            sys.exit(f"error: no adapter named {', '.join(sorted(missing))} (available: {', '.join(names)})")
        names = [n for n in names if n.lower() in wanted]
    loaded = []
    for name, (ok, why) in zip(names, parallel(lambda n: luau(flag, "probe", n), [(n,) for n in names])):
        if ok:
            loaded.append(name)
        else:
            print(f"  {name}: skipped, not a loadable adapter: {why or 'no withBuild'}", file=sys.stderr)
    return loaded


def measure(flag, names, benches, passes):
    """Every timed row, serially, `passes` times; returns each (framework, test)'s best row."""

    def rank(r):  # a wrong value in any pass sticks, then the fastest finish, then a charged DNF, then an error
        if r["status"] == "wrong":
            return (0, 0.0)
        if r.get("seconds") is not None:
            return (1, r["seconds"])
        return (3 if r["status"] in chart.EXCLUDED else 2, 0.0)

    best = {}
    total = len(names) * len(benches)
    for p in range(1, passes + 1):
        print(f"\n== measurement pass {p}/{passes} ({flag}) ==", file=sys.stderr)
        done = 0
        for name in names:
            for i, b in enumerate(benches, 1):
                done += 1
                sys.stderr.write(f"\r  {done}/{total} {name:<10} {b['key']:<40}")
                r = run(flag, "bench", name, i)
                if r["status"] in ("error", "wrong"):
                    print(f"\n  {name} {b['key']}: {r['status']}: {r.get('detail')}", file=sys.stderr)
                key = (name, b["key"])
                if key not in best or rank(r) < rank(best[key]):
                    best[key] = r
        sys.stderr.write("\n")
    return best


def report(flag, names, benches, passes):
    best = measure(flag, names, benches, passes)
    print("\ncounting node evaluations and memory...", file=sys.stderr)
    jobs = [(flag, "evals", n, i) for n in names for i, b in enumerate(benches, 1) if b["evals"]]
    evals = {(j[2], benches[j[3] - 1]["key"]): r for j, (r, _) in zip(jobs, parallel(luau, jobs))}
    memory = dict(zip(names, (r for r, _ in parallel(luau, [(flag, "memory", n) for n in names]))))

    rows = []
    for b in benches:
        suite, test = b["key"].split("/", 1)
        for name in names:
            r = best[(name, b["key"])]
            rows.append({
                "framework": name, "suite": suite, "test": test, "status": r["status"],
                "seconds": r.get("seconds"), "evals": evals.get((name, b["key"])), "cap": b["cap"],
            })
    with open(chart.RESULTS, "w") as f:
        json.dump(rows, f, indent=2)
    with open(chart.MEMORY, "w") as f:
        json.dump({n: m for n, m in memory.items() if m}, f, indent=2)

    order, data = chart.load()
    geo, counted = chart.geomeans(order, data)
    fws = chart.ranked(geo)
    print("test".ljust(34) + "".join(fw.rjust(12) for fw in fws))
    for key in order:
        ref = chart.fastest(data[key])
        cells = (f"{data[key][fw]['seconds'] / ref:.3f}x" if data[key][fw]["seconds"] is not None else "DNF" for fw in fws)
        print("/".join(key).ljust(34) + "".join(c.rjust(12) for c in cells))
    print("GEOMEAN".ljust(34) + "".join((f"{geo[fw]:.3f}x" if geo[fw] else "-").rjust(12) for fw in fws))
    print(f"(min across {passes} pass(es), over the {counted} rows any framework finished)")

    print(f"\n{'memory':<14}{'live KB':>12}{'bytes/node':>12}{'build-ret KB':>14}{'steady KB':>12}")
    for name, m in memory.items():
        if m:
            print(f"{name:<14}{m['liveKB']:>12,.0f}{m['bytesPerNode']:>12.0f}{m['retainedKB']:>14,.0f}{m['steadyKB']:>12,.0f}")
        else:
            print(f"{name:<14}failed, left out of memory.json")

    chart.main(flag)


def verify(flag, names, checks):
    """Every check for every adapter. A wrong value or crash fails the gate; a check the framework can't finish
    is a DNF, as the timed run charts it."""
    jobs = [(name, i) for name in names for i in range(1, len(checks) + 1)]
    results = parallel(lambda n, i: run(flag, "check", n, i), jobs)
    fails, dnfs = [], []
    for (name, i), r in zip(jobs, results):
        if r["status"] != "ok":
            line = f"{name} {checks[i - 1]}: {r['status']}: {r.get('detail')}"
            (dnfs if r["status"] in ("overflow", "table-overflow", "timeout") else fails).append(line)
    for name in names:
        ok = sum(r["status"] == "ok" for (n, _), r in zip(jobs, results) if n == name)
        print(f"  {name:<10} {ok}/{len(checks)} ok")
    for line in dnfs:
        print("  DNF: " + line)
    for line in fails:
        print("  FAIL: " + line)
    if fails:
        sys.exit(f"{len(fails)} correctness check(s) FAILED")
    print(f"all correctness checks passed ({len(dnfs)} DNF)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--passes", type=int, default=1, help="measurement passes, keeping each row's fastest (default 1)")
    ap.add_argument("--flag", default="-O2", help="luau flags for every run (default -O2)")
    ap.add_argument("--verify", action="store_true", help="run the correctness gate only")
    ap.add_argument("--filter", nargs="+", metavar="NAME", help="only these adapters")
    args = ap.parse_args()

    assemble.ensure()
    meta, why = luau(args.flag, "meta")
    if not meta:
        sys.exit(f"error: bench/main.luau failed to load: {why}")
    names = discover(args.flag, args.filter)
    if not names:
        sys.exit("error: no adapter under adapters/ loads")
    if args.verify:
        verify(args.flag, names, meta["checks"])
    else:
        report(args.flag, names, meta["benches"], args.passes)


if __name__ == "__main__":
    main()
