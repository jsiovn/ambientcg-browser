# Video guide recorded by script Implementation Plan

> **For agentic workers:** Use subagents when appropriate to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A silent 40-75 s video of AmbientCG Browser in use inside Blender, made by a reproducible script, shown
on `site/index.html` in English and Vietnamese.

**Architecture:** `tools/record_guide.ps1` builds, serves `dist\` only while it installs the add-on from that local
repository into an isolated profile (Blender's `extension repo-add` / `install` CLI), then runs
`tools/record_guide.py` twice: `record` in a second GUI Blender 5.2 (timer steps, real clicks with ctypes,
`screen.screenshot` frames plus a JSON log of times and cursor positions) and `encode` in a background Blender (the
Video Sequencer lays the frames on a 30 fps timeline from their times, a cursor sprite strip follows the logged
positions, FFmpeg H.264 MPEG-4). The encode run also writes the poster WebP.

**Tech Stack:** Blender 5.2 Python (bpy, ctypes, Video Sequencer, FFmpeg), PowerShell, plain unittest.

---

## Chunk 1: de-risking spikes (scratch only, nothing committed)

- [ ] Install: build, serve with the resolved `python.exe`, `repo-add --clear-all --url http://127.0.0.1:8767/index.json`,
  `install -s -e ambientcg_browser`, stop the server; port free before and after.
- [ ] Encode: 30 dummy PNGs → mp4 in background Blender; `movieclips.load` reports 1280×720. Introspect 5.2 names
  (`sequence_editor.strips`, `image_settings.media_type`).
- [ ] GUI: launch, activate, one `screen.screenshot` from a timer, quit; check the PNG size.

## Chunk 2: recorder

**Files:** Create `tools/record_guide.py`, `tools/record_guide.ps1`

- [ ] `record` mode: splash off, window activated and its client area set to 1280×720, default scene (the cube).
  Steps, each a real click (foreground guard before every press, poll for the state change, time out):
  AmbientCG tab → query "rock" (set on the scene settings: its update callback searches) → Browse Results → pick
  a tile → close → Add Material (1K) → Material Preview → HDRI → query "sky" → Set World HDRI (1K) → scene world
  shown. A deadline timer exits non-zero; every step is wrapped. Frames at 10 fps.
- [ ] `encode` mode: timeline from the frame times (long network waits capped), cursor strip, `Standard` view
  transform, no audio, 1280×720 30 fps H.264; asserts 40-75 s and ≤ 8 MB; writes `guide-poster.webp` ≤ 150 KB.
- [ ] Escalate if clicks stay unreliable after 2-3 approaches: run the operator from the timer while the cursor
  sprite moves to the button; note it.

## Chunk 3: site, tests, docs

**Files:** Modify `site/index.html`, `tests/test_site.py`, `CHANGELOG.md`, `CLAUDE.md`; add `site/assets/guide.mp4`,
`site/assets/guide-poster.webp`

- [ ] `site/index.html`: a "Video guide" block in the "Using it" section, EN/VI span pairs, the `<video>` of the
  bead, `class="shot"`; CSS `video` gets `display:block; max-width:100%; height:auto`.
- [ ] `tests/test_site.py`: the page references `assets/guide.mp4` and `assets/guide-poster.webp` and both files exist.
- [ ] CHANGELOG 1.1.2 `### Added`; CLAUDE.md Build & Test bullet on re-recording.
- [ ] Commit `feat: add a video guide to the install page`.

## Verification

Working directory `D:\www\ambientcg-browser`, Blender `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`,
every Blender run with `BLENDER_USER_RESOURCES=<scratch>\profile`.

1. `powershell -ExecutionPolicy Bypass -File tools\record_guide.ps1 -Work <scratch>\guide` exits 0; afterwards
   `Get-NetTCPConnection -LocalPort 8767 -State Listen` finds nothing (8767 is the script's default `-Port`).
2. Background Blender: `bpy.data.movieclips.load("site/assets/guide.mp4")` → `size` (1280, 720), `fps` 30,
   `frame_duration / 30` in 40-75; file ≤ 8 MB; poster ≤ 150 KB.
3. Extract 6 frames as PNG with Blender and look at them: AmbientCG tab, results popup, material on the cube, HDRI
   world, the cursor sprite on the clicked buttons.
4. `python -I -m unittest discover tests` → OK.
5. `powershell -ExecutionPolicy Bypass -File build.ps1` → `dist\assets\guide.mp4` exists.
6. Serve `dist` (resolved python.exe, port 8768, stopped afterwards), headless Chrome via Playwright as in
   `docs/plans/acg-oeq-bilingual-site.md`: in English and Vietnamese the video element has `videoWidth` 1280, no
   `error`, plays (currentTime advances), the heading reads in the chosen language.
7. Headless smoke tests (`tests/smoke_register.py`, `tests/test_nodes.py`) still pass: nothing under
   `ambientcg_browser/` changes, run for completeness.
