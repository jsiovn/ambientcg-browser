# Remove extensions.blender.org support Implementation Plan

> **For agentic workers:** Use subagents when appropriate to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Undo the code of commit 75d7960 so the update check runs for every installed copy, and ship it as 1.1.2.

**Architecture:** A surgical revert of 75d7960's code and docs (not its CHANGELOG 1.1.1 entry), plus a version bump
and a new CHANGELOG section. Nothing else in the repo uses `from_blender_org` (checked with grep).

**Tech Stack:** Blender 4.2+ extension (Python), plain `unittest`, `build.ps1`.

---

### Task 1: Runtime code

**Files:**

- Modify: `ambientcg_browser/update.py` (docstring line, `import urllib.parse`, `BLENDER_ORG_HOST`,
  `from_blender_org`, `poll` of `AMBIENTCG_OT_check_updates`, `poll` of `AMBIENTCG_PT_update`)
- Modify: `ambientcg_browser/prefs.py:119-121`

- [ ] Remove each addition of 75d7960 in `update.py`; `urllib.parse` is used nowhere else in that file.
- [ ] In `prefs.py` replace the `if not update.from_blender_org(context):` block with the unconditional
      `col.separator()` + `update.draw(col)`.

### Task 2: Tests

**Files:** Modify `tests/smoke_register.py` (remove `check_blender_org_copy` and its call in `run()`).

### Task 3: Docs and version

**Files:**

- `README.md` Privacy: drop "; never for a copy installed from extensions.blender.org", re-wrap.
- `CLAUDE.md` `update.py` bullet: drop the "Off for a copy ..." sentence, re-wrap to ~115 columns.
- `ambientcg_browser/blender_manifest.toml`: `version = "1.1.2"`.
- `CHANGELOG.md`: new `## [1.1.2] - 2026-11-20` / `### Removed` above 1.1.1; 1.1.1 untouched.

- [ ] Commit: `fix: run the update check for every installed copy again`.

## Verification

Working directory: `D:\www\ambientcg-browser`. Blender: `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`.

1. `grep -rn -i "blender_org\|extensions.blender.org" ambientcg_browser tests README.md CLAUDE.md --include=*.py --include=*.md --include=*.toml`
   prints nothing.
2. `python -I -m unittest discover tests` -> `OK`.
3. `powershell -ExecutionPolicy Bypass -File build.ps1` -> succeeds; `dist\ambientcg_browser-1.1.2.zip` exists.
4. Isolated profile (`BLENDER_USER_RESOURCES=<scratch dir>`):
   `blender --background --factory-startup --python-exit-code 1 --python tests/smoke_register.py` and the same
   with `tests/test_nodes.py` -> exit 0.

Success: all four pass. No live network test is needed: the change only removes a gate.
