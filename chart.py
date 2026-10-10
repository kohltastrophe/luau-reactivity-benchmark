#!/usr/bin/env python3
"""Renders bench/results.json (+ bench/memory.json) to chart.svg, and the overall geomean alone to
chart-small.svg. benchmark.py calls main() after a run; `python3 chart.py [luau flag]` re-renders
an existing results.json."""

import html
import json
import math
import os
import sys

import assemble

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(ROOT, "bench", "results.json")
MEMORY = os.path.join(ROOT, "bench", "memory.json")
OUT = os.path.join(ROOT, "chart.svg")
OUT_SMALL = os.path.join(ROOT, "chart-small.svg")

LUAU_VERSION = os.environ.get("LUAU_VERSION")  # stamped on the chart when the runner pins one

IN_GHA = "GITHUB_ACTIONS" in os.environ
BG_STYLE = ".bg{fill:#0000}" if IN_GHA else ".bg{fill:#fff}"
BG_DARK_STYLE = "" if IN_GHA else ".bg{fill:#0d1117}"

DNF_REASON = {
    "overflow": "stack overflow",
    "table-overflow": "table overflow",
    "timeout": "timeout",
    "wrong": "wrong value",
    "error": "unexpected error",
}

# An unexpected error points at the harness, not the framework, so it's left out of a geomean; every other
# DNF is charged its row's cap.
EXCLUDED = {"error"}

SUITE_TITLES = {
    "kairo": ("kairo", "propagation patterns"),
    "cellx": ("cellx", "interlinked layer lattice"),
    "dynamic": ("dynamic graph", "static + runtime-varying dependencies"),
    "sbench": ("sbench", "create / update throughput"),
    "gc": ("teardown & GC", "dispose cost, reclaim time, and live-graph tracing time"),
}

# ---- geometry ------------------------------------------------------------------
W = 960
M_T = 0 if IN_GHA else 24  # outer top margin
M_L = 0 if IN_GHA else 24  # outer left margin
M_R = 8 if IN_GHA else 24  # outer right margin
GUTTER = 188  # row-label gutter width
X0 = M_L + GUTTER  # plot left edge (the value=0 origin)
X1 = W - M_R  # plot right edge
PLOT_W = X1 - X0

MAX_REL = 10.0  # x-axis cap (bars beyond this fade out and show their true value)
TICKS = [1, 2, 4, 6, 8, 10]

BAR_H = 14
BAR_GAP = 4
GROUP_PAD = 9  # extra space between benchmark groups
SUITE_HEAD = 30  # space for a suite header
HERO_BAR_H = 22
HERO_GAP = 7

STYLE = (
    "<style>"
    "text{font-family:'Inter','Segoe UI',system-ui,-apple-system,sans-serif;"
    "-webkit-font-smoothing:antialiased}"
    ".mono{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace}"
    + BG_STYLE
    + ".tt{fill:#11181c}.tm{fill:#697177}.tl{fill:#3a4248}.tw{fill:#fff}"
    ".grid{stroke:#e6e9ed;stroke-width:1;stroke-dasharray:2 4}"
    ".gridkey{stroke:#c3cbd2;stroke-width:1}"
    ".zebra{fill:#11181c;opacity:.022}"
    ".b{font-weight:700}.b6{font-weight:600}"
    ".e{text-anchor:end}.m{text-anchor:middle}"
    ".o9{opacity:.9}.o7{opacity:.72}"
    ".lnk{text-decoration:none;text-decoration-thickness:.6px;}"
    ".lnk:hover{fill:#11181c}"
    ".s95{font-size:9.5px}.s93{font-size:9.3px}.s85{font-size:8.5px}"
    ".s83{font-size:8.3px}.s11{font-size:11px}.s13{font-size:13px}"
    ".s125{font-size:12.5px}"
    ".dnf{fill:#9b1c1c;font-weight:700;paint-order:stroke;stroke:#f6e4e3;"
    "stroke-width:2.6px;stroke-linejoin:round}"
    ".dnfr{fill:url(#dnf);stroke:#d99b98;stroke-width:.8px}"
    "@media (prefers-color-scheme:dark){"
    ".tt{fill:#e6edf3}.tm{fill:#8b949e}.tl{fill:#c3cbd6}"
    ".grid{stroke:#21262d}.gridkey{stroke:#3a434d}"
    ".lnk:hover{fill:#e6edf3}"
    ".zebra{fill:#fff;opacity:.025}" + BG_DARK_STYLE + "}"
    "</style>"
)

