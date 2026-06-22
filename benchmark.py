#!/usr/bin/env python3
"""Single entry point for the Luau reactivity benchmark.

    python3 benchmark.py                      # assemble (if needed), time every (framework, bench), render chart.svg
    python3 benchmark.py --passes 2           # take the per-(framework, test) min across 2 passes
    python3 benchmark.py --flag=--codegen     # measure native --codegen instead of the interpreter
    python3 benchmark.py --verify             # run the correctness gate only (no timing, no chart)
    python3 benchmark.py --filter charm vide  # restrict timing/verification to these framework names

Each (adapter, bench) is measured in its OWN luau process (heap/GC isolation), and within that
process the bench is timed fastest-of-N, the milomg/js-reactivity-benchmark methodology. Cores are
assembled on first run (wally install + each adapter's setup(ctx)); delete lib/ to force a re-assemble.
"""

import argparse
import glob
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(HERE, "bench")
ADAPTERS = os.path.join(HERE, "adapters")
LIB = os.path.join(HERE, "lib")
INDEX = os.path.join(HERE, "Packages", "_Index")
RESULTS = os.path.join(BENCH, "results.json")
SCRATCH = os.path.join(
    BENCH, "_scratch.luau"
)  # per-process snippet, rewritten for every run
CAP = float(
    os.environ.get("BENCH_CAP", 60)
)  # per-process wall-clock cap; a hang past it is a DNF

EXCLUDED_FROM_MEAN = {
    "error",
    "dnf",
}  # harness faults, left out of a framework's geomean


# ---- adapter discovery ---------------------------------------------------------


def discover(filter_names=None):
    """Adapter folder names (each with an init.luau), optionally narrowed by --filter."""
    names = [
        e
        for e in sorted(os.listdir(ADAPTERS))
        if os.path.isfile(os.path.join(ADAPTERS, e, "init.luau"))
    ]
    if filter_names:
        want = {f.lower() for f in filter_names}
        kept = [n for n in names if n.lower() in want]
        missing = want - {n.lower() for n in kept}
        if missing:
            sys.exit(
                f"error: --filter matched no adapter for: {', '.join(sorted(missing))} "
                f"(available: {', '.join(names)})"
            )
        names = kept
    return names


def setup_path(name):
    p = os.path.join(ADAPTERS, name, "setup.py")
    return p if os.path.isfile(p) else None


def cores_present():
    """True once every adapter that assembles a core has a populated lib/<name>/."""
    for name in discover():
        if setup_path(name):
            d = os.path.join(LIB, name)
            if not (os.path.isdir(d) and os.listdir(d)):
                return False
    return True


# ---- require rewriting: Roblox instance requires -> headless string requires ---


ALIAS_RE = re.compile(r"^(\s*)local\s+(\w+)\s*=\s*(script(?:\.\w+)+|script)\s*$")
REQUIRE_RE = re.compile(r"require\(\s*([A-Za-z_]\w*(?:\.\w+)*)\s*\)")


def _resolve(file_rel_dir, is_init, tokens):
    cur = list(file_rel_dir)
    at_file = not is_init
    for tok in tokens:
        if tok == "Parent":
            if at_file:
                at_file = False
            else:
                if not cur:
                    raise ValueError("navigated above tree root")
                cur = cur[:-1]
        else:
            at_file = False
            cur = cur + [tok]
    return cur


def _to_relreq(file_rel_dir, target_parts):
    rel = os.path.relpath(
        "/".join(target_parts), "/".join(file_rel_dir) if file_rel_dir else "."
    )
    rel = rel.replace(os.sep, "/")
    if not rel.startswith("."):
        rel = "./" + rel
    return rel


