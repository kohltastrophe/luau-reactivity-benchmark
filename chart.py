#!/usr/bin/env python3
"""Render `bench/results.json` to `chart.svg` and `chart-small.svg`.

Called automatically by `benchmark.py`, or run standalone (`python3 chart.py [--flag FLAG]`) to re-render
from existing JSON results.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import html
import json
import math
import os
from pathlib import Path
from typing import Any

import assemble

ROOT = assemble.ROOT
RESULTS = ROOT / "bench" / "results.json"
OUT = ROOT / "chart.svg"
OUT_SMALL = ROOT / "chart-small.svg"

DNF_REASON: dict[str, str] = {
    "overflow": "stack overflow",
    "table-overflow": "table overflow",
    "oom": "out of memory",
    "timeout": "timeout",
    "wrong": "wrong value",
    "error": "unexpected error",
}

SUITE_TITLES: dict[str, tuple[str, str]] = {
    "kairo": ("kairo", "propagation patterns"),
    "cellx": ("cellx", "interlinked layer lattice"),
    "dynamic": ("dynamic graph", "static + runtime-varying dependencies"),
    "sbench": ("sbench", "create / update throughput"),
}
SUITE_UNITS: dict[str, int] = {
    "kairo": 50,
}

# ---- geometry ------------------------------------------------------------------
W = 960
GUTTER = 188  # row-label gutter width
MAX_REL = 10.0  # x-axis cap (bars beyond this fade out and show their true value)
TICKS = (1, 2, 4, 6, 8, 10)

BAR_H = 14
BAR_GAP = 4
GROUP_PAD = 9
SUITE_HEAD = 30
HERO_BAR_H = 22
HERO_GAP = 7

RowKey = tuple[str, str]
GroupedRows = dict[RowKey, dict[str, dict[str, Any]]]


@dataclass(frozen=True)
class Layout:
    in_gha: bool
    m_t: int
    m_l: int
    m_r: int
    x0: int
    x1: int
    plot_w: int

    def xrel(self, v: float) -> float:
        return self.x0 + min(v, MAX_REL) / MAX_REL * self.plot_w


@dataclass
class Canvas:
    front: list[str] = field(default_factory=list)
    behind: list[str] = field(default_factory=list)
    group_index: int = 0


def make_layout(in_gha: bool | None = None) -> Layout:
    gha = ("GITHUB_ACTIONS" in os.environ) if in_gha is None else in_gha
    m_t = 8 if gha else 24
    m_l = 0 if gha else 24
    m_r = 8 if gha else 24
    x0 = m_l + GUTTER
    x1 = W - m_r
    return Layout(in_gha=gha, m_t=m_t, m_l=m_l, m_r=m_r, x0=x0, x1=x1, plot_w=x1 - x0)


def make_defs(in_gha: bool) -> str:
    bg_light = ".bg{fill:none}" if in_gha else ".bg{fill:#fff}"
    bg_dark = "" if in_gha else ".bg{fill:#0d1117}"
    style = (
        "<style>"
        "text{font-family:'Inter','Segoe UI',system-ui,-apple-system,sans-serif;"
        "-webkit-font-smoothing:antialiased}"
        ".mono{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace}"
        f"{bg_light}"
        ".tt{fill:#11181c}.tm{fill:#697177}.tl{fill:#3a4248}.tw{fill:#fff}"
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
        ".dnfbg{fill:#f6e4e3}.dnfln{stroke:#d99b98;stroke-width:2}"
        ".dnf{fill:#9b1c1c;font-weight:700;paint-order:stroke;stroke:#f6e4e3;"
        "stroke-width:2.6px;stroke-linejoin:round}"
        ".dnfr{fill:url(#dnf);stroke:#d99b98;stroke-width:.8px}"
        "@media (prefers-color-scheme:dark){"
        ".tt{fill:#e6edf3}.tm{fill:#8b949e}.tl{fill:#c3cbd6}"
        ".grid{stroke:#21262d}.gridkey{stroke:#3a434d}"
        ".lnk:hover{fill:#e6edf3}"
        ".zebra{fill:#fff;opacity:.025}"
        ".dnfbg{fill:#2b1517}.dnfln{stroke:#6e2c30}"
        ".dnfr{stroke:#6e2c30}.dnf{fill:#ff8e88;stroke:#2b1517}"
        f"{bg_dark}"
        "}"
        "</style>"
    )
    return (
        "<defs>"
        + style
        + '<linearGradient id="capgrad" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="#fff"/>'
        '<stop offset="0.86" stop-color="#fff"/>'
        '<stop offset="1" stop-color="#fff" stop-opacity="0.18"/>'
        "</linearGradient>"
        '<mask id="capfade" maskContentUnits="objectBoundingBox">'
        '<rect width="1" height="1" fill="url(#capgrad)"/></mask>'
        '<pattern id="dnf" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><rect width="6" height="6" class="dnfbg"/>'
        '<line x1="0" y1="0" x2="0" y2="6" class="dnfln"/>'
        "</pattern></defs>"
    )


# ---- data ------------------------------------------------------------------------


def group_rows(rows: Sequence[dict[str, Any]]) -> tuple[list[RowKey], GroupedRows]:
    """Group flat result rows into `(ordered_keys, {(suite, test): {framework: row}})`."""
    data: GroupedRows = {}
    for row in rows:
        data.setdefault((row["suite"], row["test"]), {})[row["framework"]] = row
    return list(data), data



def fastest(group: dict[str, dict[str, Any]]) -> float | None:
    """Return a row's fastest finishing time in seconds, or `None` if no framework finished."""
    return min((float(r["seconds"]) for r in group.values() if r["seconds"] is not None), default=None)


