"""Assembly for the Flux core. Flux already ships luau string requires, so the core src is
copied as-is (the rewrite is a no-op). Assembles into lib/flux/; the adapter folds the headless
entry (force strict off, re-export the reactive primitives) into its init.luau."""

import os


def setup(ctx):
    src = os.path.join(ctx.pkg_dir("kohltastrophe_flux@"), "flux", "src")
    dest = os.path.join(ctx.LIB, "flux")
    ctx.fresh(dest)
    ctx.copy_tree(src, dest)
    _, reqs = ctx.convert_tree(dest)
    print(f"  copied src -> lib/flux ({reqs} instance-require(s) rewritten)")
