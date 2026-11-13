"""Unit tests for `assemble.py`, `benchmark.py`, and `chart.py`."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import assemble
import benchmark
import chart


def _srgb_luminance(hex_color: str) -> float:
    hx = hex_color.lstrip("#")
    channels: list[float] = []
    for i in (0, 2, 4):
        c = int(hx[i : i + 2], 16) / 255.0
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_against_white(hex_color: str) -> float:
    lum = _srgb_luminance(hex_color)
    return (1.0 + 0.05) / (lum + 0.05)


class AssembleTests(unittest.TestCase):
    def test_resolve_and_parent_container_shim(self) -> None:
        self.assertEqual(assemble._resolve(["a", "b"], False, ["Parent", "c"]), ["a", "b", "c"])
        self.assertEqual(assemble._resolve(["a", "b"], True, ["Parent", "c"]), ["a", "c"])
        with self.assertRaises(ValueError):
            assemble._resolve([], False, ["Parent", "Parent", "React"], allow_container=False)
        self.assertEqual(
            assemble._resolve([], False, ["Parent", "Parent", "React"], allow_container=True),
            ["^", "React"],
        )

    def test_require_path_from_nested_init(self) -> None:
        self.assertEqual(
            assemble._require_path("/pkg/src/sub", "/pkg/src/sub/child.luau", True),
            "@self/child",
        )
        self.assertEqual(
            assemble._require_path("/pkg/src/sub", "/pkg/src/sibling.luau", True),
            "./sibling",
        )
        self.assertEqual(
            assemble._require_path("/pkg/src/sub", "/pkg/src/init.luau", True),
            "../src",
        )
        self.assertEqual(
            assemble._require_path("/pkg/src/a/b", "/pkg/src/x/y.luau", True),
            "../x/y",
        )

    def test_convert_text_multiline_methods_casts_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "scope_pkg@1.0.0" / "pkg"
            src_dir = pkg / "src"
            (src_dir / "util").mkdir(parents=True)
            (src_dir / "util" / "math.luau").write_text("return {}\n", encoding="utf-8")
            (src_dir / "types.luau").write_text("return {}\n", encoding="utf-8")
            (pkg.parent / "React.lua").write_text('return require("./react/src")\n', encoding="utf-8")

            mounts = {
                (): str(src_dir),
                assemble.PARENT_CONTAINER: str(pkg.parent),
            }
            code = "\n".join(
                [
                    "-- require(script.Parent. FakeComment)",
                    'local msg = "require(script.Parent.FakeString)"',
                    "local root = script.Parent",
                    "local util = root:WaitForChild('util')",
                    "local m = require(",
                    "    util:FindFirstChild(\"math\", true) :: any -- inline comment",
                    ")",
                    'local t = require((root["types"]))',
                    "local r = require(script.Parent.Parent.React)",
                    "",
                ]
            )
            rewritten, count = assemble._convert_text(
                code,
                str(src_dir),
                [],
                mounts,
                is_init=False,
            )
            self.assertEqual(count, 3)
            self.assertIn("-- require(script.Parent. FakeComment)", rewritten)
            self.assertIn('local msg = "require(script.Parent.FakeString)"', rewritten)
            self.assertIn("local root = nil -- [headless] was: script.Parent", rewritten)
            self.assertIn("local util = nil -- [headless] was: root:WaitForChild('util')", rewritten)
            self.assertIn('local m = require("./util/math")', rewritten)
            self.assertIn('local t = require("./types")', rewritten)
            self.assertIn('local r = require("../../React")', rewritten)

    def test_single_file_rojo_mount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "pkg"
            pkg.mkdir()
            (pkg / "helper.luau").write_text("return 1\n", encoding="utf-8")
            entry = pkg / "single.luau"
            entry.write_text("return require(script.Parent.helper)\n", encoding="utf-8")
            mounts = {(): str(pkg), ("Single",): str(entry)}
            count = assemble._convert_tree(mounts)
            self.assertEqual(count, 1)
            self.assertEqual(entry.read_text(encoding="utf-8"), 'return require("./helper")\n')

    def test_alias_key_validation_and_package_meta_fallback(self) -> None:
        self.assertTrue(assemble._valid_alias_key("fusion"))
        self.assertTrue(assemble._valid_alias_key("signals.Signals"))
        self.assertFalse(assemble._valid_alias_key("self"))
        self.assertFalse(assemble._valid_alias_key("../bad"))
        self.assertIsNone(assemble._valid_http_url("javascript:alert(1)"))
        self.assertEqual(
            assemble._valid_http_url("https://github.com/dphfox/Fusion"),
            "https://github.com/dphfox/Fusion",
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            packages = tmp_path / "Packages"
            index = packages / "_Index"
            fusion_dir = index / "elttob_fusion@0.3.0" / "fusion"
            fusion_dir.mkdir(parents=True)
            (fusion_dir / "wally.toml").write_text(
                '[package]\nname = "elttob/fusion"\nversion = "0.3.0"\n',
                encoding="utf-8",
            )
            (packages / "fusion.lua").write_text(
                'return require(script.Parent._Index["elttob_fusion@0.3.0"]["fusion"])\n',
                encoding="utf-8",
            )
            with mock.patch.object(assemble, "PACKAGES", packages), mock.patch.object(assemble, "INDEX", index):
                self.assertEqual(
                    assemble.package_meta("fusion"),
                    ("elttob/fusion", "0.3.0", "https://github.com/dphfox/Fusion"),
                )


class BenchmarkTests(unittest.TestCase):
    def test_rank_row_keeps_wrong_and_error_sticky(self) -> None:
        ok_fast = {"status": "ok", "seconds": 0.01}
        ok_slow = {"status": "ok", "seconds": 0.05}
        wrong = {"status": "wrong", "detail": "mismatch"}
        err = {"status": "error", "detail": "boom"}
        dnf = {"status": "timeout", "detail": "cap"}

        self.assertLess(benchmark.rank_row(wrong), benchmark.rank_row(ok_fast))
        self.assertLess(benchmark.rank_row(err), benchmark.rank_row(ok_fast))
        self.assertLess(benchmark.rank_row(ok_fast), benchmark.rank_row(ok_slow))
        self.assertLess(benchmark.rank_row(ok_slow), benchmark.rank_row(dnf))

    def test_pass_order_rotates_and_classify_check_tiers(self) -> None:
        names = ["charm", "flux", "fusion", "signals", "vide"]
        self.assertEqual(benchmark.pass_order(names, 1), names)
        self.assertEqual(benchmark.pass_order(names, 2), ["flux", "fusion", "signals", "vide", "charm"])
        self.assertEqual(benchmark.pass_order(names, 6), names)

        self.assertEqual(benchmark.classify_check("ok", False), "ok")
        self.assertEqual(benchmark.classify_check("timeout", False), "fail")
        self.assertEqual(benchmark.classify_check("overflow", False), "fail")
        self.assertEqual(benchmark.classify_check("timeout", True), "dnf")
        self.assertEqual(benchmark.classify_check("oom", True), "dnf")
        self.assertEqual(benchmark.classify_check("wrong", True), "fail")
        self.assertEqual(benchmark.classify_check("error", True), "fail")

    def test_parse_args_and_filtered_report_skips_writes(self) -> None:
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            benchmark.parse_args(["--passes", "0"])

        args = benchmark.parse_args(["--filter", "flux", "vide", "--write"])
        self.assertEqual(args.filter, ["flux", "vide"])
        self.assertTrue(args.write)

        benches = [{"key": "kairo/deep", "evals": True, "cap": 8}]
        fake_best = {("flux", "kairo/deep"): {"status": "ok", "seconds": 0.01}}
        fake_evals = {("flux", "kairo/deep"): 100.0}
        fake_mem = {
            "flux": {
                "N": 20000,
                "liveKB": 3600.0,
                "bytesPerNode": 61.0,
                "retainedKB": 64.0,
                "steadyKB": 0.0,
            }
        }
        with (
            mock.patch.object(benchmark, "measure", return_value=fake_best),
            mock.patch.object(benchmark, "_collect_evals", return_value=fake_evals),
            mock.patch.object(benchmark, "_collect_memory", return_value=fake_mem),
            mock.patch.object(assemble, "write_json") as write_json_mock,
            mock.patch.object(chart, "render_from_data") as render_mock,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            benchmark.report("-O2", ["flux"], benches, passes=1, write_outputs=False)
            write_json_mock.assert_not_called()
            render_mock.assert_not_called()

    def test_classify_stderr_recognizes_oom_and_overflows(self) -> None:
        self.assertEqual(benchmark._classify_stderr("luau: not enough memory\n", 1)[0], "oom")
        self.assertEqual(benchmark._classify_stderr("foo.luau:12: stack overflow\n", 1)[0], "overflow")
        self.assertEqual(benchmark._classify_stderr("foo.luau:12: table overflow\n", 1)[0], "table-overflow")
        status, detail = benchmark._classify_stderr("", -9)
        self.assertEqual(status, "error")
        self.assertIn("SIGKILL", detail)

    def test_print_summary_orders_memory_by_geomean_rank(self) -> None:
        rows = [
            {"framework": "zeta", "suite": "kairo", "test": "deep", "status": "ok", "seconds": 0.001, "cap": 0.16},
            {"framework": "alpha", "suite": "kairo", "test": "deep", "status": "ok", "seconds": 0.004, "cap": 0.16},
        ]
        memory = {
            "alpha": {"N": 20000, "liveKB": 2000.0, "bytesPerNode": 34.0, "retainedKB": 10.0, "steadyKB": 0.0},
            "zeta": {"N": 20000, "liveKB": 3000.0, "bytesPerNode": 51.0, "retainedKB": 20.0, "steadyKB": 0.0},
        }
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            benchmark._print_summary(rows, memory, passes=1)
        out = buf.getvalue()
        mem_section = out.split("memory", 1)[1]
        self.assertLess(mem_section.index("zeta"), mem_section.index("alpha"))


class ChartTests(unittest.TestCase):
    def test_geomeans_charges_every_dnf_and_handles_degenerate_rows(self) -> None:
        rows = [
            {"framework": "fast", "suite": "kairo", "test": "deep", "status": "ok", "seconds": 1.0, "cap": 8.0},
            {"framework": "errfw", "suite": "kairo", "test": "deep", "status": "error", "seconds": None, "cap": 8.0},
            {"framework": "oomfw", "suite": "kairo", "test": "deep", "status": "oom", "seconds": None, "cap": 8.0},
            {"framework": "fast", "suite": "cellx", "test": "cellx2500", "status": "timeout", "seconds": None, "cap": 8.0},
            {"framework": "errfw", "suite": "cellx", "test": "cellx2500", "status": "error", "seconds": None, "cap": 8.0},
            {"framework": "oomfw", "suite": "cellx", "test": "cellx2500", "status": "oom", "seconds": None, "cap": 8.0},
        ]
        order, data = chart.group_rows(rows)
        geo, counted = chart.geomeans(order, data)
        self.assertEqual(counted, 1)
        self.assertAlmostEqual(geo["fast"] or 0.0, 1.0)
        self.assertAlmostEqual(geo["errfw"] or 0.0, 8.0)
        self.assertAlmostEqual(geo["oomfw"] or 0.0, 8.0)

        zero_rows = [
            {"framework": "a", "suite": "s", "test": "t", "status": "ok", "seconds": 0.0, "cap": 8.0},
            {"framework": "b", "suite": "s", "test": "t", "status": "ok", "seconds": 0.001, "cap": 8.0},
        ]
        z_order, z_data = chart.group_rows(zero_rows)
        z_geo, z_counted = chart.geomeans(z_order, z_data)
        self.assertEqual(z_counted, 1)
        self.assertIsNotNone(z_geo["a"])
        self.assertIsNotNone(z_geo["b"])

    def test_rainbow_contrast_against_white_meets_wcag_aa(self) -> None:
        for n in range(1, 11):
            for color in chart.rainbow(n):
                ratio = _contrast_against_white(color)
                self.assertGreaterEqual(
                    ratio,
                    4.5,
                    msg=f"color {color} (n={n}) has contrast {ratio:.2f} < 4.5 against #FFFFFF",
                )

    def test_svg_validity_grid_bounds_dark_dnf_and_gha_header(self) -> None:
        rows = [
            {
                "framework": "flux",
                "suite": "kairo",
                "test": "deep",
                "status": "ok",
                "seconds": 0.002,
                "evals": 2500.0,
                "cap": 8.0,
            },
            {
                "framework": "fusion",
                "suite": "kairo",
                "test": "deep",
                "status": "oom",
                "seconds": None,
                "evals": None,
                "cap": 8.0,
            },
        ]
        mem = {
            "flux": {"N": 20000, "liveKB": 3600.0, "bytesPerNode": 61.0, "retainedKB": 80.0, "steadyKB": 0.0},
            "fusion": {"N": 20000, "liveKB": 14000.0, "bytesPerNode": 239.0, "retainedKB": 180.0, "steadyKB": 1.0},
        }

        for gha in (False, True):
            full_svg, small_svg, _ = chart.render_svg_pair(rows, mem, flag="-O2", in_gha=gha, luau_version="0.746")
            full_root = ET.fromstring(full_svg)
            small_root = ET.fromstring(small_svg)
            self.assertTrue(full_root.tag.endswith("svg"))
            self.assertTrue(small_root.tag.endswith("svg"))
            self.assertIn("normalized per row", full_svg)
            self.assertIn("labeled", full_svg)
            self.assertIn("relative time (lower is faster)", full_svg)
            self.assertNotIn("lower is faster →", full_svg)
            self.assertIn("out of memory", full_svg)
            self.assertIn("@media (prefers-color-scheme:dark)", full_svg)
            self.assertIn(".dnfbg{fill:#2b1517}", full_svg)
            if gha:
                self.assertIn(".bg{fill:none}", full_svg)
                self.assertNotIn('fill="transparent"', full_svg)
                self.assertIn('y="24" font-size="23"', full_svg)

            # Verify gridlines terminate above `build retained` / `steady retained`.
            ns = {"svg": "http://www.w3.org/2000/svg"}
            grid_y2s = [
                float(line.attrib["y2"])
                for line in full_root.findall(".//svg:line", ns)
                if line.attrib.get("class") in ("grid", "gridkey")
            ]
            build_retained_ys = [
                float(text.attrib["y"])
                for text in full_root.findall(".//svg:text", ns)
                if "".join(text.itertext()) == "build retained"
            ]
            self.assertTrue(grid_y2s)
            self.assertEqual(len(build_retained_ys), 1)
            self.assertLess(max(grid_y2s), build_retained_ys[0])

    def test_memory_retention_bars_scale_against_leanest_live_kb(self) -> None:
        rows = [
            {"framework": "a", "suite": "kairo", "test": "deep", "status": "ok", "seconds": 0.001, "cap": 8.0},
            {"framework": "b", "suite": "kairo", "test": "deep", "status": "ok", "seconds": 0.002, "cap": 8.0},
            {"framework": "c", "suite": "kairo", "test": "deep", "status": "ok", "seconds": 0.003, "cap": 8.0},
        ]
        mem = {
            "a": {"N": 20000, "liveKB": 24000.0, "bytesPerNode": 400.0, "retainedKB": 0.0, "steadyKB": 0.1},
            "b": {"N": 20000, "liveKB": 36000.0, "bytesPerNode": 600.0, "retainedKB": 4000.0, "steadyKB": 6000.0},
            "c": {"N": 20000, "liveKB": 48000.0, "bytesPerNode": 800.0, "retainedKB": 30000.0, "steadyKB": 0.0},
        }
        full_svg, _, _ = chart.render_svg_pair(rows, mem, flag="-O2", in_gha=False, luau_version="0.746")
        root = ET.fromstring(full_svg)

        def group_bars_and_labels(label: str) -> list[tuple[ET.Element, str]]:
            elems = list(root.iter())
            idx = next(i for i, el in enumerate(elems) if el.tag.endswith("text") and "".join(el.itertext()) == label)
            pairs: list[tuple[ET.Element, str]] = []
            # Each framework emits a bar <rect x="212"> immediately followed by its label <text>.
            for i in range(idx + 1, len(elems)):
                el = elems[i]
                if el.tag.endswith("rect") and el.attrib.get("x") == "212":
                    pairs.append((el, "".join(elems[i + 1].itertext())))
                    if len(pairs) == 3:
                        break
            return pairs

        build_pairs = group_bars_and_labels("build retained")
        self.assertEqual([rect.attrib["width"] for rect, _ in build_pairs], ["2.5", "120.7", "724.0"])
        self.assertIsNone(build_pairs[0][0].attrib.get("mask"))
        self.assertIsNone(build_pairs[1][0].attrib.get("mask"))
        self.assertEqual(build_pairs[2][0].attrib.get("mask"), "url(#capfade)")
        for _, text in build_pairs:
            self.assertNotIn("×", text)

        steady_pairs = group_bars_and_labels("steady retained")
        self.assertEqual([rect.attrib["width"] for rect, _ in steady_pairs], ["2.5", "181.0", "2.5"])
        for _, text in steady_pairs:
            self.assertNotIn("×", text)

        # Fallback when `liveKB` is omitted derives the ceiling from `bytesPerNode * 3 * N / 1024`.
        mem_no_live = {
            "a": {"N": 20000, "bytesPerNode": 409.6, "retainedKB": 0.0, "steadyKB": 0.0},
            "b": {"N": 20000, "bytesPerNode": 600.0, "retainedKB": 6000.0, "steadyKB": 12000.0},
            "c": {"N": 20000, "bytesPerNode": 800.0, "retainedKB": 0.0, "steadyKB": 0.0},
        }
        fallback_svg, _, _ = chart.render_svg_pair(rows, mem_no_live, flag="-O2", in_gha=False, luau_version="0.746")
        fb_root = ET.fromstring(fallback_svg)
        fb_elems = list(fb_root.iter())
        fb_idx = next(
            i for i, el in enumerate(fb_elems) if el.tag.endswith("text") and "".join(el.itertext()) == "build retained"
        )
        fb_widths = [
            el.attrib["width"]
            for el in fb_elems[fb_idx + 1 :]
            if el.tag.endswith("rect") and el.attrib.get("x") == "212"
        ][:3]
        self.assertEqual(fb_widths, ["2.5", "181.0", "2.5"])

    def test_kairo_only_chart_reports_per_run_deadline_not_per_iteration_cap(self) -> None:
        rows = [
            {"framework": "flux", "suite": "kairo", "test": "deep", "status": "ok", "seconds": 0.002, "cap": 0.16},
            {"framework": "fusion", "suite": "kairo", "test": "deep", "status": "oom", "seconds": None, "cap": 0.16},
        ]
        full_svg, small_svg, _ = chart.render_svg_pair(rows, {}, flag="-O2", in_gha=False, luau_version="0.746")
        self.assertIn("DNF charged the 8s per-run deadline", full_svg)
        self.assertIn("DNF charged the 8s per-run deadline", small_svg)
        self.assertNotIn("0.16s per-run deadline", full_svg)


if __name__ == "__main__":
    unittest.main()