def _convert_file(root, path):
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    is_init = parts[-1] in ("init.luau", "init.lua")
    file_rel_dir = parts[:-1]

    with open(path) as f:
        lines = f.readlines()

    aliases = {}
    for ln in lines:
        m = ALIAS_RE.match(ln.rstrip("\n"))
        if m:
            aliases[m.group(2)] = m.group(3).split(".")[1:]

    converted = [0]

    def replace_require(m):
        segs = m.group(1).split(".")
        base = segs[0]
        if base == "script":
            toks = segs[1:]
        elif base in aliases:
            toks = aliases[base] + segs[1:]
        else:
            return m.group(0)
        target = _resolve(file_rel_dir, is_init, toks)
        converted[0] += 1
        return f'require("{_to_relreq(file_rel_dir, target)}")'

    out = []
    for ln in lines:
        m = ALIAS_RE.match(ln.rstrip("\n"))
        if m:
            out.append(
                f"{m.group(1)}local {m.group(2)} = nil -- [headless] was: {m.group(3)}\n"
            )
            continue
        out.append(REQUIRE_RE.sub(replace_require, ln))

    if out != lines:
        with open(path, "w") as f:
            f.writelines(out)
    return converted[0]


def convert_tree(root):
    """Rewrite every Roblox instance-require under root in place. (ctx.convert_tree)"""
    files = reqs = 0
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if not fn.endswith((".luau", ".lua")):
                continue
            path = os.path.join(dirpath, fn)
            try:
                n = _convert_file(root, path)
            except ValueError as e:
                print(f"  skip {os.path.relpath(path, root)}: {e}")
                continue
            if n:
                files += 1
                reqs += n
    return files, reqs


# ---- core assembly (wally install + each adapter's setup(ctx)) ------------------


def pkg_dir(prefix):
    matches = sorted(glob.glob(os.path.join(INDEX, prefix + "*")))
    if not matches:
        sys.exit(
            f"error: no downloaded package matching '{prefix}*' (did wally install run?)"
        )
    return matches[-1]


def fresh(dest):
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)


def copy_tree(src, dest, skip=()):
    for dirpath, dirnames, filenames in os.walk(src):
        relroot = os.path.relpath(dirpath, src)
        top = relroot.split(os.sep)[0] if relroot != "." else ""
        if top in skip:
            dirnames[:] = []
            continue
        for fn in filenames:
            if relroot == "." and fn in skip:
                continue
            if fn.endswith((".luau", ".lua")) and "__tests__" not in dirpath:
                outdir = dest if relroot == "." else os.path.join(dest, relroot)
                os.makedirs(outdir, exist_ok=True)
                shutil.copy2(os.path.join(dirpath, fn), os.path.join(outdir, fn))


