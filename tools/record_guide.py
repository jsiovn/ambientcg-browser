"""Record the video guide of the install page (site/assets/guide.mp4) inside Blender. tools/record_guide.ps1 runs
both modes; CLAUDE.md says how.

    blender --online-mode --window-geometry 0 0 1280 720 --python tools/record_guide.py -- record <work dir>
    blender --background --factory-startup --python tools/record_guide.py -- encode <work dir> <assets dir>

record: a second GUI Blender with an isolated profile and the add-on installed from a repository. Timer steps click
real buttons (ctypes, Windows only) and screen.screenshot writes a frame every 0.1 s; frames.json keeps each frame's
time and the cursor, which screenshots leave out. The button positions are measured on Blender 5.2 at UI scale 1
in a 1280x720 window and follow the panel's layout (ambientcg._draw_search / _draw_download). A click whose effect
does not show runs the operator instead and is listed under "fallbacks".
encode: the Video Sequencer lays the frames on a 30 fps timeline by their times (network waits shortened), draws
the cursor over them and writes guide.mp4 (H.264) and guide-poster.webp.
"""

import json
import os
import shutil
import sys
import time
import traceback

import bpy

FPS = 30
SIZE = (1280, 720)
CAPTURE = 0.1        # seconds between frames
WAIT_CAP = 1.5       # seconds a network wait may last in the video
DEADLINE = 300       # seconds before the recorder gives up
ROW = 28             # pitch of the panel's rows, px at UI scale 1 (5.2)
UNIT = 20            # U.widget_unit at UI scale 1

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

