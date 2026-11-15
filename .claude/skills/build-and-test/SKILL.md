---
name: build-and-test
description: "Run this repo's actual verification: plain unittest, the Blender extension build, and the headless Blender smoke tests. Use after implementing changes."
---

# Build and Test

**Workflow position:** Executor session, between implementation (step 3) and verification (step 6). See BEADS_WORKFLOW.md.

## Stack

AmbientCG Browser is a Blender extension (no package manager). Verification has two layers:

1. **Plain Python tests** — no Blender, no network. Cover `api.py`, `tools/`, and the license files.
2. **Blender build + smoke tests** — `build.ps1` builds the extension zip, `index.json`, and `index.html` into `dist\`; `smoke_register.py` and `test_nodes.py` run headless inside Blender.

Keep repo exploration local. Verify the actual environment before running commands; do not assume local execution. This is Windows: PowerShell for the build, Python for the tests. These exact commands also run in CI (`.github/workflows/test.yml`) on Blender 4.2, 4.5 and 5.2.

## Core Rule

Run the **exact commands** from the current plan's `## Verification` section. If it does not specify commands, use the defaults below. Do not invent substitute commands.

## Commands

### 1. Plain Python tests (no Blender, no network)

```powershell
python -I -m unittest discover tests
```

`-I` (isolated mode) matters: `api.py` is loaded by file path, not by importing the package, because importing the package would import `bpy`. This layer covers `test_api.py`, `test_license.py`, and `test_site.py`, plus `tools/`.

### 2. Build the extension (Blender required)

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

`build.ps1` requires Blender on the default path (`C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`) or pass `-Blender <path>`:
- builds the zip via `blender --command extension build`
- validates it via `blender --command extension validate`
- checks the GPL text is in the zip (`tools/check_zip.py`)
- generates `index.json` via `blender --command extension server-generate`
- generates `index.html` via `tools/make_site.py`

A fresh `dist\` is made on each run (server-generate lists every zip present, so stale zips would be published).

### 3. Headless Blender smoke tests (Blender required)

Every Blender run is headless with an **isolated profile** so the user's real preferences and session are never touched:

```powershell
$env:BLENDER_USER_RESOURCES = "<scratch dir>\profile"
& blender --background --factory-startup --python-exit-code 1 --python tests\smoke_register.py
& blender --background --factory-startup --python-exit-code 1 --python tests\test_nodes.py
```

- `smoke_register.py` — registers the dev package, checks every class in each `_classes` and the `ambientcg_*` properties, reloads it the way Reload Scripts does, registers again, then unregisters and checks everything is gone. Prints `SMOKE PASSED` / `SMOKE FAILED`.
- `test_nodes.py` — builds Material/World nodes from generated dummy maps inside Blender. Prints `NODES PASSED` / `NODES FAILED`. Skipped under plain `unittest` (it needs `bpy`).

Do **not** pass `--factory-startup` when a later run must read preferences an earlier run saved (e.g. the update flow). Add `--online-mode` only when a test must reach ambientcg.com.

### 4. Live install/update test (optional, when the plan requires it)

Build, serve, install from a local repository in the isolated profile:

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
python -m http.server --directory dist
```

Then in a Blender GUI instance with the isolated profile: add `http://127.0.0.1:<port>/index.json` as a remote repository and install from it.

Constraints:
- One server per port. On Windows a second `http.server` binds a taken port **without an error** and requests go to either server, so stop the old python process (not only the shell job) before starting a new one.
- Install from the repository, not from disk: Blender only upgrades an extension inside the repository it came from.
- Do not test by hot-swapping classes or by importing the dev package beside an installed copy.
- For the update flow: install a version, publish a bumped build into the served folder, run `bpy.ops.ambientcg.check_updates()` in a GUI instance, and point `update.REPO_URL` at the local index in that session (the upgrade runs from a timer).

### 5. Another Blender version

Download the portable zip from `download.blender.org/release/`, extract it in a scratch folder, and run it by full path: `build.ps1 -Blender <path>`. Build is byte-for-byte identical across 4.2, 4.5 and 5.2 from the same files. Under the temp folder, extract with today's file dates (`unzip -DD`).

## Decide what to run

```bash
git diff --name-only HEAD
```
If nothing staged, also check unstaged:
```bash
git diff --name-only
```

| Changed path | Action |
|---|---|
| Only docs, bead metadata, notes | Skip runtime validation unless the plan requires it |
| `api.py`, `tools/`, license files, tests under `tests/` for the plain layer | Run `python -I -m unittest discover tests` |
| `ambientcg_browser/` (package), `build.ps1`, `tools/`, the manifest, the smoke/node tests | Run build **and** the headless Blender smoke tests |
| `update.py` or install/update flow | Run the live install/update test (layer 4) |
| `site/`, `dist/` outputs, `index.html` | Run the build and inspect `dist\` |

## Validation before running

Confirm the plan's `## Verification` section has exact commands and success criteria. If it says vague things like "run the app" or "make sure it works," send it back to `writing-plans` to tighten up.

## Capture evidence

For each command note: the command, exit code, relevant output, observed behavior/logs, and whether it matched the stated success criteria. Do not summarize failed checks as "mostly passed." Report the failing step precisely.

Expected success markers:
- Plain tests: `unittest` reports `OK` and exit code 0.
- Build: `Built <path>.zip, ...index.json and ...index.html`, exit 0.
- Smoke register: prints `SMOKE PASSED`, exit 0.
- Nodes: prints `NODES PASSED`, exit 0.

Do NOT claim success without evidence.

## Fix-and-Retry Loop

If validation fails, do NOT proceed to final verification:
1. Fix the code or the verification plan
2. Re-run `build-and-test`
3. Repeat until it passes

Only proceed to final verification when `build-and-test` passes.

## Skip Conditions

Do NOT trigger this skill when:
- the user explicitly says no testing is needed
- in a planner session
- only planner artifacts changed and the current plan does not require validation