def assemble():
    """wally install, then run each adapter's setup(ctx) to populate lib/<name>/."""
    print("# wally install")
    subprocess.run(["wally", "install"], cwd=HERE, check=True)

    ctx = types.SimpleNamespace(
        ROOT=HERE,
        INDEX=INDEX,
        LIB=LIB,
        pkg_dir=pkg_dir,
        fresh=fresh,
        copy_tree=copy_tree,
        convert_tree=convert_tree,
    )
    for name in discover():
        sp = setup_path(name)
        if sp is None:
            continue  # local/sibling-source adapter: required directly, nothing to assemble
        print(f"# {name}")
        spec = importlib.util.spec_from_file_location("adapter_setup", sp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.setup(ctx)
    print(f"\nadapters: {', '.join(discover())}")


def ensure_assembled():
    if not cores_present():
        print("frameworks not assembled yet, assembling first\n", file=sys.stderr)
        assemble()


# ---- one-shot luau snippets (the per-process isolation boundary) ----------------


def luau(flag, src, timeout=None):
    """Run a luau snippet from bench/ (so its relative requires resolve), capturing stdout.

    Returns (stdout, timed_out). A timeout SIGKILLs the process, so an uninterruptible hang
    becomes a DNF rather than wedging the run.
    """
    with open(SCRATCH, "w") as f:
        f.write(src)
    try:
        p = subprocess.run(
            ["luau", *flag.split(), "_scratch.luau"],
            cwd=BENCH,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return p.stdout, False
    except subprocess.TimeoutExpired:
        return "", True


def marked(stdout, prefix):
    """First stdout line carrying prefix, with the prefix stripped (else None)."""
    for line in stdout.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


# ---- measurement (one luau process per (adapter, bench)) -----------------------


def bench_keys(flag):
    out, _ = luau(
        flag, 'for _, k in require("./suite").keys do print("__KEY__" .. k) end\n'
    )
    keys = [
        line[len("__KEY__") :]
        for line in out.splitlines()
        if line.startswith("__KEY__")
    ]
    if not keys:
        sys.exit(
            "error: could not read bench keys from bench/suite.luau (luau load failed?)"
        )
    return keys


def drive(flag, names, keys):
    """One measurement pass: a row per (available adapter, bench), each in its own process."""
    rows, total = [], len(names)
    for ai, name in enumerate(names, 1):
        path = f"../adapters/{name}"
        out, _ = luau(flag, f'require("{path}") print("__OK__")\n')
        if (
            marked(out, "__OK__") is None
        ):  # core didn't load -> not available here, skip
            sys.stderr.write(
                f"\r  [adapter {ai}/{total}] {name:<10} unavailable, skipped\n"
            )
            continue
        for i, key in enumerate(keys, 1):
            sys.stderr.write(
                f"\r  [adapter {ai}/{total}] {name:<10} bench {i:>2}/{len(keys)} "
            )
            sys.stderr.flush()
            out, timed_out = luau(
                flag,
                f'require("./suite").printRow(require("{path}"), "{name}", {i})\n',
                timeout=CAP,
            )
            raw = marked(out, "__ROW_JSON__")
            if raw is not None:
                rows.append(json.loads(raw))
            else:
                rows.append(
                    {
                        "framework": name,
                        "test": key,
                        "seconds": None,
                        "status": "timeout" if timed_out else "error",
                    }
                )
    sys.stderr.write("\r" + " " * 50 + "\r")
    return rows


def combine(passes):
    """Reduce passes to the per-(framework, test) min, write results.json, print ranked geomean."""
    best, fws, order, seen, caps = {}, [], [], set(), {}
    for rows in passes:
        for r in rows:
            if r["framework"] not in fws:
                fws.append(r["framework"])
            if r["test"] not in seen:
                seen.add(r["test"])
                order.append(r["test"])
            cap = r.get("cap")
            if cap is not None:
                caps[r["test"]] = cap
            key = (r["framework"], r["test"])
            secs, cur = r.get("seconds"), best.get(key)
            if secs is None:
                best.setdefault(key, (None, r.get("status", "dnf")))
            elif cur is None or cur[0] is None or secs < cur[0]:
                best[key] = (secs, "ok")

    if not fws:
        sys.exit("error: measurement output had no framework rows (every run failed?)")

    out, data = [], {}
    for t in order:
        suite, _, test = t.partition("/")
        for fw in fws:
            secs, status = best.get((fw, t), (None, "dnf"))
            data.setdefault(t, {})[fw] = secs
            out.append(
                {
                    "framework": fw,
                    "suite": suite,
                    "test": test,
                    "seconds": secs,
                    "status": status,
                    "cap": caps.get(t),
                }
            )
    with open(RESULTS, "w") as f:
        json.dump(out, f, indent=2)

    w = 34
    print("test".ljust(w) + "".join(fw.rjust(12) for fw in fws))
    logs = {fw: [0.0, 0] for fw in fws}
    rows_counted = 0
    for t in order:
        g = data[t]
        ref = min((v for v in g.values() if v is not None), default=None)
        if ref is None:
            continue  # no framework finished this row; nothing to normalise against
        rows_counted += 1
        line = t.ljust(w)
        for fw in fws:
            v = g.get(fw)
            if v is not None:
                line += f"{v / ref:.3f}x".rjust(12)
                logs[fw][0] += math.log(v / ref)
                logs[fw][1] += 1
            else:
                line += "DNF".rjust(12)
                if best.get((fw, t), (None, "dnf"))[1] not in EXCLUDED_FROM_MEAN:
                    charge = caps.get(t) or CAP  # bench's own time budget, else the process cap
                    logs[fw][0] += math.log(charge / ref)
                    logs[fw][1] += 1
        print(line)
    geo = "GEOMEAN".ljust(w)
    for fw in fws:
        n = logs[fw][1]
        geo += (f"{math.exp(logs[fw][0] / n):.3f}x" if n else "-").rjust(12)
    print(geo)
    print(
        f"(min across {len(passes)} pass(es); geomean charges each DNF its bench time budget "
        f"-- per-bench cap where set, else the {CAP:.0f}s process cap -- over {rows_counted} rows, "
        f"unexpected errors excluded)"
    )


# ---- memory & retention (the gc suite's KB companion to its timed rows) ---------


def memory_report(flag, names):
    """Per-adapter graph footprint and retained-after-cleanup bytes. Companion to the timed
    gc/teardown + gc/collect rows: those say how long disposal/GC take, this says how much memory a
    framework costs per node and whether cleanup() actually gives it back."""
    rows = []
    for name in names:
        out, _ = luau(
            flag,
            f'print(require("./suite").emit(require("../adapters/{name}"), "gcmem"))\n',
            timeout=CAP,
        )
        d = None
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("{"):
                try:
                    d = json.loads(s)
                except json.JSONDecodeError:
                    d = None
        if d and "liveKB" in d:
            rows.append((name, d))
    if not rows:
        return
    n = rows[0][1]["N"]
    print(f"\nmemory & retention ({n} units = {3 * n} nodes, one fresh process each):")
    w = 14
    print(
        "framework".ljust(w)
        + "live KB".rjust(12)
        + "bytes/node".rjust(12)
        + "retained KB".rjust(14)
    )
    for name, d in rows:
        print(
            name.ljust(w)
            + f"{d['liveKB']:,.0f}".rjust(12)
            + f"{d['bytesPerNode']:.0f}".rjust(12)
            + f"{d['retainedKB']:,.0f}".rjust(14)
        )
    print(
        "(retained = KB still live after cleanup()+GC vs the pre-build baseline; ~0 is ideal)"
    )
    with open(os.path.join(BENCH, "memory.json"), "w") as f:
        json.dump({name: d for name, d in rows}, f, indent=2)


# ---- correctness gate (timing-independent; one capped process per unit) ---------


def run_unit(flag, path, unit):
    out, timed_out = luau(
        flag,
        f'print(require("./suite").emit(require("{path}"), "{unit}"))\n',
        timeout=CAP,
    )
    if timed_out:
        return None  # uninterruptible hang -> DNF
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    return None  # crashed / no row -> DNF


def verify(flag, names):
    fails, dnfs = [], []

    def check(cond, msg):
        if not cond:
            fails.append(msg)
            print("  FAIL: " + msg)

    def note_dnf(msg):
        dnfs.append(msg)
        print("  DNF: " + msg)

    adapters, dyn_titles = [], None
    for name in names:
        meta = run_unit(flag, f"../adapters/{name}", "meta")
        if not meta:
            print(f"  (skip {name}: core not available)")
            continue
        adapters.append((name, f"../adapters/{name}", meta))
        dyn_titles = dyn_titles or meta["dynTitles"]
    if not adapters:
        sys.exit("error: no adapters loaded (assemble cores first?)")
    print(f"adapters: {', '.join(n for n, _, _ in adapters)}")

    print("# kairo: per-value asserts (iter(true)), all cases x all adapters")
    for name, path, _meta in adapters:
        r = run_unit(flag, path, "kairo")
        if r is None:
            note_dnf(f"kairo {name}: did not complete (timeout/crash)")
        else:
            for f in r.get("fails", []):
                check(False, f"{f} [{name}]")

    print("# cellx: golden before={-3,-6,-2,2} after={-2,-4,2,3} (1000 layers)")
    EXP_BEFORE, EXP_AFTER = [-3, -6, -2, 2], [-2, -4, 2, 3]
    for name, path, meta in adapters:
        if "cellx/cellx1000" in set(meta.get("dnfKeys", [])):
            note_dnf(f"cellx1000 {name}: declared DNF")
            continue
        r = run_unit(flag, path, "cellx1000")
        if r is None or r.get("dnf"):
            note_dnf(f"cellx1000 {name}: did not complete")
        else:
            check(r["before"] == EXP_BEFORE, f"cellx1000 {name} before={r['before']}")
            check(r["after"] == EXP_AFTER, f"cellx1000 {name} after={r['after']}")

    print("# dynamic: all-static golden sum == 16")
    for name, path, _meta in adapters:
        r = run_unit(flag, path, "dyn-golden")
        if r is None or r.get("dnf"):
            note_dnf(f"dynamic golden {name}: did not complete")
        else:
            got = r.get("sum")
            check(got == 16, f"dynamic golden {name} sum={got} (want 16)")

    print(
        "# dynamic: cross-framework sum agreement on each config (first completer = ref)"
    )
    for i in range(1, len(dyn_titles) + 1):
        title, ref = dyn_titles[i - 1], None
        key = f"dynamic/{title}"
        for name, path, meta in adapters:
            if key in set(meta.get("dnfKeys", [])):
                note_dnf(f"dynamic {title} {name}: declared DNF")
                continue
            r = run_unit(flag, path, f"dyn:{i}")
            if r is None or r.get("dnf"):
                note_dnf(f"dynamic {title} {name}: did not complete")
                continue
            s = r["sum"]
            if ref is None:
                ref = s
            check(s == ref, f"dynamic {title} {name} sum={s} != ref {ref}")

    n = len(fails)
    if dnfs:
        print(f"\n{len(dnfs)} DNF recorded (capability limits, charted as DNF, not gate failures)")
    print(f"{n} correctness check(s) failed" if n else "all correctness checks passed")
    if n:
        sys.exit(f"{n} correctness check(s) FAILED")


# ---- entry point ---------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description="Run the Luau reactivity benchmark (timing + chart) or its correctness gate."
    )
    ap.add_argument(
        "--passes",
        type=int,
        default=1,
        help="measurement passes; results are the per-(framework,test) min (default 1)",
    )
    ap.add_argument(
        "--flag",
        default="-O2",
        help="luau flag for every run (default -O2)",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="run the correctness gate only (no timing, no chart)",
    )
    ap.add_argument(
        "--filter",
        nargs="+",
        metavar="NAME",
        help="restrict timing/verification to these framework (adapter) names",
    )
    args = ap.parse_args()

    try:
        ensure_assembled()
        names = discover(args.filter)
        if not names:
            sys.exit("error: no adapters found under adapters/")

        if args.verify:
            verify(args.flag, names)
            return

        keys = bench_keys(args.flag)
        passes = []
        for p in range(1, args.passes + 1):
            print(
                f"\n== measurement pass {p}/{args.passes} ({args.flag}) ==",
                file=sys.stderr,
            )
            rows = drive(args.flag, names, keys)
            if not rows:
                sys.exit("error: measurement produced no rows (every run failed?)")
            passes.append(rows)

        print()
        combine(passes)
        memory_report(args.flag, names)
        subprocess.run(["python3", "chart.py"], cwd=HERE, check=True)
        print("\nwrote bench/results.json + bench/memory.json + chart.svg")
    finally:
        if os.path.exists(SCRATCH):
            os.remove(SCRATCH)


if __name__ == "__main__":
    main()
