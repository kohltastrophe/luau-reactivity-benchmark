"""Assemble Wally packages in place for the headless Luau CLI.

Runs `wally install`, rewrites Roblox instance `require(...)` calls to Luau string requires across each
package's Rojo tree, re-points cross-package shims under `Packages/_Index`, and generates `.luaurc`
aliases (`@<name>` and `@<name>.<Child>`). Re-runs automatically when `wally.toml`, `wally.lock`, or this
script changes.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Any

ROOT = Path(__file__).resolve().parent
PACKAGES = ROOT / "Packages"
INDEX = PACKAGES / "_Index"
LUAURC = ROOT / ".luaurc"
STAMP = PACKAGES / ".headless"  # wally install wipes Packages/

# Known upstream repositories for Wally packages whose published wally.toml omits `repository`.
REPO_FALLBACKS: dict[str, str] = {
    "elttob/fusion": "https://github.com/dphfox/Fusion",
}

ALIAS_DECL_RE = re.compile(
    r"^([ \t]*)local[ \t]+([A-Za-z_]\w*)(?:[ \t]*:[ \t]*[A-Za-z_][\w.<>, \t?|]*)?[ \t]*=[ \t]*(.+?)[ \t]*;?$",
    re.M,
)
REQUIRE_WORD_RE = re.compile(r"\brequire[ \t]*\(")
CAST_TAIL_RE = re.compile(r"\s*::\s*[A-Za-z_][\w.]*(?:<[^()]*>)?\s*$")
ROOT_IDENT_RE = re.compile(r"^\s*([A-Za-z_]\w*)(.*)$", re.S)
DOT_SEG_RE = re.compile(r"^\s*\.\s*([A-Za-z_]\w*)(.*)$", re.S)
BRACKET_SEG_RE = re.compile(r"""^\s*\[\s*(?:"([^"\\\n]+)"|'([^'\\\n]+)')\s*\](.*)$""", re.S)
CHILD_CALL_RE = re.compile(
    r"""^\s*:\s*(?:WaitForChild|FindFirstChild)\s*\(\s*(?:"([^"\\\n]+)"|'([^'\\\n]+)')(?:\s*,[^)]*)?\)(.*)$""",
    re.S,
)
ALIAS_KEY_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Pristine Wally shim: require(<instance path>["scope_name@version"]["name"])
SHIM_RE = re.compile(r'\["([a-z0-9-]+)_([a-z0-9-]+)@([^"]+)"\]\["([^"]+)"\]')

PARENT_CONTAINER = ("^",)


def write_json(path: Path | str, data: Any) -> None:
    """Write UTF-8 JSON with a trailing newline."""
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _rel(path: str | os.PathLike[str], start: str | os.PathLike[str]) -> str:
    """Format a relative Luau require path with a leading `./` or `../`."""
    rel = os.path.relpath(path, start).replace(os.sep, "/")
    return rel if rel.startswith(".") else "./" + rel


def _strip_module_ext(path: str) -> str:
    for ext in (".luau", ".lua"):
        if path.endswith(ext):
            stem = path[: -len(ext)]
            return os.path.dirname(stem) if os.path.basename(stem) == "init" else stem
    return path


def _long_bracket_end(src: str, pos: int) -> int | None:
    """If `src[pos:]` opens `[=*[`, return the index right after matching `]=*]`, else `None`."""
    if pos >= len(src) or src[pos] != "[":
        return None
    i = pos + 1
    while i < len(src) and src[i] == "=":
        i += 1
    if i >= len(src) or src[i] != "[":
        return None
    closing = "]" + ("=" * (i - pos - 1)) + "]"
    end = src.find(closing, i + 1)
    return len(src) if end == -1 else end + len(closing)


def _blank_slice(src: str, start: int, end: int) -> str:
    return "".join("\n" if ch == "\n" else " " for ch in src[start:end])


