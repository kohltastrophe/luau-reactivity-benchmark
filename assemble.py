"""Makes the wally packages requirable by the headless luau CLI, in place.

`wally install`, then each package's Roblox instance-requires are rewritten to string requires through its
rojo tree, the dependency shims under Packages/_Index are re-pointed with string requires, and a generated
root .luaurc aliases each package as @<name> (plus @<name>.<Child> for each module its rojo tree mounts
beside the root). Re-runs whenever wally.toml or this file changes.
"""

import glob
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGES = os.path.join(ROOT, "Packages")
INDEX = os.path.join(PACKAGES, "_Index")
LUAURC = os.path.join(ROOT, ".luaurc")
STAMP = os.path.join(PACKAGES, ".headless")  # wally install wipes it

ALIAS_RE = re.compile(r"^(\s*)local\s+(\w+)\s*=\s*(script(?:\.\w+)*)\s*$")
REQUIRE_RE = re.compile(r"require\(\s*([A-Za-z_]\w*(?:\.\w+)*)\s*\)")
# a wally shim: require(<instance path>["scope_name@version"]["name"])
SHIM_RE = re.compile(r'\["([a-z0-9-]+)_([a-z0-9-]+)@([^"]+)"\]\["([^"]+)"\]')


def _rel(path, start):
    """A relative require path, with the "./" the luau CLI needs."""
    rel = os.path.relpath(path, start).replace(os.sep, "/")
    return rel if rel.startswith(".") else "./" + rel


def _resolve(inst_dir, is_init, tokens):
    """The instance path `tokens` (Parent / child names) reach from a module in instance folder inst_dir."""
    cur = list(inst_dir)
    at_file = not is_init
    for tok in tokens:
        if tok != "Parent":
            cur.append(tok)
        elif at_file:
            pass
        elif cur:
            cur.pop()
        else:
            raise ValueError("navigated above tree root")
        at_file = False
    return cur


def _fs_path(mounts, inst):
    """Where instance path `inst` lives on disk: under the deepest rojo mount containing it."""
    n = max(n for n in range(len(inst) + 1) if tuple(inst[:n]) in mounts)
    return os.path.join(mounts[tuple(inst[:n])], *inst[n:])


def _require_path(file_dir, target, is_init):
    """The string require from a module in file_dir to the module at `target`."""
    rel = os.path.relpath(target, file_dir).replace(os.sep, "/")
    if is_init:
        # from init.luau the folder is the module: "./" is the folder's parent and "@self/" the folder
        if rel == ".":
            return "@self"
        if rel.startswith("../"):
            rel = rel[3:]
            return rel if rel.startswith("../") else "./" + rel
        return "@self/" + rel
    if rel.split("/")[-1] in (".", ".."):
        # an ancestor folder: name it from its parent (require(script.Parent) from src/util.luau -> "../src")
        return _rel(os.path.dirname(target), file_dir) + "/" + os.path.basename(target)
    return _rel(target, file_dir)


def _convert_file(path, inst_dir, mounts):
    """Rewrites one module's instance-requires in place; returns how many."""
    is_init = os.path.basename(path) in ("init.luau", "init.lua")
    file_dir = os.path.dirname(path)
    aliases = {}
    count = 0

    def replace(m):
        nonlocal count
        base, *rest = m.group(1).split(".")
        if base == "script":
            toks = rest
        elif base in aliases:
            toks = aliases[base] + rest
        else:
            return m.group(0)
        count += 1
        target = _fs_path(mounts, _resolve(inst_dir, is_init, toks))
        return f'require("{_require_path(file_dir, target, is_init)}")'

    with open(path) as f:
        lines = f.readlines()
    out = []
    for ln in lines:
        m = ALIAS_RE.match(ln.rstrip("\n"))
        if m:
            aliases[m.group(2)] = m.group(3).split(".")[1:]
            out.append(f"{m.group(1)}local {m.group(2)} = nil -- [headless] was: {m.group(3)}\n")
        else:
            out.append(REQUIRE_RE.sub(replace, ln))
    if out != lines:
        with open(path, "w") as f:
            f.writelines(out)
    return count