def _safe_ratio(value: float, baseline: float) -> float:
    denom = baseline if baseline > 0 else 1e-9
    num = value if value > 0 else 1e-9
    return num / denom


def geomeans(order: Sequence[RowKey], data: GroupedRows) -> tuple[dict[str, float | None], int]:
    """Compute per-framework geometric mean of relative times, charging every DNF/error its row `cap`."""
    logs: dict[str, list[float]] = {fw: [] for key in order for fw in data[key]}
    counted = 0
    for key in order:
        best = fastest(data[key])
        if best is None:
            continue
        counted += 1
        for fw, row in data[key].items():
            secs = row["seconds"]
            charged = float(secs) if secs is not None else float(row["cap"])
            logs[fw].append(math.log(_safe_ratio(charged, best)))
    return {fw: math.exp(sum(xs) / len(xs)) if xs else None for fw, xs in logs.items()}, counted


def ranked(geo: dict[str, float | None]) -> list[str]:
    """Sort frameworks fastest geomean first, placing any without a geomean last."""
    return sorted(geo, key=lambda fw: (geo[fw] is None, geo[fw] or 0.0, fw))


# ---- formatting ------------------------------------------------------------------


def esc(val: object) -> str:
    return html.escape(str(val))


def fmt_mult(v: float) -> str:
    return f"{v:.1f}×" if v < 10 else f"{v:.0f}×"


def fmt_ms(secs: float) -> str:
    ms = secs * 1000.0
    if ms >= 100:
        return f"{ms:.0f} ms"
    if ms >= 10:
        return f"{ms:.1f} ms"
    if ms >= 1:
        return f"{ms:.2f} ms"
    return f"{ms:.3f} ms"


def fmt_evals(n: float) -> str:
    if n >= 1e6:
        return f"{n / 1e6:.1f}M evals"
    if n >= 1e3:
        return f"{n / 1e3:.1f}k evals"
    return f"{n:.0f} evals"


def _oklch_to_hex(L: float, C: float, H: float) -> str:
    h = math.radians(H)
    a, b = C * math.cos(h), C * math.sin(h)

    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3

    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    def to_srgb(u: float) -> int:
        u = max(0.0, min(1.0, u))
        u = 1.055 * u ** (1 / 2.4) - 0.055 if u > 0.0031308 else 12.92 * u
        return round(max(0.0, min(1.0, u)) * 255)

    return f"#{to_srgb(r):02X}{to_srgb(g):02X}{to_srgb(bl):02X}"


def rainbow(n: int, L: float = 0.52, C: float = 0.17) -> list[str]:
    """Generate `n` perceptual Oklch bar colors with WCAG AA contrast against white text."""
    if n <= 0:
        return []
    return [_oklch_to_hex(L, C, 360.0 * (i / n)) for i in range(n)]


def document(body: str, h: float, in_gha: bool) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" '
        f'viewBox="0 0 {W} {h}" role="img" '
        f'aria-label="Luau reactivity benchmark results, lower is faster">'
        + make_defs(in_gha)
        + f'<rect x="0" y="0" width="{W}" height="{h}" class="bg"/>'
        + body
        + "</svg>\n"
    )


# ---- modular SVG builders --------------------------------------------------------