def record(work):
    import ctypes
    from ctypes import wintypes
    import addon_utils
    import importlib

    # An older log must never be encoded as this run's: gone before anything here can fail.
    log_path = os.path.join(work, "frames.json")
    if os.path.exists(log_path):
        os.remove(log_path)
    log = {"frames": [], "clicks": [], "marks": {}, "fallbacks": []}

    def finish(code, why=""):
        log["exit"] = code
        log["why"] = why
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1)
        print("AmbientCG Browser: record %s %s" % ("done" if code == 0 else "FAILED", why), flush=True)
        os._exit(code)

    try:
        user32 = ctypes.windll.user32
        module = next(m.__name__ for m in addon_utils.modules() if m.__name__.endswith(".ambientcg_browser"))
        acg = importlib.import_module(module + ".ambientcg")
    except Exception:
        finish(2, "the add-on is not installed in this profile, or this is not Windows:\n" + traceback.format_exc())
    frames_dir = os.path.join(work, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    for name in os.listdir(frames_dir):
        os.remove(os.path.join(frames_dir, name))
    state = {"wait": False, "t0": time.perf_counter(), "hwnd": None, "done": False}
    win = bpy.context.window_manager.windows[0]

    def now():
        return time.perf_counter() - state["t0"]

    # -- window and input ---------------------------------------------------
    def own_window():
        pid, found = os.getpid(), []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            kind = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, kind, 64)
            # Blender's own window, not its console (--start-console)
            if owner.value == pid and user32.IsWindowVisible(hwnd) and kind.value == "GHOST_WindowClass":
                found.append(hwnd)
            return True
        user32.EnumWindows(visit, 0)
        return found[0]

    def activate():
        hwnd = state["hwnd"]
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0004)   # to the screen's top left: SWP_NOSIZE, NOZORDER
        fg = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        me = ctypes.windll.kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(fg, me, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(fg, me, False)

    def to_screen(x, y):
        pt = wintypes.POINT(int(x), int(y))
        user32.ClientToScreen(state["hwnd"], ctypes.byref(pt))
        return pt.x, pt.y

    def cursor():
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.ScreenToClient(state["hwnd"], ctypes.byref(pt))
        return pt.x, pt.y

    def ours_under_cursor():
        """Never press anywhere but in this Blender: the user's desktop may be under the pointer."""
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        hit = user32.WindowFromPoint(pt)
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hit, ctypes.byref(owner))
        return owner.value == os.getpid() and user32.GetForegroundWindow() == state["hwnd"]

    # -- capture --------------------------------------------------------------
    def capture():
        if state["done"]:
            return None
        try:
            name = "%05d.png" % len(log["frames"])
            t = now()
            area = v3d()
            with bpy.context.temp_override(window=win, area=area):
                bpy.ops.screen.screenshot(filepath=os.path.join(frames_dir, name))
            x, y = cursor()
            log["frames"].append({"file": name, "t": round(t, 3), "x": x, "y": y, "wait": state["wait"]})
        except Exception:
            finish(3, traceback.format_exc())
        return CAPTURE

    # -- layout ---------------------------------------------------------------
    def v3d():
        return next(a for a in win.screen.areas if a.type == 'VIEW_3D')

    def region(kind):
        return next(r for r in v3d().regions if r.type == kind)

    def top_of(r):
        return win.height - (r.y + r.height)

    def panel():
        """Client positions of the panel's buttons, as _draw_search / _draw_download lay them out."""
        ui = region('UI')
        s = bpy.context.scene.ambientcg_browser
        top = top_of(ui)
        mid = ui.x + (ui.width - 21) / 2
        sort = top + 50 + 3 * ROW
        status = ROW if acg.status_text() else 0
        browse = sort + status + 176
        found = acg._find(acg._picked) or (acg._results[0] if acg._results else None)
        lines = 1 + bool(found and found["maps"]) + bool(found and found["tags"])
        label = browse + 2 * ROW + 20 * (lines - 1) + 32
        if found and found["kind"] == 'HDRI':
            action = label + 5 * ROW
        else:
            action = label + (8 if s.projection == 'BOX' else 7) * ROW
        return {
            "tab": (ui.x + ui.width - 13, top + 254),
            "query": (mid - 15, top + 50 + ROW),
            "hdri": (mid + 54, top + 50 + 2 * ROW),
            "browse": (mid, browse),
            "action": (mid, action),
        }

    def shading(kind):
        header = region('HEADER')
        x = header.x + header.width - {'OVERLAYS': 165, 'MATERIAL': 58, 'RENDERED': 39}[kind]
        return x, top_of(header) + header.height / 2

    def browser_region():
        """The results popup's region while it is open."""
        r = acg._browser
        if r is None:
            return None
        try:
            with bpy.context.temp_override(window=win, region=r):
                pass
        except TypeError:
            return None
        return r

    def tile(index):
        r = browser_region()
        rows = -(-len(acg._results) // acg._browser_columns)
        used = -(-len(acg._results) // rows)
        pitch = (acg._browser_columns * 6 * UNIT + 0.3 * UNIT) / used
        row, col = divmod(index, used)
        top = top_of(r) + (1.1 * UNIT if acg.status_text() else 0)
        return r.x + 1.1 * UNIT + col * pitch + (pitch - 0.3 * UNIT) / 2, top + 7.65 * UNIT + 6.1 * UNIT * row

    def close_button():
        r = browser_region()
        return r.x + r.width - 2.6 * UNIT, top_of(r) + 1.55 * UNIT

    # -- steps (a generator: yield seconds, or a condition with its timeout) ----
    def until(test, timeout, what, soft=False):
        return ("until", test, timeout, what, soft)

    def move(x, y, seconds=0.6):
        x0, y0 = cursor()
        steps = max(1, int(seconds * 30))
        for i in range(1, steps + 1):
            k = i / steps
            k = k * k * (3 - 2 * k)   # ease in and out
            user32.SetCursorPos(*to_screen(x0 + (x - x0) * k, y0 + (y - y0) * k))
            yield 1 / 30

    def click(where, done, fallback, what):
        """Move to `where` (a function: the layout may change while the cursor moves), press, check `done`."""
        x, y = where()
        yield from move(x, y)
        x, y = where()
        user32.SetCursorPos(*to_screen(x, y))
        yield 0.35   # a press right after a move shows the tooltip and is dropped
        if ours_under_cursor():
            user32.mouse_event(0x0002, 0, 0, 0, 0)   # MOUSEEVENTF_LEFTDOWN
            log["clicks"].append({"t": round(now(), 3), "x": x, "y": y, "what": what})
            yield 0.08
            user32.mouse_event(0x0004, 0, 0, 0, 0)   # MOUSEEVENTF_LEFTUP
            yield until(done, 3.0, what, soft=True)
        if not done():
            log["fallbacks"].append(what)
            print("AmbientCG Browser: the click on %s did not take, running it directly" % what, flush=True)
            fallback()
            yield until(done, 3.0, what)

    def orbit(dx, dy):
        """Turn the view with the middle mouse button, as a user looks around."""
        x, y = cursor()
        if not ours_under_cursor():
            log["fallbacks"].append("orbit (skipped)")
            return
        user32.mouse_event(0x0020, 0, 0, 0, 0)   # MOUSEEVENTF_MIDDLEDOWN
        yield 0.1
        yield from move(x + dx, y + dy, 1.5)
        user32.mouse_event(0x0040, 0, 0, 0, 0)   # MOUSEEVENTF_MIDDLEUP

    def override(**kw):
        area = v3d()
        return bpy.context.temp_override(window=win, area=area, region=region('UI'), **kw)

    def scene_material():
        cube = bpy.data.objects.get("Cube")
        mat = cube.active_material if cube is not None else None
        return mat is not None and acg.marked(mat) is not None

    def scene_world():
        world = bpy.context.scene.world
        return world is not None and acg.marked(world) is not None

    def run():
        s = bpy.context.scene.ambientcg_browser
        space = v3d().spaces[0]
        yield 1.5
        # The opening view: the default scene, then the sidebar (N).
        space.show_region_ui = True
        yield 1.2
        yield from click(lambda: panel()["tab"], lambda: region('UI').active_panel_category == "AmbientCG",
                         lambda: setattr(region('UI'), "active_panel_category", "AmbientCG"), "AmbientCG tab")
        yield 1.0
        yield from move(*panel()["query"])
        yield 0.4
        s.query = "rock"   # as typing it and pressing Enter does: the update starts the search
        state["wait"] = True
        yield until(lambda: acg._search is None and acg._results, 40, "rock results")
        yield until(lambda: acg._thumbs is None, 40, "rock thumbnails")
        state["wait"] = False
        yield 1.2
        yield from click(lambda: panel()["browse"], lambda: browser_region() is not None,
                         lambda: _ok(override, lambda: bpy.ops.ambientcg.browse()), "Browse Results")
        state["wait"] = True
        yield until(lambda: acg._thumbs is None, 40, "popup thumbnails")
        state["wait"] = False
        yield 1.0
        log["marks"]["poster"] = round(now(), 3)
        target = acg._results[1]["id"]
        yield from click(lambda: tile(1), lambda: acg._picked == target,
                         lambda: bpy.ops.ambientcg.pick(asset_id=target), "result " + target)
        yield 1.2
        yield from click(close_button, lambda: browser_region() is None, escape, "close the results")
        yield 1.0
        yield from click(lambda: panel()["action"], lambda: acg.downloading() or scene_material(),
                         lambda: _ok(override, lambda: bpy.ops.ambientcg.fetch()), "Add Material")
        yield from move(700, 470)   # off the panel: its rows move down under the status line
        state["wait"] = True
        yield until(lambda: not acg.downloading() and scene_material(), 120, "material download")
        state["wait"] = False
        yield 1.0
        yield from click(lambda: shading('MATERIAL'), lambda: space.shading.type == 'MATERIAL',
                         lambda: setattr(space.shading, "type", 'MATERIAL'), "Material Preview")
        yield from move(760, 300)
        yield 3.5   # the shaders compile
        yield from click(lambda: panel()["hdri"], lambda: s.kind == 'HDRI',
                         lambda: setattr(s, "kind", 'HDRI'), "HDRI")
        state["wait"] = True
        yield until(lambda: acg._search is None, 40, "HDRI search")
        state["wait"] = False
        yield from move(*panel()["query"])
        yield 0.4
        s.query = "sky"
        state["wait"] = True
        yield until(lambda: acg._search is None and acg._results, 40, "sky results")
        yield until(lambda: acg._thumbs is None, 40, "sky thumbnails")
        state["wait"] = False
        yield 1.5
        yield from click(lambda: panel()["action"], lambda: acg.downloading() or scene_world(),
                         lambda: _ok(override, lambda: bpy.ops.ambientcg.fetch()), "Set World HDRI")
        yield from move(700, 470)
        state["wait"] = True
        yield until(lambda: not acg.downloading() and scene_world(), 120, "HDRI download")
        state["wait"] = False
        yield 1.0
        yield from click(lambda: shading('RENDERED'), lambda: space.shading.type == 'RENDERED',
                         lambda: setattr(space.shading, "type", 'RENDERED'), "Rendered")
        yield from click(lambda: shading('OVERLAYS'), lambda: not space.overlay.show_overlays,
                         lambda: setattr(space.overlay, "show_overlays", False), "Overlays")
        yield from move(560, 520, 0.8)
        yield 1.5
        yield from orbit(160, -110)   # up: toward the sky
        yield from move(640, 640, 0.8)
        yield 3.5
        state["done"] = True
        yield 0.2
        finish(0, "fallbacks: %s" % (log["fallbacks"] or "none"))

    def escape():
        if ours_under_cursor():
            user32.keybd_event(0x1B, 0, 0, 0)
            user32.keybd_event(0x1B, 0, 2, 0)

    def _ok(make_override, call):
        with make_override():
            call()

    gen = None
    pending = {"until": None}

    def drive():
        nonlocal gen
        try:
            if now() > DEADLINE:
                finish(4, "deadline")
            if pending["until"] is not None:
                _, test, timeout, what, soft = pending["until"]
                if test():
                    pending["until"] = None
                elif now() > pending["start"] + timeout:
                    pending["until"] = None
                    if not soft:
                        finish(5, "timed out waiting for " + what)
                else:
                    return 0.05
            step = next(gen)
            if isinstance(step, tuple):
                pending["until"], pending["start"] = step, now()
                return 0.05
            return step
        except StopIteration:
            return None
        except SystemExit:
            raise
        except Exception:
            finish(3, traceback.format_exc())

    def start():
        nonlocal gen
        try:
            prefs = bpy.context.preferences
            if prefs.view.show_splash or prefs.system.ui_scale != 1.0 or (win.width, win.height) != SIZE:
                finish(2, "needs the splash off, UI scale 1 and a %dx%d window, got %s %s %s" % (
                    SIZE + (prefs.view.show_splash, prefs.system.ui_scale, (win.width, win.height))))
            if not bpy.app.online_access:
                finish(2, "needs --online-mode")
            cache = os.path.join(work, "cache")
            os.makedirs(cache, exist_ok=True)
            prefs.addons[module].preferences.cache_dir = cache   # not the user's temp folder; never saved
            s = bpy.context.scene.ambientcg_browser
            s.resolution = '1K'
            state["hwnd"] = own_window()
            activate()
            area = v3d()
            with bpy.context.temp_override(window=win, area=area):
                bpy.ops.screen.screen_full_area()
            v3d().spaces[0].show_region_tool_header = False
            user32.SetCursorPos(*to_screen(640, 420))
            state["t0"] = time.perf_counter()
            gen = run()
            bpy.app.timers.register(capture, first_interval=0.0)
            bpy.app.timers.register(drive, first_interval=0.3)
        except SystemExit:
            raise
        except Exception:
            finish(3, traceback.format_exc())
        return None

    bpy.app.timers.register(start, first_interval=2.0)


# ---------------------------------------------------------------------------
# encode
# ---------------------------------------------------------------------------

def sprite(path, size, shape):
    """Write an RGBA PNG drawn by shape(x, y) -> (alpha, white): 4x4 supersampled."""
    import numpy as np
    w, h = size
    ss = 4
    ys, xs = np.mgrid[0:h * ss, 0:w * ss]
    xs = (xs + 0.5) / ss
    ys = (ys + 0.5) / ss
    alpha, white = shape(xs, ys)
    alpha = alpha.reshape(h, ss, w, ss).mean(axis=(1, 3))
    white = white.reshape(h, ss, w, ss).mean(axis=(1, 3))
    lum = np.where(alpha > 0, white / np.maximum(alpha, 1e-6), 0)
    rgba = np.dstack([lum, lum, lum, alpha])[::-1].astype(np.float32)   # Blender's rows start at the bottom
    img = bpy.data.images.new(os.path.basename(path), w, h, alpha=True)
    img.pixels.foreach_set(rgba.ravel())
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()


def inside(xs, ys, poly):
    """Even-odd point in polygon, vectorised."""
    import numpy as np
    hit = np.zeros(xs.shape, dtype=bool)
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        crosses = (y1 > ys) != (y2 > ys)
        with np.errstate(divide="ignore", invalid="ignore"):
            at = x1 + (ys - y1) * (x2 - x1) / (y2 - y1)
        hit ^= crosses & (xs < at)
    return hit


# The arrow, tip at (1, 1), in px: a white body with a black rim.
ARROW = [(1, 1), (1, 22), (6, 17.5), (9.5, 25), (12.5, 23.7), (9.2, 16.5), (16, 16.5)]
TIP = (1, 1)


def arrow_shape(xs, ys):
    import numpy as np
    body = inside(xs, ys, ARROW)
    rim = np.zeros_like(body)
    for dx, dy in ((1.3, 0), (-1.3, 0), (0, 1.3), (0, -1.3), (1, 1), (-1, -1), (1, -1), (-1, 1)):
        rim |= inside(xs + dx, ys + dy, ARROW)
    alpha = (rim | body).astype(float)
    return alpha, body.astype(float)


def ring_shape(xs, ys):
    import numpy as np
    d = np.hypot(xs - 24, ys - 24)
    ring = (d > 15) & (d < 20)
    return ring.astype(float) * 0.85, ring.astype(float) * 0.85


def encode(work, assets):
    import numpy as np
    with open(os.path.join(work, "frames.json"), encoding="utf-8") as f:
        log = json.load(f)
    frames = log["frames"]
    if log.get("exit") != 0 or len(frames) < 2:
        raise SystemExit("the recording did not finish: %s" % log.get("why"))

    # Times on the video clock: every network wait lasts WAIT_CAP at most.
    times, clock, i = [], 0.0, 0
    raw = [f["t"] for f in frames] + [frames[-1]["t"] + CAPTURE]
    while i < len(frames):
        j = i
        while j < len(frames) and frames[j]["wait"] == frames[i]["wait"]:
            j += 1
        span = raw[j] - raw[i]
        scale = min(1.0, WAIT_CAP / span) if frames[i]["wait"] and span > 0 else 1.0
        for k in range(i, j):
            times.append(clock + (raw[k] - raw[i]) * scale)
        clock += span * scale
        i = j
    end = clock

    def video_time(t):
        """A recorder time on the video clock."""
        k = max(0, min(len(raw) - 2, int(np.searchsorted(raw, t, side="right")) - 1))
        a = times[k]
        b = times[k + 1] if k + 1 < len(times) else end
        span = raw[k + 1] - raw[k]
        return a + (b - a) * ((t - raw[k]) / span if span else 0)

    def frame_at(seconds):
        return 1 + int(round(seconds * FPS))

    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = SIZE
    scene.render.resolution_percentage = 100
    scene.render.fps, scene.render.fps_base = FPS, 1.0
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.view_settings.exposure, scene.view_settings.gamma = 0.0, 1.0
    bpy.context.preferences.edit.keyframe_new_interpolation_type = 'LINEAR'
    seq = scene.sequence_editor_create()
    folder = os.path.join(work, "frames")

    last = frame_at(end)
    for n, (f, t) in enumerate(zip(frames, times)):
        start = frame_at(t)
        stop = frame_at(times[n + 1]) if n + 1 < len(times) else last
        if stop <= start:
            continue
        strip = seq.strips.new_image(f["file"], os.path.join(folder, f["file"]), 1, start)
        strip.duration = stop - start

    cursor_png = os.path.join(work, "cursor.png")
    ring_png = os.path.join(work, "ring.png")
    sprite(cursor_png, (20, 28), arrow_shape)
    sprite(ring_png, (48, 48), ring_shape)

    def offset(strip, size, anchor, x, y):
        """Put the sprite's anchor pixel at client (x, y): offsets are from the canvas centre, y up."""
        strip.transform.offset_x = x - SIZE[0] / 2 + size[0] / 2 - anchor[0]
        strip.transform.offset_y = (SIZE[1] - y) - SIZE[1] / 2 - size[1] / 2 + anchor[1]

    cur = seq.strips.new_image("Cursor", cursor_png, 3, 1, fit_method='ORIGINAL')
    cur.duration = last - 1
    for f, t in zip(frames, times):
        offset(cur, (20, 28), TIP, f["x"], f["y"])
        cur.transform.keyframe_insert("offset_x", frame=frame_at(t))
        cur.transform.keyframe_insert("offset_y", frame=frame_at(t))
    for n, c in enumerate(log["clicks"]):
        ring = seq.strips.new_image("Click %d" % n, ring_png, 2, frame_at(video_time(c["t"])), fit_method='ORIGINAL')
        ring.duration = 10
        offset(ring, (48, 48), (24, 24), c["x"], c["y"])

    scene.frame_start, scene.frame_end = 1, last - 1
    seconds = (last - 1) / FPS
    if not 40 <= seconds <= 75:
        raise SystemExit("the video would last %.1f s, not 40-75 s" % seconds)

    out = os.path.join(work, "video")
    os.makedirs(out, exist_ok=True)
    for name in os.listdir(out):
        os.remove(os.path.join(out, name))
    render = scene.render
    render.image_settings.media_type = 'VIDEO'
    render.image_settings.file_format = 'FFMPEG'
    render.ffmpeg.format = 'MPEG4'
    render.ffmpeg.codec = 'H264'
    render.ffmpeg.constant_rate_factor = 'MEDIUM'
    render.ffmpeg.ffmpeg_preset = 'BEST'
    render.ffmpeg.gopsize = FPS * 2
    render.ffmpeg.audio_codec = 'NONE'
    render.filepath = os.path.join(out, "guide")
    bpy.ops.render.render(animation=True)
    made = [n for n in os.listdir(out) if n.endswith(".mp4")]
    if len(made) != 1:
        raise SystemExit("expected one mp4 in %s, found %s" % (out, made))
    video = os.path.join(assets, "guide.mp4")
    if os.path.exists(video):
        os.remove(video)
    shutil.move(os.path.join(out, made[0]), video)
    size = os.path.getsize(video)
    if size > 8 * 1024 * 1024:
        raise SystemExit("guide.mp4 is %d bytes, more than 8 MB" % size)

    # The poster: the results popup, the frame the recorder marked.
    render.image_settings.media_type = 'IMAGE'
    render.image_settings.file_format = 'WEBP'
    render.image_settings.color_mode = 'RGB'
    render.image_settings.quality = 80
    scene.frame_set(frame_at(video_time(log["marks"]["poster"])))
    poster = os.path.join(assets, "guide-poster.webp")
    render.filepath = poster
    bpy.ops.render.render(write_still=True)
    if os.path.getsize(poster) > 150 * 1024:
        raise SystemExit("guide-poster.webp is %d bytes, more than 150 KB" % os.path.getsize(poster))
    print("AmbientCG Browser: wrote %s (%.1f s, %d bytes) and %s (%d bytes); fallbacks: %s" % (
        video, seconds, size, poster, os.path.getsize(poster), log["fallbacks"] or "none"), flush=True)


if __name__ == "__main__":
    if ARGS[:1] == ["record"]:
        record(os.path.abspath(ARGS[1]))
    elif ARGS[:1] == ["encode"]:
        encode(os.path.abspath(ARGS[1]), os.path.abspath(ARGS[2]))
    else:
        raise SystemExit(__doc__)