def _convert_tree(mounts):
    """Rewrites every instance-require under a package's rojo mounts ({instance path: dir}, root at ())."""
    count = 0
    dirs = set(mounts.values())
    for inst, base in mounts.items():
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if os.path.join(dirpath, d) not in dirs]  # converted as their own mount
            rel = os.path.relpath(dirpath, base)
            inst_dir = list(inst) + ([] if rel == "." else rel.split(os.sep))
            for fn in filenames:
                if fn.endswith((".luau", ".lua")):
                    path = os.path.join(dirpath, fn)
                    try:
                        count += _convert_file(path, inst_dir, mounts)
                    except ValueError as e:
                        print(f"  skip {os.path.relpath(path, base)}: {e}")
    return count


def _mounts(root):
    """{instance path: source dir} of a package's rojo tree; () is the root module."""
    proj = os.path.join(root, "default.project.json")
    if not os.path.isfile(proj):
        return {(): root}
    with open(proj) as f:
        tree = json.load(f).get("tree", {})
    mounts = {}

    def walk(node, inst):
        if "$path" in node:
            mounts[inst] = os.path.normpath(os.path.join(root, node["$path"]))
        for k, child in node.items():
            if not k.startswith("$") and isinstance(child, dict):
                walk(child, inst + (k,))

    walk(tree, ())
    mounts.setdefault((), root)
    return mounts


def _shim(path):
    """(scope, name, version, package root) a pristine wally shim points at, else None."""
    try:
        with open(path) as f:
            m = SHIM_RE.search(f.read())
    except OSError:
        return None
    if not m:
        return None
    scope, name, version, inner = m.groups()
    return scope, name, version, os.path.join(INDEX, f"{scope}_{name}@{version}", inner)


def package_meta(name):
    """(scope/name, resolved version, repository) of the wally dependency `name`, else None."""
    shim = _shim(os.path.join(PACKAGES, name + ".lua"))
    if not shim:
        return None
    scope, pkg, version, root = shim
    try:
        with open(os.path.join(root, "wally.toml")) as f:
            m = re.search(r'^\s*repository\s*=\s*"([^"]+)"', f.read(), re.M)
    except OSError:
        m = None
    return f"{scope}/{pkg}", version, m.group(1) if m else f"https://github.com/{scope}/{pkg}"


def _stamp():
    with open(os.path.join(ROOT, "wally.toml"), "rb") as f, open(__file__, "rb") as g:
        return hashlib.sha256(f.read() + g.read()).hexdigest()


def assemble():
    print("# wally install", flush=True)
    subprocess.run(["wally", "install"], cwd=ROOT, check=True)
    aliases = {}
    converted = set()
    for shim in sorted(glob.glob(os.path.join(PACKAGES, "*.lua"))):
        target = _shim(shim)
        if not target:
            continue
        name = os.path.splitext(os.path.basename(shim))[0]
        mounts = _mounts(target[3])
        print(f"  @{name}: {_convert_tree(mounts)} instance-require(s) rewritten")
        converted.add(mounts[()])
        for inst, src in mounts.items():
            aliases[".".join((name, *inst))] = _rel(src, ROOT)
    if not aliases:
        sys.exit("error: wally install wrote no Packages/*.lua shims (empty [dependencies]?)")
    # the dependency shims packages require each other through
    for shim in glob.glob(os.path.join(INDEX, "*", "*.lua")):
        target = _shim(shim)
        if target:
            mounts = _mounts(target[3])
            if mounts[()] not in converted:  # a transitive dependency
                _convert_tree(mounts)
                converted.add(mounts[()])
            with open(shim, "w") as f:
                f.write(f'return require("{_rel(mounts[()], os.path.dirname(shim))}")\n')
    with open(LUAURC, "w") as f:
        json.dump({"aliases": aliases}, f, indent=2)
    with open(STAMP, "w") as f:
        f.write(_stamp())


def ensure():
    try:
        with open(STAMP) as f:
            current = f.read() == _stamp() and os.path.isfile(LUAURC)
    except OSError:
        current = False
    if not current:
        print("packages not assembled (or out of date), assembling", file=sys.stderr)
        assemble()


if __name__ == "__main__":
    assemble()