def _draw_bar(canvas: Canvas, layout: Layout, by: float, color: str, rel: float, h: int = BAR_H, rx: int = 3) -> float:
    w = max(2.5, layout.xrel(rel) - layout.x0)
    mask = ' mask="url(#capfade)"' if rel > MAX_REL else ""
    canvas.front.append(
        f'<rect x="{layout.x0}" y="{by}" width="{w:.1f}" height="{h}" rx="{rx}" fill="{color}"{mask}/>'
    )
    return w


def _draw_bar_label(
    canvas: Canvas,
    layout: Layout,
    by: float,
    fw: str,
    mult: str,
    amount: str,
    w: float,
    evals: float | None = None,
) -> None:
    cy = by + BAR_H / 2 + 3.3
    extra = f" · {fmt_evals(evals)}" if evals else ""
    text = f"{fw} {mult} {amount}{extra}" if mult else f"{fw} {amount}{extra}"
    inside = w > 18 + 4.3 * len(text)
    if inside:
        mult_svg = f'<tspan class="b" dx="5">{mult}</tspan>' if mult else ""
        canvas.front.append(
            f'<text x="{layout.x0 + 9}" y="{cy:.1f}" class="tw s93">'
            f'<tspan class="o9">{esc(fw)}</tspan>{mult_svg}'
            f'<tspan class="o7 s83" dx="5">{esc(amount)}{esc(extra)}</tspan></text>'
        )
    else:
        mult_svg = f'<tspan class="tt b" dx="5">{mult}</tspan>' if mult else ""
        canvas.front.append(
            f'<text x="{layout.x0 + w + 7:.1f}" y="{cy:.1f}" class="tl s93">'
            f"<tspan>{esc(fw)}</tspan>{mult_svg}"
            f'<tspan class="tm s83" dx="5">{esc(amount)}{esc(extra)}</tspan></text>'
        )


def _draw_group_head(canvas: Canvas, layout: Layout, gy: float, n: int, label: str, baseline: str) -> int:
    gh = n * BAR_H + max(0, n - 1) * BAR_GAP
    if canvas.group_index % 2 == 1:
        canvas.behind.append(
            f'<rect x="{layout.m_l}" y="{gy - 4:.1f}" width="{W - layout.m_l - layout.m_r}" '
            f'height="{gh + 8:.1f}" rx="4" class="zebra"/>'
        )
    canvas.group_index += 1
    canvas.front.append(
        f'<text x="{layout.x0 - 14}" y="{gy + gh / 2 + 1:.1f}" class="tl mono e b6 s11">{esc(label)}</text>'
    )
    if baseline:
        canvas.front.append(
            f'<text x="{layout.x0 - 14}" y="{gy + gh / 2 + 13:.1f}" class="tm mono e s85">{esc(baseline)}</text>'
        )
    return gh


def _draw_section_head(canvas: Canvas, layout: Layout, y: float, title: str, sub: str) -> float:
    canvas.front.append(
        f'<text x="{layout.m_l}" y="{y + 14}" class="tt b s13">{esc(title)}'
        f'<tspan class="tm s11" font-weight="500" dx="8">{esc(sub)}</tspan></text>'
    )
    return y + SUITE_HEAD


def _render_grid(layout: Layout, y_top: float, y_bottom: float) -> list[str]:
    return [
        f'<line x1="{layout.xrel(t):.1f}" y1="{y_top}" x2="{layout.xrel(t):.1f}" y2="{y_bottom:.1f}" '
        f'class="{"gridkey" if t == 1 else "grid"}"/>'
        for t in TICKS
    ]


def _render_header(canvas: Canvas, layout: Layout, flag: str, luau_version: str | None) -> float:
    canvas.front.append(
        f'<text x="{layout.m_l}" y="{16 + layout.m_t}" font-size="23" font-weight="800" class="tt">'
        "Luau reactivity benchmark</text>"
    )
    luau_label = f"headless luau {esc(luau_version)} " if luau_version else "headless luau "
    canvas.front.append(
        f'<text x="{layout.m_l}" y="{37 + layout.m_t}" class="tm s125">{luau_label}'
        f'<tspan class="mono">{esc(flag)}</tspan>'
        " · per-bench process isolation · normalized per row to the fastest (shorter = faster)</text>"
    )
    y = 60 + layout.m_t
    for t in TICKS:
        canvas.front.append(f'<text x="{layout.xrel(t):.1f}" y="{y}" class="tm mono m s95">{t}×</text>')
    canvas.front.append(
        f'<text x="{layout.x0 - 14}" y="{y}" class="tm e s95">relative time (lower is faster)</text>'
    )
    return y + 6


