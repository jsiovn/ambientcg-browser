# Epic acg-w2c end-to-end verification Plan

> **For agentic workers:** Use subagents when appropriate to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the epic (B1 no extensions.blender.org special case, B2 bilingual page, B3 video guide) works as
users get it: built, installed from a served repository, and the page served.

**Architecture:** Verification only. No code is expected to change; a small defect inside a file B1-B3 touched is
fixed in its own `fix:` commit, anything larger blocks the bead.

**Tech Stack:** plain `unittest`, `build.ps1` + Blender 5.2, `python -m http.server`, a browser.

---

### Task 1: Checks

- [ ] Plain tests, build, headless smoke + nodes (isolated profile).
- [ ] Live install from the served `dist\` into a fresh isolated profile, update check reports up to date.
- [ ] Page served: EN ↔ VI, video plays, install link and zip link 200.
- [ ] grep for blender.org leftovers, CHANGELOG 1.1.2 section, manifest version.
- [ ] Record commands and results in the bead notes.

## Verification

Working directory `D:\www\ambientcg-browser`, PowerShell. `$B = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"`,
`$W = <session scratch dir>\verify` (fresh).

1. `python -I -m unittest discover tests` → `OK`, exit 0.
2. `powershell -ExecutionPolicy Bypass -File build.ps1` → `Built ...ambientcg_browser-1.1.2.zip, ...index.json and
   ...index.html`, exit 0; `dist\` holds `ambientcg_browser-1.1.2.zip`, `index.json`, `index.html`,
   `assets\guide.mp4`.
3. `$env:BLENDER_USER_RESOURCES = "$W\smoke"`, then
   `& $B --background --factory-startup --python-exit-code 1 --python tests\smoke_register.py` → `SMOKE PASSED`,
   exit 0; same with `tests\test_nodes.py` → `NODES PASSED`, exit 0.
4. Live (port 8768, checked free first with `Get-NetTCPConnection -LocalPort 8768 -State Listen`):
   - start the real `python.exe -m http.server 8768 --bind 127.0.0.1 --directory dist` (Start-Process, keep the
     process id).
   - `$env:BLENDER_USER_RESOURCES = "$W\live"`;
     `& $B --online-mode --command extension repo-add --clear-all --name Local --url http://127.0.0.1:8768/index.json local`;
     `& $B --online-mode --command extension install --sync --enable ambientcg_browser` → exit 0.
   - `& $B --background --online-mode --python-exit-code 1 --python $W\live_check.py` (no `--factory-startup`:
     it reads the saved preferences): asserts the add-on loads as `bl_ext.local.ambientcg_browser`,
     `AMBIENTCG_PT_update` is registered with parent `AMBIENTCG_PT_main` and no `poll` hiding it, the operator's
     `poll` passes, sets `update.REPO_URL` to the local index, runs `bpy.ops.ambientcg.check_updates()` →
     `{'FINISHED'}` and `update._status == "Up to date"`; prints `LIVE PASSED`.
5. Page (same server): `curl -s -o NUL -w "%{http_code}"` on `/`, `/index.json`, `/ambientcg_browser-1.1.2.zip`,
   `/assets/guide.mp4`, `/assets/guide-poster.webp` → 200 each; the install link `href` in `dist\index.html`
   resolves to the served zip (200). In a browser at `http://127.0.0.1:8768/`: Tiếng Việt switch shows Vietnamese
   text and `<html lang="vi">`, English switches back; the `<video>` plays (`currentTime` advances,
   `readyState >= 3`, no `error`). Browser unavailable → do the same checks with a headless browser driven by a
   script; neither available → block.
6. Stop the server: `Stop-Process -Id <id> -Force`; port 8768 no longer listening.
7. `grep -rn -i "blender_org\|extensions.blender.org" ambientcg_browser tests README.md CLAUDE.md site` → no output.
8. `CHANGELOG.md` `## [1.1.2]` lists the removal and the bilingual page + video;
   `ambientcg_browser/blender_manifest.toml` `version = "1.1.2"`.

**Success criteria:** every step passes; any failure is either a small fix in a B1-B3 file (re-run all steps) or
the bead is blocked with the failure and the owning bead named.
