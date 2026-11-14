"""Register smoke test for the AmbientCG Browser dev package (no .blend, no network).

Run headless with an isolated profile so your own preferences are never touched:
  $env:BLENDER_USER_RESOURCES="<scratch>\\profile"
  & blender --background --factory-startup --python-exit-code 1 --python tests\\smoke_register.py [-- <repo root>]

Imports `ambientcg_browser` from the repo root (default: one level above this file), registers it, checks every
class in each module's `_classes` and the `ambientcg_*` properties on Object / WindowManager / Scene, reloads the
package the way Reload Scripts does, registers again, then unregisters and checks everything is gone.
Prints `SMOKE PASSED` (exit 0) or `SMOKE FAILED` (exit 1).
"""
import importlib
import os
import sys
import traceback

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ROOT = os.path.abspath(argv[0] if argv else os.path.join(os.path.dirname(__file__), ".."))
# Pointers that must exist; every other ambientcg_* property is found and checked for removal anyway.
SETTINGS = {"Scene.ambientcg_browser"}
OPERATORS = ("search", "fetch", "apply", "cancel", "pick", "browse", "close", "cache_folder", "cache_reset", "check_updates")
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("  [%s]" % detail if detail else ""))
    if not cond:
        failures.append(name)


def registered(pkg):
    """Every class, as (module, class name, is_registered)."""
    return [(m.__name__.rsplit(".", 1)[-1], c.__name__, c.is_registered) for m in pkg._modules for c in m._classes]


def props():
    return {"%s.%s" % (t.__name__, p.identifier) for t in (bpy.types.Object, bpy.types.WindowManager, bpy.types.Scene)
            for p in t.bl_rna.properties if p.identifier.startswith("ambientcg_")}


def operators():
    return [name for name in OPERATORS if hasattr(bpy.types, "AMBIENTCG_OT_" + name)]


def check_blender_org_copy(update):
    """The update check runs for our own repository's copy and is off for one from extensions.blender.org."""
    package = update.__package__
    try:
        for module, expected in (("user_default", False), ("blender_org", True)):
            update.__package__ = "bl_ext.%s.ambientcg_browser" % module
            check("installed in %s: from_blender_org is %s" % (module, expected),
                  update.from_blender_org(bpy.context) == expected)
            check("installed in %s: Check for Updates %s" % (module, "off" if expected else "on"),
                  bpy.ops.ambientcg.check_updates.poll() != expected)
            check("installed in %s: Update panel %s" % (module, "hidden" if expected else "shown"),
                  bpy.types.AMBIENTCG_PT_update.poll(bpy.context) != expected)
    finally:
        update.__package__ = package


def run():
    sys.path.insert(0, ROOT)
    import ambientcg_browser as pkg
    check("package imported from the repo root",
          os.path.normcase(os.path.dirname(os.path.dirname(pkg.__file__))) == os.path.normcase(ROOT), pkg.__file__)
    check("_modules follows _NAMES", tuple(m.__name__.rsplit(".", 1)[-1] for m in pkg._modules) == pkg._NAMES)

    for round_ in ("register", "re-register after reload"):
        pkg.register()
        rows = registered(pkg)
        missing = ["%s.%s" % (m, c) for m, c, ok in rows if not ok]
        check("%s: %d classes registered" % (round_, len(rows)), rows and not missing, ", ".join(missing))
        check("%s: %d ambientcg_* properties" % (round_, len(props())), SETTINGS <= props(), sorted(SETTINGS - props()))
        check("%s: %d ambientcg.* operators" % (round_, len(operators())), operators() == list(OPERATORS),
              sorted(set(OPERATORS) - set(operators())))
        check("%s: the Update panel sits under the main panel" % round_,
              bpy.types.AMBIENTCG_PT_update.bl_parent_id == bpy.types.AMBIENTCG_PT_main.bl_idname)
        check_blender_org_copy(sys.modules[pkg.__name__ + ".update"])
        pkg.unregister()
        left = ["%s.%s" % (m, c) for m, c, ok in registered(pkg) if ok]
        check("%s: unregister removes every class" % round_, not left, ", ".join(left))
        check("%s: unregister removes ambientcg_* properties" % round_, not props(), sorted(props()))
        check("%s: unregister removes the operators" % round_, not operators(), operators())
        if round_ == "register":
            pkg = importlib.reload(pkg)


try:
    run()
except Exception:
    traceback.print_exc()
    failures.append("exception")

print("SMOKE FAILED: %s" % ", ".join(failures) if failures else "SMOKE PASSED")
sys.stdout.flush()
if failures:
    sys.exit(1)