DEFS = (
    "<defs>" + STYLE + '<linearGradient id="capgrad" x1="0" y1="0" x2="1" y2="0">'
    '<stop offset="0" stop-color="#fff"/>'
    '<stop offset="0.86" stop-color="#fff"/>'
    '<stop offset="1" stop-color="#fff" stop-opacity="0.18"/>'
    "</linearGradient>"
    '<mask id="capfade" maskContentUnits="objectBoundingBox">'
    '<rect width="1" height="1" fill="url(#capgrad)"/></mask>'
    '<pattern id="dnf" width="6" height="6" patternUnits="userSpaceOnUse" '
    'patternTransform="rotate(45)"><rect width="6" height="6" fill="#f6e4e3"/>'
    '<line x1="0" y1="0" x2="0" y2="6" stroke="#d99b98" stroke-width="2"/>'
    "</pattern></defs>"
)


# ---- data ------------------------------------------------------------------------


def load():
    """bench/results.json -> ([(suite, test)] in run order, {(suite, test): {framework: row}})."""
    with open(RESULTS) as f:
        rows = json.load(f)
    data = {}
    for r in rows:
        data.setdefault((r["suite"], r["test"]), {})[r["framework"]] = r
    return list(data), data


def load_memory():
    """bench/memory.json -> {framework: {liveKB, retainedKB, steadyKB, bytesPerNode}} (empty if absent)."""
    try:
        with open(MEMORY) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def fastest(group):
    """A row's fastest finish ({framework: row} -> seconds), or None if no framework finished it."""
    return min((r["seconds"] for r in group.values() if r["seconds"] is not None), default=None)


def geomeans(order, data):
    """Each framework's geometric mean of time relative to every row's fastest finisher, over the rows at
    least one framework finished, a DNF charged its row's cap. Returns ({framework: geomean or None}, rows)."""
    logs = {fw: [] for key in order for fw in data[key]}
    counted = 0
    for key in order:
        best = fastest(data[key])
        if best is None:
            continue
        counted += 1
        for fw, r in data[key].items():
            if r["seconds"] is not None:
                logs[fw].append(math.log(r["seconds"] / best))
            elif r["status"] not in EXCLUDED:
                logs[fw].append(math.log(r["cap"] / best))
    return {fw: math.exp(sum(xs) / len(xs)) if xs else None for fw, xs in logs.items()}, counted


def ranked(geo):
    """Frameworks fastest geomean first, any without one last."""
    return sorted(geo, key=lambda fw: (geo[fw] is None, geo[fw] or 0.0))


# ---- formatting ------------------------------------------------------------------


def esc(s):
    return html.escape(str(s))


def xrel(v):
    return X0 + min(v, MAX_REL) / MAX_REL * PLOT_W


def fmt_mult(v):
    return f"{v:.1f}×" if v < 10 else f"{v:.0f}×"


def fmt_ms(secs):
    ms = secs * 1000.0
    if ms >= 100:
        return f"{ms:.0f} ms"
    if ms >= 10:
        return f"{ms:.1f} ms"
    if ms >= 1:
        return f"{ms:.2f} ms"
    return f"{ms:.3f} ms"


def fmt_evals(n):
    if n >= 1e6:
        return f"{n / 1e6:.1f}M evals"
    if n >= 1e3:
        return f"{n / 1e3:.1f}k evals"
    return f"{n:.0f} evals"


def fmt_kb(kb):
    if kb >= 1024:
        return f"{kb / 1024:.1f} MB"
    if kb < 10:
        return f"{kb:.1f} KB"
    return f"{kb:.0f} KB"


