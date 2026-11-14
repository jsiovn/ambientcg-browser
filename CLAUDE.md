# Project Instructions for AI Agents

**AmbientCG Browser**: a public, GPL-3.0-or-later Blender extension (`ambientcg_browser/`) that searches
ambientcg.com and adds its CC0 materials and HDRIs to the file. Unofficial: say so wherever users read about it.

## Working Principles

How to approach any change: think before coding, keep it simple, make surgical edits, drive every task to a
verified outcome. The rules are in `.claude/rules/working-principles.md`, which Claude Code loads by itself.

## Build & Test

No package manager. Build = Blender's extension CLI; tests = plain Python for `api.py`, Blender for the rest.

```powershell
python -I -m unittest discover tests                  # api.py, tools/, the license files: no Blender, no network
powershell -ExecutionPolicy Bypass -File build.ps1    # dist\ (git-ignored): zip, index.json, index.html
```

- **Never touch the user's Blender preferences or running session.** Every Blender run is headless (or a second
  GUI instance) with an isolated profile: `BLENDER_USER_RESOURCES=<scratch dir> blender --background ...`, plus
  `--online-mode` when a test must reach ambientcg.com. No `--factory-startup` when a later run must read the
  preferences an earlier run saved.
- `tests/smoke_register.py` and `tests/test_nodes.py` run inside Blender with `--factory-startup
  --python-exit-code 1 --python <file>`; they import the dev package from the repo root.
- **Deploy for a live test:** build, serve `dist\` with `python -m http.server --directory dist`, add
  `http://127.0.0.1:<port>/index.json` as a remote repository in the isolated profile, install from it. Never test
  by hot-swapping classes or by importing the dev package beside an installed copy. One server per port: on
  Windows a second `http.server` binds a taken port without an error and requests go to either, so stop the old
  one first (the python process, not only the shell job).
- **Another Blender version:** the portable zip from `download.blender.org/release/`, extracted in a scratch
  folder and run by its full path under the same isolated-profile rules (`build.ps1 -Blender <path>`). 4.2, 4.5
  and 5.2 build the same zip, byte for byte, from the same files. Under the temp folder, extract with today's file
  dates (`unzip -DD`): a cleanup by file age once removed every never-opened file of a copy that kept the archive's
  dates. Blender still started, but `extension install-file` failed with "No module named 'encodings'".
- **Update flow without CI:** install as above, publish a bumped build into the served folder, run
  `bpy.ops.ambientcg.check_updates()` (GUI instance: the upgrade runs from a timer). `update.REPO_URL` is the
  public index, so point it at the local one in that session.
- Verify the actual environment before claiming a run passed.

## Architecture

- `__init__.py`: thin aggregator. `_NAMES` is the register order (a module comes after the ones it imports);
  each module has `register()` / `unregister()` and its classes in a module-level `_classes` tuple, which
  `tests/smoke_register.py` reads. Re-enabling after an upgrade re-runs `__init__` while the old submodules stay
  in `sys.modules`: those are reloaded, new ones imported.
- `api.py`: no `bpy`, runs under `python -I`. URLs (`full_json`, no key; `q=` matches names and tags, never an
  exact id, so `search_assets` asks a one-word query that found nothing again by `id=`), `parse_assets` (plain
  dicts; ids and attributes pass `_SAFE`: they become folder names), `attribute_for` (exact, largest under it in
  that format, smallest), the cache (`cache_root(folder)`: the picked folder itself, else
  `<temp>/ambientcg-browser`; `<id>_<attr>/` = the maps, `thumbs/<id>.png`, `api/<blake2b of the URL>.json`),
  `cached_json` (fresh for `CACHE_TTL`, a day; an older answer stands in offline or on failure; swept after
  `CACHE_KEEP`, 30 days), `safe_members` / `extract` (flat image names only, all or nothing), `download_file`
  (`.part`, cancelled per chunk), `fetch_asset` (the blocking core), `Task` (a daemon thread with `poll` /
  `finish`).
- `prefs.py`: `AmbientCGPreferences.cache_dir`, `cache_folder()` (`""` when not an enabled add-on),
  `ambientcg.cache_folder` (folders-only file browser, then `wm.save_userpref`) and `ambientcg.cache_reset`.
- `ambientcg.py`: the panel `AMBIENTCG_PT_main`, the results popup `AMBIENTCG_PT_browser` (`wm.call_panel`,
  `keep_open`; `draw` keeps `context.region_popup`, `_refresh_browser` rebuilds it with `tag_refresh_ui`), the
  operators `ambientcg.search|fetch|apply|cancel|pick`, and the Python API (the module docstring). Thread →
  timer `_tick` (0.25 s, everything caught) → `ambientcg.apply` (INTERNAL, its own undo step): bpy work only on
  the main thread. `MARK_KEY` `ambientcg_browser` (JSON) marks our Material / World; `LEGACY_KEY` is read, never
  written. Settings on `Scene.ambientcg_browser`; results, picked id and previews are module state, never saved.
- `update.py`: reads the published `index.json`, compares with the manifest version, adds the remote repository
  on first use and runs Blender's own upgrade from a timer. Off for a copy installed from extensions.blender.org
  (`from_blender_org`: its terms forbid loading code from elsewhere; Blender updates that copy). `draw()` is shared by the Update sub-panel and the
  preferences.
- `site/index.html` + `tools/make_site.py`: the landing page, filled from `dist/index.json` (`{{token}}`
  values). Self-contained: no third-party requests. The install link keeps the form Blender's own generator emits.
- `.github/workflows/publish.yml`: on push to `main`, gated on the manifest version: deploys `dist` to GitHub
  Pages and creates release `v<version>`. `test.yml`: plain tests, then build + smoke + nodes on Blender 4.2,
  4.5 and 5.2.