def _format_hero_name(fw: str) -> str:
    label, ver, link = assemble.package_meta(fw) or (fw, None, None)
    scope, sep, name = label.partition("/")
    name_svg = f'<tspan class="tm">{esc(scope)}/</tspan>{esc(name)}' if sep else esc(label)
    if link:
        name_svg = f'<a href="{esc(link)}" class="lnk" target="_blank">{name_svg}</a>'
    ver_svg = f'<tspan class="tm s85" font-weight="500" dx="5">{esc(ver)}</tspan>' if ver else ""
    return f"{name_svg}{ver_svg}"


def _render_hero(
    canvas: Canvas,
    layout: Layout,
    axis_top: float,
    frameworks: Sequence[str],
    geo: dict[str, float | None],
    colors: dict[str, str],
    complete_rows: int,
    total_rows: int,
    charge: float,
) -> float:
    y = axis_top + 18
    canvas.front.append(
        f'<text x="{layout.m_l}" y="{y}" class="tl b s11" letter-spacing="0.5">OVERALL · GEOMETRIC MEAN</text>'
    )
    canvas.front.append(
        f'<text x="{layout.x1}" y="{y}" class="tm e" font-size="10">'
        f"over {complete_rows} of {total_rows} rows · DNF charged the {charge:g}s per-run deadline</text>"
    )
    y += 12
    for fw in frameworks:
        g = geo[fw]
        cy = y + HERO_BAR_H / 2 + 4.5
        canvas.front.append(
            f'<text x="{layout.x0 - 12}" y="{cy:.1f}" class="tt b e s125">{_format_hero_name(fw)}</text>'
        )
        if g is None:
            canvas.front.append(
                f'<rect x="{layout.x0}" y="{y}" width="{layout.plot_w}" height="{HERO_BAR_H}" rx="4" class="dnfr"/>'
                f'<text x="{layout.x0 + 10}" y="{cy:.1f}" class="dnf s125">DNF</text>'
            )
        else:
            w = _draw_bar(canvas, layout, y, colors[fw], g, HERO_BAR_H, 4)
            label = f"{g:.2f}×"
            if w > 18 + 7.2 * len(label):
                canvas.front.append(f'<text x="{layout.x0 + 10}" y="{cy:.1f}" class="tw b s125">{label}</text>')
            else:
                canvas.front.append(f'<text x="{layout.x0 + w + 8:.1f}" y="{cy:.1f}" class="tt b s125">{label}</text>')
        y += HERO_BAR_H + HERO_GAP
    return y


def _render_benchmark_group(
    canvas: Canvas,
    layout: Layout,
    y: float,
    key: RowKey,
    group: dict[str, dict[str, Any]],
    frameworks: Sequence[str],
    colors: dict[str, str],
) -> float:
    present = [fw for fw in frameworks if fw in group]
    if not present:
        return y
    best = fastest(group)
    gh = _draw_group_head(canvas, layout, y, len(present), key[1], fmt_ms(best) if best is not None else "")
    by = y
    for fw in present:
        row = group[fw]
        secs = row["seconds"]
        if secs is None or best is None:
            reason = DNF_REASON.get(row["status"], str(row["status"]))
            canvas.front.append(
                f'<rect x="{layout.x0}" y="{by}" width="{layout.plot_w}" height="{BAR_H}" rx="3" class="dnfr"/>'
                f'<text x="{layout.x0 + 9}" y="{by + BAR_H - 3.8:.1f}" '
                f'class="dnf s93">{esc(fw)} · DNF ({esc(reason)})</text>'
            )
        else:
            rel = _safe_ratio(float(secs), best)
            w = _draw_bar(canvas, layout, by, colors[fw], rel)
            _draw_bar_label(canvas, layout, by, fw, fmt_mult(rel), fmt_ms(float(secs)), w, row.get("evals"))
        by += BAR_H + BAR_GAP
    return y + gh + GROUP_PAD


def _render_suites(
    canvas: Canvas,
    layout: Layout,
    y: float,
    order: Sequence[RowKey],
    data: GroupedRows,
    frameworks: Sequence[str],
    colors: dict[str, str],
) -> float:
    suites: dict[str, list[RowKey]] = {}
    for key in order:
        suites.setdefault(key[0], []).append(key)

    y += 12
    canvas.front.append(f'<text x="{layout.m_l}" y="{y}" class="tl b s11" letter-spacing="0.5">PER-BENCHMARK</text>')
    y += 12
    for suite, keys in suites.items():
        y = _draw_section_head(canvas, layout, y, *SUITE_TITLES.get(suite, (suite, "")))
        for key in keys:
            y = _render_benchmark_group(canvas, layout, y, key, data[key], frameworks, colors)
        y += 8
    return y