def _scan_luau(src: str, *, mask_strings: bool) -> str:
    """Blank Luau comments (and optionally string interiors) while keeping byte offsets and newlines."""
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == "-" and i + 1 < n and src[i + 1] == "-":
            long_end = _long_bracket_end(src, i + 2)
            if long_end is not None:
                out.append(_blank_slice(src, i, long_end))
                i = long_end
                continue
            nl = src.find("\n", i + 2)
            end = n if nl == -1 else nl
            out.append(" " * (end - i))
            i = end
            continue
        if ch == "[":
            long_end = _long_bracket_end(src, i)
            if long_end is not None:
                if mask_strings:
                    out.append('"' + _blank_slice(src, i + 1, long_end - 1) + '"')
                else:
                    out.append(src[i:long_end])
                i = long_end
                continue
        if ch in ('"', "'", "`"):
            quote = ch
            j = i + 1
            while j < n and src[j] != quote:
                if src[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if src[j] == "\n" and quote != "`":
                    break
                j += 1
            end = min(n, j + 1) if j < n and src[j] == quote else j
            if mask_strings:
                inner = _blank_slice(src, i + 1, max(i + 1, end - 1))
                out.append('"' + inner + ('"' if end > i + 1 else ""))
            else:
                out.append(src[i:end])
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _mask_luau(src: str) -> str:
    """Blank comments and string interiors with spaces while keeping exact byte offsets and newlines."""
    return _scan_luau(src, mask_strings=True)


def _parse_chain(raw_expr: str, aliases: dict[str, list[str]]) -> list[str] | None:
    """Parse an instance chain rooted at `script` or a known local alias into path tokens."""
    expr = _strip_comments_only(raw_expr).strip()
    while expr.startswith("(") and expr.endswith(")"):
        inner = expr[1:-1].strip()
        if _balanced_parens(inner):
            expr = inner
        else:
            break
    expr = CAST_TAIL_RE.sub("", expr).strip()

    m = ROOT_IDENT_RE.match(expr)
    if not m:
        return None
    root_ident, rest = m.group(1), m.group(2)
    if root_ident == "script":
        tokens: list[str] = []
    elif root_ident in aliases:
        tokens = list(aliases[root_ident])
    else:
        return None

    while rest.strip():
        dm = DOT_SEG_RE.match(rest)
        if dm:
            tokens.append(dm.group(1))
            rest = dm.group(2)
            continue
        bm = BRACKET_SEG_RE.match(rest)
        if bm:
            tokens.append(bm.group(1) or bm.group(2))
            rest = bm.group(3)
            continue
        cm = CHILD_CALL_RE.match(rest)
        if cm:
            tokens.append(cm.group(1) or cm.group(2))
            rest = cm.group(3)
            continue
        return None
    return tokens


def _balanced_parens(s: str) -> bool:
    depth = 0
    for ch in _mask_luau(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _strip_comments_only(src: str) -> str:
    """Blank Luau line and block comments while preserving string literals intact."""
    return _scan_luau(src, mask_strings=False)


def _resolve(
    inst_dir: list[str] | tuple[str, ...],
    is_init: bool,
    tokens: list[str],
    allow_container: bool = False,
) -> list[str]:
    """Resolve `tokens` (`Parent` / child names) from a module in `inst_dir`."""
    cur = list(inst_dir)
    at_file = not is_init
    for tok in tokens:
        if tok != "Parent":
            cur.append(tok)
        elif at_file:
            pass
        elif cur == list(PARENT_CONTAINER):
            raise ValueError("navigated above package container")
        elif cur:
            cur.pop()
        elif allow_container:
            cur = list(PARENT_CONTAINER)
        else:
            raise ValueError("navigated above tree root")
        at_file = False
    return cur


def _module_exists(path: str) -> bool:
    stem = _strip_module_ext(path)
    return any(
        os.path.isfile(candidate)
        for candidate in (
            path,
            stem + ".luau",
            stem + ".lua",
            os.path.join(stem, "init.luau"),
            os.path.join(stem, "init.lua"),
        )
    )


def _fs_path(mounts: dict[tuple[str, ...], str], inst: list[str]) -> str:
    """Resolve instance path `inst` to its filesystem path under the deepest matching Rojo mount."""
    if inst == list(PARENT_CONTAINER):
        raise ValueError("package container is not a module")
    matching = [n for n in range(len(inst) + 1) if tuple(inst[:n]) in mounts]
    if not matching:
        raise ValueError(f"unmounted instance path: {'.'.join(inst)}")
    n = max(matching)
    base = mounts[tuple(inst[:n])]
    target = os.path.join(base, *inst[n:]) if n < len(inst) else base
    if tuple(inst[:1]) == PARENT_CONTAINER and not _module_exists(target):
        raise ValueError(f"missing sibling shim: {os.path.basename(target)}")
    return target


def _require_path(file_dir: str, target: str, is_init: bool) -> str:
    """Build the Luau string-require path from `file_dir` to `target`."""
    target = _strip_module_ext(target)
    rel = os.path.relpath(target, file_dir).replace(os.sep, "/")
    if is_init:
        if rel == ".":
            return "@self"
        if rel == ".." or rel.startswith("../"):
            return _require_path(os.path.dirname(file_dir), target, False)
        return f"@self/{rel}"
    if rel.split("/")[-1] in (".", ".."):
        return f"{_rel(os.path.dirname(target), file_dir)}/{os.path.basename(target)}"
    return _rel(target, file_dir)


def _find_requires(masked: str) -> list[tuple[int, int, int, int]]:
    """Return `(call_start, call_end, arg_start, arg_end)` spans for every `require(...)` call."""
    spans: list[tuple[int, int, int, int]] = []
    pos = 0
    n = len(masked)
    while pos < n:
        m = REQUIRE_WORD_RE.search(masked, pos)
        if not m:
            break
        open_paren = m.end() - 1
        depth = 1
        j = open_paren + 1
        while j < n and depth > 0:
            if masked[j] == "(":
                depth += 1
            elif masked[j] == ")":
                depth -= 1
            j += 1
        if depth == 0:
            spans.append((m.start(), j, open_paren + 1, j - 1))
        pos = m.end()
    return spans


def _convert_text(
    src: str,
    file_dir: str,
    inst_dir: list[str] | tuple[str, ...],
    mounts: dict[tuple[str, ...], str],
    is_init: bool,
) -> tuple[str, int]:
    """Rewrite instance `require(...)` calls in `src`; return `(new_src, rewrite_count)`."""
    masked = _mask_luau(src)
    allow_container = PARENT_CONTAINER in mounts
    aliases: dict[str, list[str]] = {}
    alias_pos: dict[str, int] = {}
    replacements: list[tuple[int, int, str]] = []

    for m in ALIAS_DECL_RE.finditer(masked):
        indent, name, _ = m.group(1), m.group(2), m.group(3)
        rhs_start, rhs_end = m.start(3), m.end(3)
        raw_rhs = src[rhs_start:rhs_end].rstrip("; \t")
        toks = _parse_chain(raw_rhs, aliases)
        if toks is not None:
            aliases[name] = toks
            alias_pos[name] = m.end()
            one_line_rhs = " ".join(raw_rhs.split())
            replacements.append(
                (m.start(), m.end(), f"{indent}local {name} = nil -- [headless] was: {one_line_rhs}")
            )

    count = 0
    alias_ranges = [(s, e) for s, e, _ in replacements]
    for call_start, call_end, arg_start, arg_end in _find_requires(masked):
        if any(s <= call_start < e for s, e in alias_ranges):
            continue
        raw_arg = src[arg_start:arg_end]
        active_aliases = {k: v for k, v in aliases.items() if alias_pos[k] <= call_start}
        toks = _parse_chain(raw_arg, active_aliases)
        if toks is None:
            continue
        try:
            resolved = _resolve(inst_dir, is_init, toks, allow_container=allow_container)
            target = _fs_path(mounts, resolved)
            req = _require_path(file_dir, target, is_init)
        except ValueError:
            continue
        count += 1
        replacements.append((call_start, call_end, f'require("{req}")'))

    if not replacements:
        return src, 0

    replacements.sort(key=lambda item: item[0])
    pieces: list[str] = []
    cursor = 0
    for start, end, rep in replacements:
        if start < cursor:
            continue
        pieces.append(src[cursor:start])
        pieces.append(rep)
        cursor = end
    pieces.append(src[cursor:])
    return "".join(pieces), count


def _convert_file(
    path: str,
    inst_dir: list[str] | tuple[str, ...],
    mounts: dict[tuple[str, ...], str],
    force_non_init: bool = False,
) -> int:
    """Rewrite one module's instance requires in place and return the rewrite count."""
    is_init = (not force_non_init) and os.path.basename(path) in ("init.luau", "init.lua")
    file_dir = os.path.dirname(path)
    src = Path(path).read_text(encoding="utf-8")
    updated, count = _convert_text(src, file_dir, inst_dir, mounts, is_init)
    if updated != src:
        Path(path).write_text(updated, encoding="utf-8")
    return count


def _convert_tree(mounts: dict[tuple[str, ...], str]) -> int:
    """Rewrite every instance require across a package's Rojo mounts."""
    count = 0
    mount_paths = {os.path.normpath(p) for k, p in mounts.items() if k != PARENT_CONTAINER}
    for inst, base in mounts.items():
        if inst == PARENT_CONTAINER:
            continue
        if os.path.isfile(base):
            if base.endswith((".luau", ".lua")):
                count += _convert_file(base, list(inst), mounts, force_non_init=True)
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d for d in dirnames if os.path.normpath(os.path.join(dirpath, d)) not in mount_paths
            ]
            rel = os.path.relpath(dirpath, base)
            inst_dir = list(inst) + ([] if rel == "." else rel.split(os.sep))
            for fn in sorted(filenames):
                if fn.endswith((".luau", ".lua")):
                    path = os.path.join(dirpath, fn)
                    count += _convert_file(path, inst_dir, mounts)
    return count


def _mounts(pkg_root: str) -> dict[tuple[str, ...], str]:
    """Extract `{instance_path: fs_path}` from `default.project.json`, with `()` at the package root."""
    proj = os.path.join(pkg_root, "default.project.json")
    mounts: dict[tuple[str, ...], str] = {}
    if os.path.isfile(proj):
        tree = json.loads(Path(proj).read_text(encoding="utf-8")).get("tree", {})

        def walk(node: dict[str, Any], inst: tuple[str, ...]) -> None:
            if "$path" in node:
                mounts[inst] = os.path.normpath(os.path.join(pkg_root, node["$path"]))
            for k, child in node.items():
                if not k.startswith("$") and isinstance(child, dict):
                    walk(child, inst + (k,))

        walk(tree, ())
    mounts.setdefault((), os.path.normpath(pkg_root))
    container = os.path.dirname(os.path.normpath(pkg_root))
    if os.path.normpath(container) != os.path.normpath(str(INDEX)):
        mounts[PARENT_CONTAINER] = container
    return mounts


def _shim(path: str | Path) -> tuple[str, str, str, str] | None:
    """Return `(scope, name, version, package_root)` for a pristine Wally shim, else `None`."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    m = SHIM_RE.search(text)
    if not m:
        return None
    scope, name, version, inner = m.groups()
    return scope, name, version, str(INDEX / f"{scope}_{name}@{version}" / inner)


def _valid_http_url(url: object) -> str | None:
    if isinstance(url, str) and url.startswith(("https://", "http://")) and not any(c.isspace() for c in url):
        return url
    return None


def package_meta(name: str) -> tuple[str, str, str] | None:
    """Return `(scope/name, version, repo_url)` for Wally dependency `name`, else `None`."""
    shim = _shim(PACKAGES / f"{name}.lua")
    if not shim:
        return None
    scope, pkg, version, pkg_root = shim
    ident = f"{scope}/{pkg}"
    repo: str | None = None
    wally_toml = Path(pkg_root) / "wally.toml"
    try:
        manifest = tomllib.loads(wally_toml.read_text(encoding="utf-8"))
        repo = _valid_http_url(manifest.get("package", {}).get("repository"))
    except (OSError, tomllib.TOMLDecodeError):
        repo = None
    if not repo:
        repo = REPO_FALLBACKS.get(ident, f"https://github.com/{ident}")
    return ident, version, repo


def _declared_dependencies() -> list[str]:
    manifest = tomllib.loads((ROOT / "wally.toml").read_text(encoding="utf-8"))
    deps = manifest.get("dependencies", {})
    return sorted(deps.keys()) if isinstance(deps, dict) else []


def _stamp() -> str:
    h = hashlib.sha256()
    for path in (ROOT / "wally.toml", ROOT / "wally.lock", Path(__file__).resolve()):
        if path.is_file():
            data = path.read_bytes()
            h.update(f"{path.name}:{len(data)}\n".encode("utf-8"))
            h.update(data)
    return h.hexdigest()


def _valid_alias_key(key: str) -> bool:
    return key != "self" and bool(ALIAS_KEY_RE.match(key))


def assemble() -> None:
    """Install Wally packages and rewrite their module trees for headless Luau."""
    print("# wally install", flush=True)
    subprocess.run(["wally", "install"], cwd=ROOT, check=True, timeout=120)
    aliases: dict[str, str] = {}
    converted: set[str] = set()
    for shim in sorted(glob.glob(str(PACKAGES / "*.lua"))):
        target = _shim(shim)
        if not target:
            continue
        name = os.path.splitext(os.path.basename(shim))[0]
        mounts = _mounts(target[3])
        print(f"  @{name}: {_convert_tree(mounts)} instance-require(s) rewritten")
        converted.add(mounts[()])
        for inst, src in mounts.items():
            if inst == PARENT_CONTAINER:
                continue
            key = ".".join((name, *inst))
            if _valid_alias_key(key):
                aliases[key] = _rel(_strip_module_ext(src), ROOT)
    if not aliases:
        sys.exit("error: wally install wrote no Packages/*.lua shims (empty [dependencies]?)")
    for shim in sorted(glob.glob(str(INDEX / "*" / "*.lua"))):
        target = _shim(shim)
        if target:
            mounts = _mounts(target[3])
            if mounts[()] not in converted:
                _convert_tree(mounts)
                converted.add(mounts[()])
            req = _rel(_strip_module_ext(mounts[()]), os.path.dirname(shim))
            Path(shim).write_text(f'return require("{req}")\n', encoding="utf-8")
    write_json(LUAURC, {"aliases": dict(sorted(aliases.items()))})
    STAMP.write_text(_stamp() + "\n", encoding="utf-8")


def ensure() -> None:
    """Run `assemble()` when `Packages/` or `.luaurc` is missing or out of date."""
    try:
        expected_shims = all((PACKAGES / f"{dep}.lua").is_file() for dep in _declared_dependencies())
        current = (
            STAMP.read_text(encoding="utf-8").strip() == _stamp()
            and LUAURC.is_file()
            and INDEX.is_dir()
            and expected_shims
        )
    except (OSError, tomllib.TOMLDecodeError):
        current = False
    if not current:
        print("packages not assembled (or out of date), assembling", file=sys.stderr)
        assemble()


if __name__ == "__main__":
    assemble()
