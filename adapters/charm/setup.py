"""Assembly for the Charm core. Charm ships plain Luau but uses Roblox instance requires
(`require(script.atom)`, `require(script.Parent.store)`), so the whole src is copied and those
requires are rewritten to headless string paths by convert_tree. Assembles into lib/charm/; the
adapter folds the headless entry (re-export the primitives + store.batch) into its init.luau."""

import os


def setup(ctx):
    src = os.path.join(ctx.pkg_dir("littensy_charm@"), "charm", "src")
    dest = os.path.join(ctx.LIB, "charm")
    ctx.fresh(dest)
    ctx.copy_tree(src, dest)
    _, reqs = ctx.convert_tree(dest)
    print(f"  copied src -> lib/charm ({reqs} instance-require(s) rewritten)")
