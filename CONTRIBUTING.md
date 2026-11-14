# Contributing

Bug reports, ideas and pull requests are welcome.

## Reporting a bug

Open an [issue](https://github.com/jsiovn/ambientcg-browser/issues/new/choose) with your Blender version, your
operating system, how you installed the add-on and what the system console printed (on Windows: *Window › Toggle
System Console*; elsewhere start Blender from a terminal). Lines that start with `AmbientCG Browser:` say why a
search or a download failed.

## Working on the code

You need Blender 4.2 or newer and any Python 3 for the plain tests. There is nothing to install.

```powershell
python -I -m unittest discover tests                  # api.py, the tools, the license files: no Blender, no network
powershell -ExecutionPolicy Bypass -File build.ps1    # dist\: the zip, index.json and the landing page
```

Run the Blender tests headless with a profile of their own, so your preferences are never touched:

```powershell
$env:BLENDER_USER_RESOURCES = "$env:TEMP\ambientcg-browser-profile"
& blender --background --factory-startup --python-exit-code 1 --python tests\smoke_register.py
& blender --background --factory-startup --python-exit-code 1 --python tests\test_nodes.py
```

On Linux and macOS the same commands work with `BLENDER_USER_RESOURCES=/tmp/ambientcg-browser-profile blender …`
and forward slashes; `build.ps1` is three Blender commands, `python tools/check_zip.py <zip>` and
`python tools/make_site.py dist` (see the `test` workflow).

To try an install or an update the way users get them, serve `dist\` with
`python -m http.server --directory dist`, add `http://127.0.0.1:8000/index.json` in *Preferences › Get Extensions
› Repositories* and install from it. Do not test with *Install from Disk*: Blender never updates that copy, and
the same extension enabled from two repositories clashes.

## Layout

| Path | What it is |
|------|------------|
| `ambientcg_browser/api.py` | Everything that talks to ambientcg.com and the cache folder. No `bpy`, so it runs under plain Python. |
| `ambientcg_browser/ambientcg.py` | The panel, the operators and the Python API (its docstring is the reference). |
| `ambientcg_browser/prefs.py` | The add-on preferences: the cache folder. |
| `ambientcg_browser/update.py` | Check for Updates. |
| `site/index.html`, `tools/make_site.py` | The landing page and the script that fills it from `dist/index.json`. |
| `LICENSE`, `ambientcg_browser/LICENSE` | The GPL v3 text, and the same file again so that it is in every zip. `tools/check_zip.py` checks a built zip for it. |
| `tests/` | `test_api.py`, `test_site.py`, `test_license.py` (plain Python), `smoke_register.py`, `test_nodes.py` (inside Blender). |

## Conventions

- English only for UI strings and comments; comment only the non-obvious *why*.
- Operators are `ambientcg.*`, classes `AMBIENTCG_OT_*` / `AMBIENTCG_PT_*`; every module has `register()` /
  `unregister()` and its classes in a module-level `_classes` tuple.
- Only `api.py` (ambientcg.com) and `update.py` (the repository index) reach the network. Both honour
  `bpy.app.online_access` and never raise into the UI. A new permission goes into the manifest.
- Enum items carry explicit numbers and `MARK_KEY` names a custom property: both are saved in users' files, so
  never renumber or rename them.
- Asset ids and attributes become folder names: they pass `_SAFE` in `api.py` first. Only plain image file names
  are unpacked from a download.
- No new dependencies: the standard library and what Blender ships.
- A change users can see gets a line in `CHANGELOG.md`.

## Pull requests

Keep them small and say what you tested and on which Blender version. The `test` workflow runs the plain tests
and, on Blender 4.2 LTS, 4.5 LTS and 5.2, the build, the register smoke test and the node tests.

By contributing you agree that your work is released under the [GNU General Public License](LICENSE), either
version 3 of the License, or (at your option) any later version (GPL-3.0-or-later).
