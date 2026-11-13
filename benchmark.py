#!/usr/bin/env python3
"""Run the Luau reactivity benchmark suite (see README.md).

Executes each `(adapter, row)` pair in an isolated `luau` process via `bench/main.luau`. Timed rows run
serially in rotating framework order per benchmark; untimed work (`evals`, `memory`, `checks`) runs in
parallel.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
import json
import os
import shlex
import signal
import subprocess
import sys
from typing import Any, TypeVar

import assemble
import chart

BENCH = assemble.ROOT / "bench"
ADAPTERS = assemble.ROOT / "adapters"
CAP = float(os.environ.get("BENCH_CAP", 60))  # per-process backstop for a run that never returns
JOBS = os.cpu_count() or 1
VERIFY_JOBS = min(JOBS, 4)
RESOURCE_DNF = frozenset({"overflow", "table-overflow", "oom", "timeout"})

T = TypeVar("T")
R = TypeVar("R")


def _classify_stderr(stderr: str, returncode: int) -> tuple[str, str]:
    detail = next((ln.strip() for ln in stderr.splitlines() if ln.strip()), "")
    if not detail:
        if returncode < 0:
            sig = -returncode
            try:
                sig_name = signal.Signals(sig).name
            except ValueError:
                sig_name = f"SIG{sig}"
            detail = f"terminated by {sig_name}"
        else:
            detail = f"exited with code {returncode}"
    lowered = detail.lower()
    if "not enough memory" in lowered or "block too big" in lowered or "string length overflow" in lowered:
        return "oom", detail
    if "stack overflow" in lowered:
        return "overflow", detail
    if "table overflow" in lowered:
        return "table-overflow", detail
    return "error", detail


def luau(flag: str, *args: object) -> tuple[Any, tuple[str, str] | None]:
    """Run `bench/main.luau` and return `(json_value, None)` or `(None, (status, detail))`."""
    cmd = ["luau", *shlex.split(flag), "main.luau", "-a", *map(str, args)]
    try:
        proc = subprocess.run(cmd, timeout=CAP, cwd=BENCH, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return None, ("timeout", f"killed at the {CAP:g}s process cap")

    lines = proc.stdout.strip().splitlines()
    if proc.returncode == 0 and lines:
        try:
            return json.loads(lines[-1]), None
        except json.JSONDecodeError as exc:
            return None, ("error", f"invalid JSON from main.luau: {exc}")
    return None, _classify_stderr(proc.stderr, proc.returncode)


def run(flag: str, *args: object) -> dict[str, Any]:
    """Run one `bench/main.luau` row command and convert process crashes/caps into structured status dicts."""
    res, err = luau(flag, *args)
    if err is not None:
        return {"status": err[0], "detail": err[1]}
    return res


def parallel(fn: Callable[[T], R], items: Sequence[T], workers: int = JOBS) -> list[R]:
    """Map `fn` across `items` on a bounded thread pool."""
    if not items:
        return []
    with ThreadPoolExecutor(max(1, min(workers, len(items)))) as pool:
        return list(pool.map(fn, items))


def discover(flag: str, wanted: list[str] | None) -> list[str]:
    """Discover and probe adapters under `adapters/`, failing hard if any adapter fails to load."""
    names = sorted(
        entry.name.removesuffix(".luau")
        for entry in ADAPTERS.iterdir()
        if (entry / "init.luau").is_file() or (entry.name.endswith(".luau") and entry.name != "types.luau")
    )
    if wanted:
        wanted_set = {w.lower() for w in wanted}
        missing = wanted_set - {n.lower() for n in names}
        if missing:
            sys.exit(f"error: no adapter named {', '.join(sorted(missing))} (available: {', '.join(names)})")
        names = [n for n in names if n.lower() in wanted_set]

    probes = parallel(lambda n: luau(flag, "probe", n), names, workers=VERIFY_JOBS)
    failures: list[str] = []
    for name, (res, err) in zip(names, probes):
        if err is not None:
            failures.append(f"  {name}: {err[1]}")
        elif isinstance(res, dict):
            if not res.get("ok"):
                failures.append(f"  {name}: {res.get('detail') or 'failed Framework probe'}")
        elif not res:
            failures.append(f"  {name}: failed Framework probe")
    if failures:
        sys.exit("error: adapter probe failed:\n" + "\n".join(failures))
    return names


def rank_row(row: dict[str, Any]) -> tuple[int, float]:
    """Order multi-pass candidates: sticky `wrong`/`error` first, then fastest `ok`, then resource DNF."""
    if row["status"] in ("wrong", "error"):
        return (0, 0.0)
    if row.get("seconds") is not None:
        return (1, float(row["seconds"]))
    return (2, 0.0)


def pass_order(names: list[str], pass_num: int) -> list[str]:
    """Rotate framework order on each pass so thermal/frequency drift spreads evenly."""
    if not names:
        return []
    offset = (pass_num - 1) % len(names)
    return names[offset:] + names[:offset]


def measure(
    flag: str,
    names: list[str],
    benches: list[dict[str, Any]],
    passes: int,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Run every timed row serially across `passes`, interleaving frameworks per benchmark."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    total = len(names) * len(benches)
    for p in range(1, passes + 1):
        print(f"\n== measurement pass {p}/{passes} ({flag}) ==", file=sys.stderr)
        rotated = pass_order(names, p)
        done = 0
        for i, bench in enumerate(benches, 1):
            for name in rotated:
                done += 1
                sys.stderr.write(f"\r  {done}/{total} {name:<10} {bench['key']:<40}")
                sys.stderr.flush()
                row = run(flag, "bench", name, i)
                if row["status"] in ("error", "wrong"):
                    print(f"\n  {name} {bench['key']}: {row['status']}: {row.get('detail')}", file=sys.stderr)
                key = (name, bench["key"])
                if key not in best or rank_row(row) < rank_row(best[key]):
                    best[key] = row
        sys.stderr.write("\n")
    return best


def _collect_evals(
    flag: str,
    names: list[str],
    benches: list[dict[str, Any]],
    best: dict[tuple[str, str], dict[str, Any]],
) -> dict[tuple[str, str], float | None]:
    jobs = [
        (n, i, b["key"])
        for n in names
        for i, b in enumerate(benches, 1)
        if b["evals"] and best[(n, b["key"])].get("seconds") is not None
    ]
    raw = parallel(lambda job: luau(flag, "evals", job[0], job[1]), jobs, workers=VERIFY_JOBS)
    evals: dict[tuple[str, str], float | None] = {}
    for (name, _, bkey), (val, err) in zip(jobs, raw):
        if err is not None:
            print(f"  warning: evals failed for {name} {bkey}: {err[1]}", file=sys.stderr)
        evals[(name, bkey)] = val
    return evals


def _collect_memory(flag: str, names: list[str]) -> dict[str, dict[str, Any] | None]:
    raw = parallel(lambda n: luau(flag, "memory", n), names, workers=VERIFY_JOBS)
    memory: dict[str, dict[str, Any] | None] = {}
    for name, (val, err) in zip(names, raw):
        if err is not None:
            print(f"  warning: memory failed for {name}: {err[1]}", file=sys.stderr)
        memory[name] = val
    return memory


def _format_ratio(seconds: float | None, ref: float | None) -> str:
    if seconds is None or ref is None:
        return "DNF"
    if ref <= 0:
        return "1.000x" if seconds <= 0 else "-"
    return f"{seconds / ref:.3f}x"


def _print_summary(
    rows: list[dict[str, Any]],
    memory: dict[str, dict[str, Any] | None],
    passes: int,
) -> None:
    order, data = chart.group_rows(rows)
    geo, counted = chart.geomeans(order, data)
    fws = chart.ranked(geo)
    print("test".ljust(34) + "".join(fw.rjust(12) for fw in fws))
    for key in order:
        ref = chart.fastest(data[key])
        cells = (_format_ratio(data[key][fw]["seconds"], ref) for fw in fws)
        print("/".join(key).ljust(34) + "".join(c.rjust(12) for c in cells))
    print("GEOMEAN".ljust(34) + "".join((f"{geo[fw]:.3f}x" if geo[fw] else "-").rjust(12) for fw in fws))
    print(f"(min across {passes} pass(es), over the {counted} rows any framework finished)")

    print(f"\n{'memory':<14}{'live KB':>12}{'bytes/node':>12}{'build-ret KB':>14}{'steady KB':>12}")
    ordered_names = [fw for fw in fws if fw in memory] + [fw for fw in memory if fw not in fws]
    for name in ordered_names:
        mem = memory[name]
        if mem:
            print(
                f"{name:<14}{mem['liveKB']:>12,.0f}{mem['bytesPerNode']:>12.0f}"
                f"{mem['retainedKB']:>14,.0f}{mem['steadyKB']:>12,.0f}"
            )
        else:
            print(f"{name:<14}failed, left out of memory.json")


def report(
    flag: str,
    names: list[str],
    benches: list[dict[str, Any]],
    passes: int,
    write_outputs: bool = True,
) -> None:
    """Measure timed rows, collect evals and memory, print tables, and optionally write JSON/SVG outputs."""
    best = measure(flag, names, benches, passes)
    print("\ncounting node evaluations and memory...", file=sys.stderr)
    evals = _collect_evals(flag, names, benches, best)
    memory = _collect_memory(flag, names)

    rows: list[dict[str, Any]] = []
    for bench in benches:
        suite, test = bench["key"].split("/", 1)
        for name in names:
            row = best[(name, bench["key"])]
            rows.append(
                {
                    "framework": name,
                    "suite": suite,
                    "test": test,
                    "status": row["status"],
                    "seconds": row.get("seconds"),
                    "evals": evals.get((name, bench["key"])),
                    "cap": bench["cap"],
                }
            )

    clean_mem = {n: m for n, m in memory.items() if m}
    if write_outputs:
        assemble.write_json(chart.RESULTS, rows)
        assemble.write_json(chart.MEMORY, clean_mem)

    _print_summary(rows, memory, passes)

    if write_outputs:
        chart.render_from_data(rows, clean_mem, flag=flag)
    else:
        print(
            "\n(filtered run: skipped writing results.json, memory.json, and SVG charts; pass --write to overwrite)",
            file=sys.stderr,
        )


def classify_check(status: str, scale: bool) -> str:
    """Classify a `check` outcome as `'ok'`, `'dnf'` (resource limit on a scale check), or `'fail'`."""
    if status == "ok":
        return "ok"
    if scale and status in RESOURCE_DNF:
        return "dnf"
    return "fail"


def verify(flag: str, names: list[str], checks: Sequence[dict[str, Any]]) -> None:
    """Run every correctness check across `names`; unit checks must finish `ok`, scale checks allow resource DNF."""
    norm_checks = [(str(c["key"]), bool(c.get("scale", False))) for c in checks]
    jobs = [(name, i) for name in names for i in range(1, len(norm_checks) + 1)]
    results = parallel(lambda job: run(flag, "check", job[0], job[1]), jobs, workers=VERIFY_JOBS)

    fails: list[str] = []
    dnfs: list[str] = []
    for (name, i), res in zip(jobs, results):
        key, scale = norm_checks[i - 1]
        outcome = classify_check(res["status"], scale)
        if outcome == "dnf":
            dnfs.append(f"{name} {key}: {res['status']}: {res.get('detail')}")
        elif outcome == "fail":
            fails.append(f"{name} {key}: {res['status']}: {res.get('detail')}")

    for name in names:
        ok = sum(res["status"] == "ok" for (n, _), res in zip(jobs, results) if n == name)
        print(f"  {name:<10} {ok}/{len(norm_checks)} ok")
    for line in dnfs:
        print("  DNF: " + line)
    for line in fails:
        print("  FAIL: " + line)
    if fails:
        sys.exit(f"{len(fails)} correctness check(s) FAILED")
    print(f"all correctness checks passed ({len(dnfs)} scale DNF)")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--passes",
        type=int,
        default=1,
        help="measurement passes, keeping each row's fastest (default 1)",
    )
    ap.add_argument("--flag", default="-O2", help="luau flags for every run (default -O2)")
    ap.add_argument("--verify", action="store_true", help="run the correctness gate only")
    ap.add_argument("--filter", nargs="+", metavar="NAME", help="run only these adapters")
    ap.add_argument(
        "--write",
        action="store_true",
        help="write JSON and SVG files even when --filter is set",
    )
    args = ap.parse_args(argv)
    if args.passes < 1:
        ap.error("--passes must be >= 1")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    assemble.ensure()
    meta, err = luau(args.flag, "meta")
    if err is not None or not meta:
        why = err[1] if err else "empty metadata"
        sys.exit(f"error: bench/main.luau failed to load: {why}")
    names = discover(args.flag, args.filter)
    if not names:
        sys.exit("error: no adapter under adapters/ loads")
    if args.verify:
        verify(args.flag, names, meta["checks"])
    else:
        write_outputs = (not args.filter) or args.write
        report(args.flag, names, meta["benches"], args.passes, write_outputs=write_outputs)


if __name__ == "__main__":
    main()
