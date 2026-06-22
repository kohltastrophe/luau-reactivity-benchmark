#!/usr/bin/env python3
import json
import math
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(ROOT, "bench/results.json")
MEMORY = os.path.join(ROOT, "bench/memory.json")
OUT = os.path.join(ROOT, "chart.svg")
OUT_SMALL = os.path.join(ROOT, "chart-small.svg")

CAP = float(os.environ.get("BENCH_CAP", 60))

IN_GHA = "GITHUB_ACTIONS" in os.environ
BG_STYLE = ".bg{fill:#0000}" if IN_GHA else ".bg{fill:#fff}"
BG_DARK_STYLE = "" if IN_GHA else ".bg{fill:#0d1117}"

DNF_REASON = {
    "overflow": "stack overflow",
    "table-overflow": "table overflow",
    "timeout": "timeout",
    "error": "unexpected error",
    "dnf": "unexpected error",
}

EXCLUDED_FROM_MEAN = {"error", "dnf"}

SUITE_TITLES = {
    "kairo": ("kairo", "propagation patterns"),
    "cellx": ("cellx", "interlinked layer lattice"),
    "dynamic": ("dynamic graph", "static + runtime-varying dependencies"),
    "sbench": ("sbench", "create / update throughput"),
    "gc": ("teardown & GC", "dispose cost + full-collection reclaim time"),
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
    ".lnk{text-decoration:underline;text-decoration-thickness:.6px;"
    "text-underline-offset:2px}"
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


def load():
    with open(RESULTS) as f:
        rows = json.load(f)
    order, seen, data = [], set(), {}
    for r in rows:
        key = (r["suite"], r["test"])
        if key not in seen:
            seen.add(key)
            order.append(key)
            data[key] = {}
        data[key][r["framework"]] = r
    return order, data


def load_memory():
    """bench/memory.json -> {framework: {liveKB, retainedKB, bytesPerNode}} (empty if absent)."""
    try:
        with open(MEMORY) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def dnf_reason(status):
    return DNF_REASON.get(status, status or "unexpected error")


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


def fmt_bytes_node(b):
    return f"{b:.0f} B"


def fmt_kb(kb):
    if kb >= 1024:
        return f"{kb / 1024:.1f} MB"
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


def frameworks_and_colors(order, data):
    seen = []
    for key in order:
        for fw in data[key]:
            if fw not in seen:
                seen.append(fw)
    rels = {fw: [] for fw in seen}
    rows_counted = 0
    for key in order:
        group = data[key]
        done = [
            group[fw]["seconds"]
            for fw in seen
            if fw in group and group[fw].get("seconds") is not None
        ]
        if not done:
            continue  # no framework finished this row; nothing to normalise against
        rows_counted += 1
        best = min(done)
        for fw in seen:
            r = group.get(fw)
            if r is None:
                continue  # framework not present in this row at all
            secs = r.get("seconds")
            if secs is not None:
                rels[fw].append(secs / best)
            elif r.get("status") not in EXCLUDED_FROM_MEAN:
                rels[fw].append(CAP / best)  # failed to complete -> charged the cap
            # else: a genuine unexpected error -> left out of this framework's mean
    geo = {
        fw: (math.exp(sum(map(math.log, xs)) / len(xs)) if xs else None)
        for fw, xs in rels.items()
    }
    frameworks = sorted(
        seen, key=lambda fw: (geo[fw] is None, geo[fw] or 0.0, seen.index(fw))
    )
    colors = dict(zip(frameworks, rainbow(len(frameworks))))
    return frameworks, colors, geo, rows_counted


def main():
    order, data = load()
    MEM = load_memory()
    FRAMEWORKS, COLORS, geo, complete_rows = frameworks_and_colors(order, data)

    suites, cur = [], None
    for suite, test in order:
        if suite != cur:
            suites.append([suite, []])
            cur = suite
        suites[-1][1].append((suite, test))

    front, behind = [], []

    # ---- header ----------------------------------------------------------------
    front.append(
        f'<text x="{M_L}" y="{16 + M_T}" font-size="23" font-weight="800" class="tt">'
        "Luau reactivity benchmark</text>"
    )
    front.append(
        f'<text x="{M_L}" y="{37 + M_T}" class="tm s125">headless luau <tspan class="mono">-O2</tspan>'
        "· per-bench process isolation · normalised per row to the fastest (shorter = faster)</text>"
    )

    # ---- shared x-axis: one 0-10× scale for both the hero and the detail --------
    y = 60 + M_T
    for t in TICKS:
        tx = xrel(t)
        front.append(f'<text x="{tx:.1f}" y="{y}" class="tm mono m s95">{t}×</text>')
    front.append(
        f'<text x="{X0 - 14}" y="{y}" class="tm e s95">'
        f"relative time, lower is faster →</text>"
    )
    axis_top = y + 6

    def bar_label(by, bh, fw, mult, ms, w):
        # framework + multiple (+ ms): inside the bar when wide, else just past it
        cy = by + bh / 2 + 3.3
        if w > 132:
            return (
                f'<text x="{X0 + 9}" y="{cy:.1f}" class="tw s93">'
                f'<tspan class="o9">{esc(fw)}</tspan>'
                f'<tspan class="b" dx="5">{mult}</tspan>'
                f'<tspan class="o7 s83" dx="5">{ms}</tspan></text>'
            )
        return (
            f'<text x="{X0 + w + 7:.1f}" y="{cy:.1f}" class="tl s93">'
            f"<tspan>{esc(fw)}</tspan>"
            f'<tspan class="tt b" dx="5">{mult}</tspan>'
            f'<tspan class="tm s83" dx="5">{ms}</tspan></text>'
        )

    # ---- hero: overall geometric mean (also the legend) ------------------------
    y = axis_top + 18
    front.append(
        f'<text x="{M_L}" y="{y}" class="tl b s11" '
        f'letter-spacing="0.5">OVERALL · GEOMETRIC MEAN</text>'
    )
    front.append(
        f'<text x="{X1}" y="{y}" class="tm e" font-size="10">'
        f"over {complete_rows} of {len(order)} rows · DNF charged the {int(CAP)}s cap</text>"
    )
    y += 12

    for fw in FRAMEWORKS:
        g = geo[fw]
        if g is None:
            continue
        by = y
        capped = g > MAX_REL
        w = max(2.5, xrel(g) - X0)
        mask = ' mask="url(#capfade)"' if capped else ""
        cy = by + HERO_BAR_H / 2 + 4.5
        front.append(
            f'<rect x="{X0}" y="{by}" width="{w:.1f}" height="{HERO_BAR_H}" '
            f'rx="4" fill="{COLORS[fw]}"{mask}/>'
        )
        front.append(
            f'<text x="{X0 - 12}" y="{cy:.1f}" class="tt b e s125">{esc(fw)}</text>'
        )
        vtxt = f"{g:.2f}×"
        if w > 64:
            front.append(
                f'<text x="{X0 + 10}" y="{cy:.1f}" class="tw b s125">{vtxt}</text>'
            )
        else:
            front.append(
                f'<text x="{X0 + w + 8:.1f}" y="{cy:.1f}" class="tt b s125">{vtxt}'
                f'<tspan class="tm b6">  fastest</tspan></text>'
            )
        y += HERO_BAR_H + HERO_GAP

    # ---- small output -----------------------------------------------------------

    total_h = y + 46

    front.append(
        f'<text x="{M_L}" y="{total_h - 25}" class="tm s95">'
        f'Port of <a href="https://github.com/milomg/js-reactivity-benchmark" class="lnk">milomg/js-reactivity-benchmark</a> · normalised per row to the fastest '
        f"framework (1.0×, shorter = faster) · process-isolated, fastest-of-N per run.</text>"
        f'<text x="{M_L}" y="{total_h - 11}" class="tm s95">'
        f"Faded bars exceed {int(MAX_REL)}× (true multiple labelled) · a DNF is charged the "
        f"{int(CAP)}s cap in the geomean (an unexpected error is excluded instead).</text>"
    )

    defs = (
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
    bg = f'<rect x="0" y="0" width="{W}" height="{total_h}" class="bg"/>'

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{total_h}" '
        f'viewBox="0 0 {W} {total_h}" role="img" '
        f'aria-label="Luau reactivity benchmark results, lower is faster">'
        + defs
        + bg
        + "".join(behind)
        + "".join(front)
        + "</svg>\n"
    )

    with open(OUT_SMALL, "w") as f:
        f.write(svg)

    front.pop()

    # ---- detail plot -----------------------------------------------------------
    y += 12
    front.append(
        f'<text x="{M_L}" y="{y}" class="tl b s11" '
        f'letter-spacing="0.5">PER-BENCHMARK</text>'
    )
    y += 12

    # ---- detail: per-suite, per-benchmark groups -------------------------------
    gi = 0  # global group index, for zebra striping
    for suite, tests in suites:
        title, sub = SUITE_TITLES.get(suite, (suite, ""))
        front.append(
            f'<text x="{M_L}" y="{y + 14}" class="tt b s13">{esc(title)}'
            f'<tspan class="tm s11" font-weight="500" dx="8">{esc(sub)}'
            f"</tspan></text>"
        )
        y += SUITE_HEAD

        for key in tests:
            group = data[key]
            test = key[1]
            present = [fw for fw in FRAMEWORKS if fw in group]
            times = [
                group[fw]["seconds"]
                for fw in present
                if group[fw].get("seconds") is not None
            ]
            best = min(times) if times else None
            n = len(present)
            gh = n * BAR_H + (n - 1) * BAR_GAP
            gy = y

            if gi % 2 == 1:
                behind.append(
                    f'<rect x="{M_L}" y="{gy - 4:.1f}" width="{W - M_L - M_R}" '
                    f'height="{gh + 8:.1f}" rx="4" class="zebra"/>'
                )
            gi += 1

            # row label + the fastest framework's absolute time (this row's 1.0× baseline),
            base_ms = fmt_ms(best) if best is not None else ""
            front.append(
                f'<text x="{X0 - 14}" y="{gy + gh / 2 + 1:.1f}" '
                f'class="tl mono e b6 s11">{esc(test)}</text>'
            )
            if base_ms:
                front.append(
                    f'<text x="{X0 - 14}" y="{gy + gh / 2 + 13:.1f}" '
                    f'class="tm mono e s85">{esc(base_ms)}</text>'
                )

            by = gy
            for fw in present:
                secs = group[fw].get("seconds")
                if secs is None:
                    # DNF
                    reason = dnf_reason(group[fw].get("status"))
                    front.append(
                        f'<rect x="{X0}" y="{by}" width="{PLOT_W}" height="{BAR_H}" '
                        f'rx="3" class="dnfr"/>'
                    )
                    front.append(
                        f'<text x="{X0 + 9}" y="{by + BAR_H - 3.8:.1f}" '
                        f'class="dnf s93">{esc(fw)} · DNF ({esc(reason)})</text>'
                    )
                else:
                    rel = secs / best if best else 1.0
                    w = max(2.5, xrel(rel) - X0)
                    mask = ' mask="url(#capfade)"' if rel > MAX_REL else ""
                    front.append(
                        f'<rect x="{X0}" y="{by}" width="{w:.1f}" height="{BAR_H}" '
                        f'rx="3" fill="{COLORS[fw]}"{mask}/>'
                    )
                    front.append(
                        bar_label(by, BAR_H, fw, fmt_mult(rel), fmt_ms(secs), w)
                    )
                by += BAR_H + BAR_GAP
            y = gy + gh + GROUP_PAD
        y += 8  # gap between suites

    # ---- memory: footprint + retention from bench/memory.json ------------------
    mem_present = [fw for fw in FRAMEWORKS if fw in MEM] if MEM else []
    if mem_present:
        mem_rows = [
            ("footprint", lambda d: d.get("bytesPerNode"), fmt_bytes_node),
            ("retained", lambda d: d.get("retainedKB"), fmt_kb),
        ]
        front.append(
            f'<text x="{M_L}" y="{y + 14}" class="tt b s13">memory'
            f'<tspan class="tm s11" font-weight="500" dx="8">'
            f"footprint &amp; retention · 60k-node graph · ×leanest, lower = leaner"
            f"</tspan></text>"
        )
        y += SUITE_HEAD
        for rowname, getval, fmt in mem_rows:
            vals = {fw: getval(MEM[fw]) for fw in mem_present}
            positives = [v for v in vals.values() if v is not None and v > 0]
            if not positives:
                continue
            best = min(positives)
            n = len(mem_present)
            gh = n * BAR_H + (n - 1) * BAR_GAP
            gy = y
            if gi % 2 == 1:
                behind.append(
                    f'<rect x="{M_L}" y="{gy - 4:.1f}" width="{W - M_L - M_R}" '
                    f'height="{gh + 8:.1f}" rx="4" class="zebra"/>'
                )
            gi += 1
            front.append(
                f'<text x="{X0 - 14}" y="{gy + gh / 2 + 1:.1f}" '
                f'class="tl mono e b6 s11">{esc(rowname)}</text>'
            )
            front.append(
                f'<text x="{X0 - 14}" y="{gy + gh / 2 + 13:.1f}" '
                f'class="tm mono e s85">{esc(fmt(best))}</text>'
            )
            by = gy
            for fw in mem_present:
                v = vals[fw]
                if v is None or v <= 0:
                    rel, w = 0.0, 2.5
                else:
                    rel = v / best
                    w = max(2.5, xrel(rel) - X0)
                mask = ' mask="url(#capfade)"' if rel > MAX_REL else ""
                front.append(
                    f'<rect x="{X0}" y="{by}" width="{w:.1f}" height="{BAR_H}" '
                    f'rx="3" fill="{COLORS[fw]}"{mask}/>'
                )
                lbl = fmt(v) if v is not None else "n/a"
                front.append(bar_label(by, BAR_H, fw, fmt_mult(rel), lbl, w))
                by += BAR_H + BAR_GAP
            y = gy + gh + GROUP_PAD
        y += 8

    detail_bottom = y

    # ---- gridlines (drawn behind everything in the detail band) ----------------
    for t in TICKS:
        tx = xrel(t)
        cls = "gridkey" if t == 1 else "grid"
        behind.append(
            f'<line x1="{tx:.1f}" y1="{axis_top + 2}" x2="{tx:.1f}" '
            f'y2="{detail_bottom - 4:.1f}" class="{cls}"/>'
        )

    total_h = detail_bottom + 46

    # ---- footer (two lines so nothing is clipped on either axis) ---------------
    front.append(
        f'<text x="{M_L}" y="{total_h - 25}" class="tm s95">'
        f'Port of <a href="https://github.com/milomg/js-reactivity-benchmark" class="lnk">milomg/js-reactivity-benchmark</a> · normalised per row to the fastest '
        f"framework (1.0×, shorter = faster) · process-isolated, fastest-of-N per run.</text>"
        f'<text x="{M_L}" y="{total_h - 11}" class="tm s95">'
        f"Faded bars exceed {int(MAX_REL)}× (true multiple labelled) · a DNF is charged the "
        f"{int(CAP)}s cap in the geomean (an unexpected error is excluded instead).</text>"
    )

    defs = (
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
    bg = f'<rect x="0" y="0" width="{W}" height="{total_h}" class="bg"/>'

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{total_h}" '
        f'viewBox="0 0 {W} {total_h}" role="img" '
        f'aria-label="Luau reactivity benchmark results, lower is faster">'
        + defs
        + bg
        + "".join(behind)
        + "".join(front)
        + "</svg>\n"
    )

    with open(OUT, "w") as f:
        f.write(svg)
    print(f"wrote {OUT} ({W}x{total_h}px, {len(order)} benchmarks)")


if __name__ == "__main__":
    main()
