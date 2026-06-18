"""Assembly for the Vide core. Vide already ships luau string requires, so the core src is
copied as-is (the rewrite is a no-op). Assembles into lib/vide/; the adapter folds the headless
entry (force strict off, re-export the reactive primitives) into its init.luau."""

import os


def setup(ctx):
    src = os.path.join(ctx.pkg_dir("centau_vide@"), "vide", "src")
    dest = os.path.join(ctx.LIB, "vide")
    ctx.fresh(dest)
    ctx.copy_tree(src, dest)
    _, reqs = ctx.convert_tree(dest)
    print(f"  copied src -> lib/vide ({reqs} instance-require(s) rewritten)")
