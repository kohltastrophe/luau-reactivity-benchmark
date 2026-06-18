"""Assembly for the signals core. Its wally artifact is a rojo-assembled monorepo that doesn't
load headless, so the core is assembled directly from the package's modules/ tree via an
explicit file map + require rewrites. Assembles into lib/signals/; the adapter folds the headless
entry (re-export the create* primitives + the scheduler's batch) into its init.luau."""

import os
import re
import sys

# Each entry: (source file under the package, dest filename, [(pattern, replacement), ...]).
# Rewrites turn the monorepo's unresolved cross-module requires into flat string requires.
FILES = [
    (
        "modules/signals/src/Signals.lua",
        "Signals.luau",
        [
            (
                r"require\(\s*Packages\.SignalsScheduler\s*\)",
                'require("./SignalsScheduler")',
            ),
            (
                r"require\(\s*script\.Parent\.callUserSpace\s*\)",
                'require("./callUserSpace")',
            ),
        ],
    ),
    ("modules/signals/src/callUserSpace.lua", "callUserSpace.luau", []),
    (
        "modules/signals-scheduler/src/SignalsScheduler.lua",
        "SignalsScheduler.luau",
        [(r"require\(\s*Packages\.SignalsFlags\s*\)", 'require("./SignalsFlags")')],
    ),
    ("modules/signals-flags/src/init.lua", "SignalsFlags.luau", []),
]


def setup(ctx):
    pkg = os.path.join(ctx.pkg_dir("roblox_signals@"), "signals")
    dest = os.path.join(ctx.LIB, "signals")
    ctx.fresh(dest)
    total = 0
    for rel, out, rewrites in FILES:
        src = os.path.join(pkg, rel)
        if not os.path.isfile(src):
            sys.exit(
                f"error: signals source missing: {rel} (did the package layout change?)"
            )
        with open(src) as f:
            text = f.read()
        for pat, repl in rewrites:
            text, n = re.subn(pat, repl, text)
            if n == 0:
                sys.exit(
                    f"error: signals rewrite '{pat}' matched nothing in {rel} "
                    "(upstream layout changed; update FILES)"
                )
            total += n
        with open(os.path.join(dest, out), "w") as f:
            f.write(text)
    # Mop up orphaned `local Packages = ...` alias lines and any remaining script.* requires;
    # the rewrites above handle only the cross-module Packages.* aliases.
    _, reqs = ctx.convert_tree(dest)
    print(
        f"  assembled {len(FILES)} modules -> lib/signals "
        f"({total} cross-module + {reqs} script require(s) rewritten)"
    )