def _oklch_to_hex(L, C, H):
    h = math.radians(H)
    a, b = C * math.cos(h), C * math.sin(h)

    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3

    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    def to_srgb(u):
        u = max(0.0, min(1.0, u))
        u = 1.055 * u ** (1 / 2.4) - 0.055 if u > 0.0031308 else 12.92 * u
        return round(max(0.0, min(1.0, u)) * 255)

    return "#%02X%02X%02X" % (to_srgb(r), to_srgb(g), to_srgb(bl))


def rainbow(n, L=0.6, C=0.2):
    return [_oklch_to_hex(L, C, 360 * (i / n)) for i in range(n)]


def document(body, h):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" '
        f'viewBox="0 0 {W} {h}" role="img" '
        f'aria-label="Luau reactivity benchmark results, lower is faster">'
        + DEFS
        + f'<rect x="0" y="0" width="{W}" height="{h}" class="bg"/>'
        + body
        + "</svg>\n"
    )


# ---- render ----------------------------------------------------------------------


def main(flag="-O2"):
    order, data = load()
    mem = load_memory()
    geo, complete_rows = geomeans(order, data)
    frameworks = ranked(geo)
    colors = dict(zip(frameworks, rainbow(len(frameworks))))
    # the per-run deadline: the largest cap (kairo's is per iteration)
    charge = max(r["cap"] for g in data.values() for r in g.values())

    suites = {}
    for key in order:
        suites.setdefault(key[0], []).append(key)

    front, behind = [], []

    # ---- header ----------------------------------------------------------------
    front.append(
        f'<text x="{M_L}" y="{16 + M_T}" font-size="23" font-weight="800" class="tt">'
        "Luau reactivity benchmark</text>"
    )
    luau = f"headless luau {esc(LUAU_VERSION)} " if LUAU_VERSION else "headless luau "
    front.append(
        f'<text x="{M_L}" y="{37 + M_T}" class="tm s125">{luau}<tspan class="mono">{esc(flag)}</tspan>'
        " · per-bench process isolation · normalised per row to the fastest (shorter = faster)</text>"
    )

    # ---- shared x-axis: one 0-10× scale for both the hero and the detail --------
    y = 60 + M_T
    for t in TICKS:
        front.append(f'<text x="{xrel(t):.1f}" y="{y}" class="tm mono m s95">{t}×</text>')
    front.append(
        f'<text x="{X0 - 14}" y="{y}" class="tm e s95">relative time, lower is faster →</text>'
    )
    axis_top = y + 6

    def footer(total_h, detail):
        noise = "noise is about ±10% even with fastest-of-N and several passes, so treat smaller gaps as ties."
        last = (
            f"evals = computed + effect bodies run per kairo iteration / cellx propagation / dynamic run · run-to-run {noise}"
            if detail
            else f"Run-to-run {noise}"
        )
        return (
            f'<text x="{M_L}" y="{total_h - 39}" class="tm s95">'
            f'Port of <a href="https://github.com/milomg/js-reactivity-benchmark" class="lnk">'
            "milomg/js-reactivity-benchmark</a> · normalised per row to the fastest "
            "framework (1.0×, shorter = faster) · process-isolated, upstream's warm-ups and fastest-of-N (cellx: mean).</text>"
            f'<text x="{M_L}" y="{total_h - 25}" class="tm s95">'
            f"Faded bars exceed {int(MAX_REL)}× (true multiple labelled) · a DNF is a run past the "
            f"{charge:g}s deadline and is charged exactly that run time in the geomean on every row "
            "(an unexpected error is excluded instead).</text>"
            f'<text x="{M_L}" y="{total_h - 11}" class="tm s95">{last}</text>'
        )

    def bar(by, fw, rel, h=BAR_H, rx=3):
        w = max(2.5, xrel(rel) - X0)
        mask = ' mask="url(#capfade)"' if rel > MAX_REL else ""
        front.append(
            f'<rect x="{X0}" y="{by}" width="{w:.1f}" height="{h}" '
            f'rx="{rx}" fill="{colors[fw]}"{mask}/>'
        )
        return w

    def bar_label(by, fw, mult, amount, w, evals=None):
        # framework + multiple (+ amount, + node evaluations): inside the bar when wide, else past it
        cy = by + BAR_H / 2 + 3.3
        extra = f" · {fmt_evals(evals)}" if evals else ""
        text = f"{fw} {mult} {amount}{extra}" if mult else f"{fw} {amount}{extra}"
        # ~4.3px per character across the label's mixed 9.3/8.3px runs, plus the side padding
        if w > 18 + 4.3 * len(text):
            front.append(
                f'<text x="{X0 + 9}" y="{cy:.1f}" class="tw s93">'
                f'<tspan class="o9">{esc(fw)}</tspan>'
                + (f'<tspan class="b" dx="5">{mult}</tspan>' if mult else "")
                + f'<tspan class="o7 s83" dx="5">{amount}{extra}</tspan></text>'
            )
        else:
            front.append(
                f'<text x="{X0 + w + 7:.1f}" y="{cy:.1f}" class="tl s93">'
                f"<tspan>{esc(fw)}</tspan>"
                + (f'<tspan class="tt b" dx="5">{mult}</tspan>' if mult else "")
                + f'<tspan class="tm s83" dx="5">{amount}{extra}</tspan></text>'
            )

    gi = 0  # global group index, for zebra striping

    def group_head(gy, n, label, baseline):
        # zebra stripe, then the row label over this row's 1.0× baseline
        nonlocal gi
        gh = n * BAR_H + (n - 1) * BAR_GAP
        if gi % 2 == 1:
            behind.append(
                f'<rect x="{M_L}" y="{gy - 4:.1f}" width="{W - M_L - M_R}" '
                f'height="{gh + 8:.1f}" rx="4" class="zebra"/>'
            )
        gi += 1
        front.append(
            f'<text x="{X0 - 14}" y="{gy + gh / 2 + 1:.1f}" '
            f'class="tl mono e b6 s11">{esc(label)}</text>'
        )
        if baseline:
            front.append(
                f'<text x="{X0 - 14}" y="{gy + gh / 2 + 13:.1f}" '
                f'class="tm mono e s85">{esc(baseline)}</text>'
            )
        return gh

    def section_head(y, title, sub):
        front.append(
            f'<text x="{M_L}" y="{y + 14}" class="tt b s13">{esc(title)}'
            f'<tspan class="tm s11" font-weight="500" dx="8">{esc(sub)}</tspan></text>'
        )
        return y + SUITE_HEAD

    # ---- hero: overall geometric mean (also the legend) ------------------------
    y = axis_top + 18
    front.append(
        f'<text x="{M_L}" y="{y}" class="tl b s11" '
        f'letter-spacing="0.5">OVERALL · GEOMETRIC MEAN</text>'
    )
    front.append(
        f'<text x="{X1}" y="{y}" class="tm e" font-size="10">'
        f"over {complete_rows} of {len(order)} rows · DNF charged the {charge:g}s per-run deadline</text>"
    )
    y += 12

    for fw in frameworks:
        g = geo[fw]
        if g is None:
            continue
        w = bar(y, fw, g, HERO_BAR_H, 4)
        cy = y + HERO_BAR_H / 2 + 4.5
        label, ver, link = assemble.package_meta(fw) or (fw, None, None)
        scope, sep, name = label.partition("/")
        name_svg = f'<tspan class="tm">{esc(scope)}/</tspan>{esc(name)}' if sep else esc(label)
        if link:
            name_svg = f'<a href="{esc(link)}" class="lnk" target="_blank">{name_svg}</a>'
        ver_svg = (
            f'<tspan class="tm s85" font-weight="500" dx="5">{esc(ver)}</tspan>' if ver else ""
        )
        front.append(
            f'<text x="{X0 - 12}" y="{cy:.1f}" class="tt b e s125">{name_svg}{ver_svg}</text>'
        )
        if w > 64:
            front.append(f'<text x="{X0 + 10}" y="{cy:.1f}" class="tw b s125">{g:.2f}×</text>')
        else:
            front.append(
                f'<text x="{X0 + w + 8:.1f}" y="{cy:.1f}" class="tt b s125">{g:.2f}×'
                f'<tspan class="tm b6">  fastest</tspan></text>'
            )
        y += HERO_BAR_H + HERO_GAP

    # ---- small output: the hero alone -----------------------------------------
    small_h = y + 60
    with open(OUT_SMALL, "w") as f:
        f.write(document("".join(front) + footer(small_h, False), small_h))

    # ---- detail: per-suite, per-benchmark groups -------------------------------
    y += 12
    front.append(
        f'<text x="{M_L}" y="{y}" class="tl b s11" letter-spacing="0.5">PER-BENCHMARK</text>'
    )
    y += 12

    for suite, keys in suites.items():
        y = section_head(y, *SUITE_TITLES.get(suite, (suite, "")))
        for key in keys:
            group = data[key]
            present = [fw for fw in frameworks if fw in group]
            best = fastest(group)
            gh = group_head(y, len(present), key[1], fmt_ms(best) if best is not None else "")
            by = y
            for fw in present:
                r = group[fw]
                if r["seconds"] is None:
                    reason = DNF_REASON.get(r["status"], r["status"])
                    front.append(
                        f'<rect x="{X0}" y="{by}" width="{PLOT_W}" height="{BAR_H}" '
                        f'rx="3" class="dnfr"/>'
                        f'<text x="{X0 + 9}" y="{by + BAR_H - 3.8:.1f}" '
                        f'class="dnf s93">{esc(fw)} · DNF ({esc(reason)})</text>'
                    )
                else:
                    rel = r["seconds"] / best
                    w = bar(by, fw, rel)
                    bar_label(by, fw, fmt_mult(rel), fmt_ms(r["seconds"]), w, r.get("evals"))
                by += BAR_H + BAR_GAP
            y += gh + GROUP_PAD
        y += 8  # gap between suites

    # ---- memory: footprint + retention from bench/memory.json ------------------
    mem_present = [fw for fw in frameworks if fw in mem]
    if mem_present:
        y = section_head(
            y,
            "memory",
            f"{3 * mem[mem_present[0]]['N'] // 1000}k-node graph · footprint ×leanest, retention in KB (~0 = fully released) · "
            "excluded from the overall geometric mean",
        )
        # retention sits near zero and moves in whole-KB steps (and table-capacity jumps), so a
        # multiple of the leanest would be noise: those rows show the absolute figure only
        for rowname, field, fmt, relative in (
            ("node footprint", "bytesPerNode", lambda b: f"{b:.0f} B", True),
            ("build retained", "retainedKB", fmt_kb, False),
            ("steady retained", "steadyKB", fmt_kb, False),
        ):
            vals = {fw: mem[fw].get(field) for fw in mem_present}
            best = (
                min((v for v in vals.values() if v is not None and v > 0), default=None)
                if relative
                else None
            )
            gh = group_head(y, len(mem_present), rowname, fmt(best) if best else "")
            by = y
            for fw in mem_present:
                v = vals[fw]
                rel = v / best if v and v > 0 and best else 0.0
                w = bar(by, fw, rel)
                bar_label(
                    by, fw, fmt_mult(rel) if best else "", fmt(v) if v is not None else "n/a", w
                )
                by += BAR_H + BAR_GAP
            y += gh + GROUP_PAD
        y += 8

    # ---- gridlines (drawn behind everything in the detail band) ----------------
    for t in TICKS:
        tx = xrel(t)
        behind.append(
            f'<line x1="{tx:.1f}" y1="{axis_top + 2}" x2="{tx:.1f}" y2="{y - 4:.1f}" '
            f'class="{"gridkey" if t == 1 else "grid"}"/>'
        )

    total_h = y + 60
    with open(OUT, "w") as f:
        f.write(document("".join(behind) + "".join(front) + footer(total_h, True), total_h))
    print(f"wrote {OUT} ({W}x{total_h}px, {len(order)} benchmarks)")


if __name__ == "__main__":
    main(*sys.argv[1:2])
