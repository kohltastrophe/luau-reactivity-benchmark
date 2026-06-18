"""Assembly for the Fusion core. Copy the whole src minus the Roblox-only modules (which never
load headless) and rewrite the remaining `require(script.X)` calls to string paths. Assembles
into lib/fusion/; the adapter folds the headless entry (a synchronous External provider standing
in for Roblox's task scheduler, plus a relaxed safety timer) into its init.luau."""

import os

# Roblox-only modules the headless core never loads; excluded from the copy.
SKIP = {"Animation", "Colour", "Instances", "RobloxExternal.luau", "init.luau"}


def setup(ctx):
    src = os.path.join(ctx.pkg_dir("elttob_fusion@"), "fusion", "src")
    dest = os.path.join(ctx.LIB, "fusion")
    ctx.fresh(dest)
    ctx.copy_tree(src, dest, skip=SKIP)
    _, reqs = ctx.convert_tree(dest)
    print(
        f"  copied src (minus Roblox modules) -> lib/fusion "
        f"({reqs} instance-require(s) rewritten)"
    )
