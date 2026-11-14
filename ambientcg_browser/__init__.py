"""AmbientCG Browser: search ambientcg.com and add its CC0 materials and HDRIs to the file (3D Viewport > Sidebar > AmbientCG).

One module per feature; each has register() / unregister() and its classes in a module-level _classes tuple.
"""

import importlib
import sys

# Register order, also the reload order: a module comes after the ones it imports at the top.
# update after ambientcg: its sub-panel needs the main panel registered first.
_NAMES = ("api", "prefs", "ambientcg", "update")


def _load(name):
    # Reload Scripts and re-enabling after an upgrade on disk re-run this file while the old submodules
    # stay cached: reload those, import the ones a newer version added.
    full = "%s.%s" % (__name__, name)
    return importlib.reload(sys.modules[full]) if full in sys.modules else importlib.import_module(full)


_modules = tuple(_load(name) for name in _NAMES)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
