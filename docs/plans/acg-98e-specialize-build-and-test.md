# acg-98e — Specialize build-and-test for this repo

## Goal

Replace the generic stage-1 `build-and-test` skill with a repo-specific stage-2 version that knows the
AmbientCG Browser build, test, and smoke-test path.

## Changes

- `.claude/skills/build-and-test/SKILL.md`: rewrite the generic stage-1 text as the specialized version.
  - Document the two verification layers (plain Python tests; Blender build + headless smoke tests).
  - Give exact commands for each, sourced from CLAUDE.md, `build.ps1`, and the test file docstrings.
  - Add a "Decide what to run" table keyed to this repo's actual paths.
  - Document the live install/update test and the multi-version Blender workflow as optional layers.

## Verification

The skill is documentation; verification is that the commands it documents are real commands that run in this repo:

```powershell
python -I -m unittest discover tests
```
- Expected: `unittest` reports `OK`, exit 0 (covers `test_api.py`, `test_license.py`, `test_site.py`).

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```
- Requires Blender on the default path or `-Blender <path>`.
- Expected: `Built <path>.zip, ...index.json and ...index.html`, exit 0.

```powershell
$env:BLENDER_USER_RESOURCES = "<scratch>\profile"
& blender --background --factory-startup --python-exit-code 1 --python tests\smoke_register.py
& blender --background --factory-startup --python-exit-code 1 --python tests\test_nodes.py
```
- Requires Blender.
- Expected: `SMOKE PASSED` and `NODES PASSED`, exit 0 each.

The plain test layer is runnable without Blender and is the minimum verification for this change; the
Blender layers are environment-dependent and documented as such.