def _render_footer(layout: Layout, total_h: float, charge: float, detail: bool) -> str:
    noise = "noise is about ±10% even with fastest-of-N and several passes, so treat smaller gaps as ties."
    last = (
        f"evals = computed + effect bodies run per kairo iteration / cellx propagation / dynamic run · run-to-run {noise}"
        if detail
        else f"Run-to-run {noise}"
    )
    return (
        f'<text x="{layout.m_l}" y="{total_h - 39}" class="tm s95">'
        f'Port of <a href="https://github.com/milomg/js-reactivity-benchmark" class="lnk">'
        "milomg/js-reactivity-benchmark</a> · normalized per row to the fastest "
        "framework (1.0×, shorter = faster) · process-isolated, upstream's warm-ups and fastest-of-N (cellx: mean).</text>"
        f'<text x="{layout.m_l}" y="{total_h - 25}" class="tm s95">'
        f"Faded bars exceed {int(MAX_REL)}× (true multiple labeled) · every non-finishing run (DNF) is charged "
        f"the {charge:g}s per-run deadline in the geomean.</text>"
        f'<text x="{layout.m_l}" y="{total_h - 11}" class="tm s95">{last}</text>'
    )


def render_svg_pair(
    rows: Sequence[dict[str, Any]],
    flag: str = "-O2",
    in_gha: bool | None = None,
    luau_version: str | None = None,
) -> tuple[str, str, int]:
    """Render `(full_svg, small_svg, total_height)` in memory from parsed rows."""
    layout = make_layout(in_gha)
    version = os.environ.get("LUAU_VERSION") if luau_version is None else luau_version
    order, data = group_rows(rows)
    geo, complete_rows = geomeans(order, data)
    frameworks = ranked(geo)
    colors = dict(zip(frameworks, rainbow(len(frameworks))))
    charge = max(
        (float(r["cap"]) * SUITE_UNITS.get(r["suite"], 1) for g in data.values() for r in g.values()),
        default=8.0,
    )

    hero_canvas = Canvas()
    axis_top = _render_header(hero_canvas, layout, flag, version)
    hero_bottom = _render_hero(
        hero_canvas, layout, axis_top, frameworks, geo, colors, complete_rows, len(order), charge
    )

    small_grid = _render_grid(layout, axis_top + 2, hero_bottom - 4)
    small_h = int(hero_bottom + 60)
    small_svg = document(
        "".join(small_grid) + "".join(hero_canvas.front) + _render_footer(layout, small_h, charge, False),
        small_h,
        layout.in_gha,
    )

    full_canvas = Canvas(front=list(hero_canvas.front), behind=list(hero_canvas.behind))
    suites_bottom = _render_suites(full_canvas, layout, hero_bottom, order, data, frameworks, colors)
    full_grid = _render_grid(layout, axis_top + 2, suites_bottom - GROUP_PAD + 4)

    total_h = int(suites_bottom + 60)
    full_svg = document(
        "".join(full_canvas.behind)
        + "".join(full_grid)
        + "".join(full_canvas.front)
        + _render_footer(layout, total_h, charge, True),
        total_h,
        layout.in_gha,
    )
    return full_svg, small_svg, total_h


def render_from_data(
    rows: Sequence[dict[str, Any]],
    flag: str = "-O2",
    out_path: Path | str = OUT,
    out_small_path: Path | str = OUT_SMALL,
) -> None:
    """Render and write `chart.svg` and `chart-small.svg` from in-memory rows."""
    full_svg, small_svg, total_h = render_svg_pair(rows, flag=flag)
    Path(out_small_path).write_text(small_svg, encoding="utf-8")
    Path(out_path).write_text(full_svg, encoding="utf-8")
    bench_count = len({(r["suite"], r["test"]) for r in rows})
    print(f"wrote {out_path} ({W}x{total_h}px, {bench_count} benchmarks)")


def main(flag: str = "-O2") -> None:
    """Load `bench/results.json` and render both SVG charts."""
    rows = json.loads(Path(RESULTS).read_text(encoding="utf-8"))
    render_from_data(rows, flag=flag)


def _cli(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("positional_flag", nargs="?", help="luau flag label for the subtitle (default -O2)")
    ap.add_argument("--flag", default=None, help="luau flag label for the subtitle (default -O2)")
    args = ap.parse_args(argv)
    main(args.flag or args.positional_flag or "-O2")


if __name__ == "__main__":
    _cli()