## Conventions

- **Version source of truth:** `version` in `ambientcg_browser/blender_manifest.toml`. Bump it to ship, and add
  a matching `## [x.y.z] - DATE` section to `CHANGELOG.md` (the workflow refuses without it).
- **License:** GPL-3.0-or-later; a worded notice says "either version 3 of the License, or (at your option) any
  later version". `LICENSE` is the unmodified GPL v3 text (GitHub reads it there) and `ambientcg_browser/LICENSE`
  is the same file again: Blender packs the whole package folder, so every zip carries the text.
  `tests/test_license.py` keeps the two identical; `tools/check_zip.py` fails a build whose zip lacks it.
- **Naming:** operators `ambientcg.*`, classes `AMBIENTCG_OT_*` / `AMBIENTCG_PT_*`, sidebar tab `AmbientCG`,
  node names `AmbientCG *`, console prefix `AmbientCG Browser:`.
- **Saved in users' files, never rename or renumber:** `MARK_KEY`, the numbers of the enum items in `api.py`.
- **Network:** only `api.py` and `update.py`; both honour `bpy.app.online_access`. The User-Agent names the
  add-on and its version, nothing about the user.
- **Install from the repository, not from disk:** Blender only upgrades an extension inside the repository it
  came from.
- English only for UI strings and comments; comment only non-obvious *why*.
- `blender_version_min = "4.2.0"`: keep to APIs that exist there, the `test` workflow checks it.

## Git

- **Branches:** `<type>/<short-description>`, kebab-case, the type from the commit list below: `feat/`, `fix/`,
  `chore/`, `docs/`, `refactor/`, `test/`, `perf/`, `style/`, `build/`, `ci/`. Examples: `feat/results-close-button`,
  `fix/popup-over-sidebar`, `docs/update-branch-guidelines`.
- **Commits:** Conventional Commits, `<type>: <short summary>`, the summary imperative ("add", not "added" or
  "adds") and ideally under 72 characters; an optional body after a blank line for more context.
  - `feat`: a new feature
  - `fix`: a bug fix
  - `chore`: maintenance tasks, tooling, dependency updates (no production code change)
  - `docs`: documentation-only changes
  - `refactor`: code change that neither fixes a bug nor adds a feature
  - `test`: adding or updating tests
  - `perf`: performance improvements
  - `style`: formatting/whitespace changes with no code behavior change
  - `build`: changes to build system or external dependencies
  - `ci`: changes to CI configuration/scripts

  Examples: `feat: add a close button to the results popup`, `fix: open the results popup beside the sidebar`,
  `docs: add working principles and git conventions`.

<!-- BEGIN TEMPLATE BD WORKFLOW -->

### PR review replies

Only append `@claude review` when posting a reply to a **conversation comment authored by `claude[bot]`**. This triggers the bot to re-run its review.

Do **not** include `@claude review` in replies to human reviewer inline thread comments or in any other conversation comments.

## Issue tracking

This repo uses `bd` for issue tracking. Use `bd`, not markdown TODO files or alternate trackers.

Live `.beads` state is local-only and should not be committed. Use one top-level executor session at a time per branch.

Preferred workflow entry points are `plan-beads`, `executor-task`, and `executor-task-worktree`. When the bead is part of an epic that should land in main as a single merge, use `executor-epic-task` (or `executor-epic-task-worktree` for parallel work) — these branch off and PR into `epic/<epic-bead-id>` instead of main. To deliver a whole epic unattended on a single branch, use `executor-epic-sequential <epic-id>` — it runs every ready child bead in turn, each in a fresh worker session that the skill picks itself (inside Herdr: an agent you start in the tab `<epic-id>-implementer` when the run asks, cleared before every bead, via `run-bead-in-herdr`; outside Herdr: a headless run of the CLI you invoked it from, `claude -p`, `opencode run` or `omp -p`, via `run-bead-headless-claude`, `-opencode` or `-omp`), commits them onto one `epic/<epic-bead-id>` branch, skips any bead that blocks, and opens one PR to the default branch at the end; use `executor-epic-sequential-worktree <epic-id>` for the same whole-epic run inside an isolated git worktree that never touches the main checkout (then `cleanup-worktree` to remove it). Use `planner-research` only inside planner sessions and keep `writing-plans` executor-only. Invoke a skill with `/<name>` in Claude Code, `/skill:<name>` in omp, or by naming it in OpenCode. The epic skills need a POSIX shell: OpenCode on Windows runs PowerShell unless `"shell"` in `opencode.json` points at Git Bash.

Each bead must be fresh-session-safe: a new executor session should be able to execute from the bead contract, persisted inputs, and local code inspection without replaying prior chat.

Workflow scaffold files such as `CLAUDE.md`, `BEADS_WORKFLOW.md`, repo-local skills under `.claude/skills/`, and repo-local subagents under `.claude/agents/` (plus their generated omp / OpenCode copies under `.omp/agents/` and `.opencode/agents/` when the repo was scaffolded with `--with-omp` / `--with-opencode`) are committed to this repo's git — so they travel with feature branches and `git worktree` checkouts. Refresh them from the template with `agent-workflow-beads update`. Plan files under `docs/plans/` are committed too: `writing-plans` commits each bead's plan on its branch, so plans travel with the branch and land in the PR. A plan is only visible from other branches once it merges; until then, inline it into the bead's `notes` (Dolt-synced) when another checkout needs it.

Keep repo exploration local. Verify the actual environment before running build, test, run, deploy, or migration commands; do not assume local execution.

Useful commands:

```bash
bd ready --json
bd show <id> --json
bd update <id> --status=in_progress
bd close <id> --reason="Completed"
bd dep add <child-id> <parent-id>
```

<!-- END TEMPLATE BD WORKFLOW -->
