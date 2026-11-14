"""AmbientCG Browser: search ambientcg.com, download CC0 materials and HDRIs, attach them to the current file.

The panel downloads on a daemon thread (api.Task) polled by a timer. This module's functions are the same work for
scripts: synchronous, no context needed. The package is `bl_ext.<repository>.ambientcg_browser`, the repository
module being the one listed in Preferences > Get Extensions > Repositories:

    import importlib
    import addon_utils
    name = next(m.__name__ for m in addon_utils.modules() if m.__name__.endswith(".ambientcg_browser"))
    ambientcg = importlib.import_module(name + ".ambientcg")

    ambientcg.search("wood", kind="MATERIAL", sort="Popular", limit=24, offset=0, refresh=False)
                                                              # [{"id", "name", "kind", "tags", "maps", "thumb",
                                                              #   "downloads"}]
    ambientcg.asset("Rock035", refresh=False)                 # one asset, None when unknown
    ambientcg.fetch("Rock035", "2K-JPG")                      # folder of the extracted maps, downloaded once
    mat = ambientcg.create_material("Rock035", "2K-JPG", projection="BOX", blend=0.2, scale=1.0, use_ao=True)
    ambientcg.assign(mat, bpy.context.selected_objects)       # the objects that changed
    ambientcg.set_world("DayEnvironmentHDRI114", "4K", strength=1.0, rotation=0.0)
    ambientcg.attach("Rock035")                               # material on the selection, or world for an HDRI

Attributes are `1K-JPG` .. `8K-PNG` for a material, `1K` .. `16K` for an HDRI; None = 2K (JPG), or the nearest there
is. Projection: FLAT (the UV map), BOX, SPHERE, TUBE. A second call reuses the marked material / world; other options
rebuild its nodes. Every function raises RuntimeError when it must go online and Online Access is off (a downloaded
asset still works) and api.FetchError (.code, .text, .detail) when AmbientCG fails. root=None = cache_root().
search and asset answer from the cache for a day, and from an older answer when AmbientCG cannot be asked, offline
too; refresh=True asks AmbientCG first.
The operators do the same with the panel's settings:
`bpy.ops.ambientcg.fetch('EXEC_DEFAULT', True, asset_id="Rock035", attribute="1K-JPG", kind='MATERIAL')`
returns at once: it downloads in the background, then applies through the timer.
"""

import json
import os
import traceback

import bpy
import bpy.utils.previews
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty, PointerProperty

from . import api, prefs

# ID prop on our Material / World, JSON {"v": 1, "asset", "attribute", "kind", "opts"}.
MARK_KEY = "ambientcg_browser"
# The key this panel wrote before it became an add-on of its own: read, never written.
LEGACY_KEY = "ocbaby_ambientcg"
MARK_VERSION = 1
PREFIX = "AmbientCG "
_TICK = 0.25
_BROWSER_COLUMNS = 8
_DEFAULT_RESOLUTION = "2K"
_DEFAULT_FORMAT = "JPG"
_OFFLINE = "Allow Online Access in Preferences > System > Network"


# ---------------------------------------------------------------------------
# Marker
# ---------------------------------------------------------------------------

def mark(block, asset_id, attribute, kind, opts=None):
    block[MARK_KEY] = json.dumps({"v": MARK_VERSION, "asset": asset_id, "attribute": attribute, "kind": kind,
                                  "opts": opts})


def marked(block):
    """The marker of a Material / World, None when it is not ours."""
    for key in (MARK_KEY, LEGACY_KEY):
        try:
            data = json.loads(block[key])
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("v") == MARK_VERSION:
            return data
    return None


def _marked_block(blocks, asset_id, attribute):
    for block in blocks:
        data = None if block.library is not None else marked(block)
        if data and data.get("asset") == asset_id and attribute in (None, data.get("attribute")):
            return block
    return None


def material_for(asset_id, attribute=None):
    return _marked_block(bpy.data.materials, asset_id, attribute)


def world_for(asset_id, attribute=None):
    return _marked_block(bpy.data.worlds, asset_id, attribute)


# ---------------------------------------------------------------------------
# Node building (no context)
# ---------------------------------------------------------------------------

def _load_image(path, non_color=False, relative=True):
    known = set(bpy.data.images)
    img = bpy.data.images.load(path, check_existing=True)
    if img in known and not img.has_data:
        # Its file was missing (a cleared temp folder) and is back: Blender does not look again by itself.
        img.reload()
    if non_color:
        img.colorspace_settings.name = 'Non-Color'
    # Only a newly loaded image: writing filepath reloads it.
    if relative and bpy.data.filepath and img not in known:
        try:
            img.filepath = bpy.path.relpath(img.filepath)
        except ValueError:  # another drive on Windows
            pass
    return img


def _image_ok(block, node_name):
    """True when the image of a marked block's texture node still exists on disk."""
    node = block.node_tree.nodes.get(node_name) if block.node_tree is not None else None
    img = getattr(node, "image", None)
    return img is not None and os.path.isfile(bpy.path.abspath(img.filepath, library=img.library))


def _node(nt, idname, x, y):
    node = nt.nodes.new(idname)
    node.location = (x, y)
    return node


def _mix_socket(sockets, name, typ):
    # The Mix node keeps one socket per data type under the same name; only one is enabled.
    return next(s for s in sockets if s.name == name and s.type == typ)


def principled_of(nt):
    """The Principled BSDF feeding the active Material Output, else the first one."""
    out = nt.get_output_node('ALL')
    if out is not None:
        for link in out.inputs["Surface"].links:
            if link.from_node.bl_idname == 'ShaderNodeBsdfPrincipled':
                return link.from_node
    return next((n for n in nt.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled'), None)


def _named(nt, idname, name, x, y):
    node = _node(nt, idname, x, y)
    node.name = PREFIX + name
    node.label = name
    return node


def build_material(mat, maps, projection="FLAT", blend=0.2, scale=1.0, use_ao=True):
    """Wire the maps ({role: path}) into mat's Principled BSDF; our older nodes go first, the BSDF and the output stay."""
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    for node in [n for n in nt.nodes if n.name.startswith(PREFIX)]:
        nt.nodes.remove(node)
    bsdf = principled_of(nt)
    if bsdf is None:
        bsdf = _node(nt, 'ShaderNodeBsdfPrincipled', 0, 0)
    out = nt.get_output_node('ALL') or _node(nt, 'ShaderNodeOutputMaterial', 300, 0)
    if not out.inputs["Surface"].is_linked:
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    left = bsdf.location.x - 900
    coord = _named(nt, 'ShaderNodeTexCoord', "Coordinates", left - 400, 0)
    mapping = _named(nt, 'ShaderNodeMapping', "Mapping", left - 200, 0)
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    nt.links.new(coord.outputs["UV" if projection == 'FLAT' else "Object"], mapping.inputs["Vector"])

    def texture(role, name, y, non_color=True):
        node = _named(nt, 'ShaderNodeTexImage', name, left, y)
        node.image = _load_image(maps[role], non_color)
        node.projection = projection
        if projection == 'BOX':
            node.projection_blend = blend
        nt.links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        return node

    links = nt.links
    color = texture("color", "Color", 400, non_color=False).outputs["Color"]
    if use_ao and "ao" in maps:
        ao = texture("ao", "Ambient Occlusion", 120)
        mix = _named(nt, 'ShaderNodeMix', "AO Multiply", left + 320, 300)
        mix.data_type = 'RGBA'
        mix.blend_type = 'MULTIPLY'
        _mix_socket(mix.inputs, "Factor", 'VALUE').default_value = 1.0
        links.new(color, _mix_socket(mix.inputs, "A", 'RGBA'))
        links.new(ao.outputs["Color"], _mix_socket(mix.inputs, "B", 'RGBA'))
        color = _mix_socket(mix.outputs, "Result", 'RGBA')
    links.new(color, bsdf.inputs["Base Color"])
    if "roughness" in maps:
        links.new(texture("roughness", "Roughness", -160).outputs["Color"], bsdf.inputs["Roughness"])
    if "metalness" in maps:
        links.new(texture("metalness", "Metalness", -440).outputs["Color"], bsdf.inputs["Metallic"])
    if "normal" in maps:
        normal = _named(nt, 'ShaderNodeNormalMap', "Normal Map", left + 320, -720)
        normal.inputs["Strength"].default_value = 1.0
        links.new(texture("normal", "Normal", -720).outputs["Color"], normal.inputs["Color"])
        links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
    if "opacity" in maps:
        links.new(texture("opacity", "Opacity", -1000).outputs["Color"], bsdf.inputs["Alpha"])
    if "emission" in maps:
        links.new(texture("emission", "Emission", -1280, non_color=False).outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 1.0
    if "displacement" in maps:
        disp = _named(nt, 'ShaderNodeDisplacement', "Displacement", bsdf.location.x, -700)
        disp.inputs["Midlevel"].default_value = 0.5
        disp.inputs["Scale"].default_value = 0.05
        links.new(texture("displacement", "Height", -1560).outputs["Color"], disp.inputs["Height"])
        links.new(disp.outputs["Displacement"], out.inputs["Displacement"])
        mat.displacement_method = 'BUMP'
    return mat


def build_world(world, exr, strength=1.0, rotation=0.0):
    if world.node_tree is None:
        world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    coord = _named(nt, 'ShaderNodeTexCoord', "Coordinates", -800, 0)
    mapping = _named(nt, 'ShaderNodeMapping', "Mapping", -600, 0)
    env = _named(nt, 'ShaderNodeTexEnvironment', "Environment", -400, 0)
    env.image = _load_image(exr)
    background = _named(nt, 'ShaderNodeBackground', "Background", -100, 0)
    output = _named(nt, 'ShaderNodeOutputWorld', "Output", 100, 0)
    nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], background.inputs["Color"])
    nt.links.new(background.outputs["Background"], output.inputs["Surface"])
    _push_world(world, strength, rotation)
    return world


def _push_world(world, strength, rotation):
    nodes = world.node_tree.nodes
    background, mapping = nodes.get(PREFIX + "Background"), nodes.get(PREFIX + "Mapping")
    if background is not None:
        background.inputs["Strength"].default_value = strength
    if mapping is not None:
        mapping.inputs["Rotation"].default_value[2] = rotation


# ---------------------------------------------------------------------------
# Python API (synchronous, no context)
# ---------------------------------------------------------------------------

_roots = {}   # picked folder -> cache root: the panel asks on every redraw, and asking makes folders


def cache_root():
    folder = prefs.cache_folder()
    folder = folder if prefs.folder_state(folder) is None else ""
    if folder not in _roots:
        _roots[folder] = api.cache_root(folder)
    return _roots[folder]


def _require_online():
    if not bpy.app.online_access:
        raise RuntimeError(_OFFLINE)


def search(query="", kind="MATERIAL", sort="Popular", limit=api.PAGE, offset=0, refresh=False):
    try:
        return api.search_assets(query, kind, sort, limit, offset, cache_root(), bpy.app.online_access, refresh)[0]
    except api.FetchError as ex:
        if ex.code == "OFFLINE":
            raise RuntimeError(_OFFLINE) from None
        raise


def asset(asset_id, refresh=False):
    try:
        data = api.cached_json(api.asset_url(asset_id), cache_root(), bpy.app.online_access, refresh)[0]
    except api.FetchError as ex:
        if ex.code == "OFFLINE":
            raise RuntimeError(_OFFLINE) from None
        raise
    found = api.parse_assets(data)
    return found[0] if found else None


def _resolve(asset_id, attribute, kind):
    """(attribute, kind), asking AmbientCG only for what is missing."""
    if attribute and kind:
        return attribute, kind
    found = asset(asset_id)
    if found is None:
        raise api.FetchError("MISSING", "%s is not on AmbientCG" % asset_id)
    if kind and kind != found["kind"]:
        raise api.FetchError("KIND", "%s is not a %s" % (asset_id, kind.lower()))
    attribute = attribute or api.attribute_for(found, _DEFAULT_RESOLUTION, _DEFAULT_FORMAT)
    if not attribute:
        raise api.FetchError("MISSING", "%s has no download" % asset_id)
    return attribute, found["kind"]


def fetch(asset_id, attribute=None, kind=None, root=None):
    attribute, kind = _resolve(asset_id, attribute, kind)
    root = root or cache_root()
    folder = api.asset_dir(root, asset_id, attribute)
    if api.is_complete(folder, kind):
        return folder
    _require_online()
    return api.fetch_asset(asset_id, attribute, kind, root)


def create_material(asset_id, attribute=None, projection="FLAT", blend=0.2, scale=1.0, use_ao=True, root=None):
    # Rounded: a panel slider is a float32, so 0.2 comes back as 0.20000000298 and would read as other options.
    opts = {"projection": projection, "blend": round(blend, 4), "scale": round(scale, 4), "use_ao": bool(use_ao)}
    mat = material_for(asset_id, attribute)
    if mat is not None:
        data = marked(mat)
        if data.get("opts") == opts and _image_ok(mat, PREFIX + "Color"):
            return mat
        attribute = data["attribute"]
    attribute, kind = _resolve(asset_id, attribute, "MATERIAL")
    maps = api.maps_in(fetch(asset_id, attribute, kind, root))
    if mat is None:
        mat = bpy.data.materials.new("%s%s %s" % (PREFIX, asset_id, attribute))
    build_material(mat, maps, projection, blend, scale, use_ao)
    mark(mat, asset_id, attribute, kind, opts)
    return mat


def assign(material, objects):
    """Make material the active material of objects (a slot is added when there is none); the objects changed."""
    changed = []
    for ob in objects:
        data = ob.data
        if ob.library is not None or data is None or not hasattr(data, "materials") or data.library is not None:
            continue
        if ob.material_slots:
            ob.active_material = material
        else:
            data.materials.append(material)
        changed.append(ob)
    return changed


def set_world(asset_id, attribute=None, strength=1.0, rotation=0.0, scene=None, root=None):
    """Our own World on scene: the world the scene had stays intact in the file."""
    world = world_for(asset_id, attribute)
    if world is not None and _image_ok(world, PREFIX + "Environment"):
        _push_world(world, strength, rotation)
    else:
        if world is not None:
            attribute = marked(world)["attribute"]
        attribute, kind = _resolve(asset_id, attribute, "HDRI")
        maps = api.maps_in(fetch(asset_id, attribute, kind, root))
        if world is None:
            world = bpy.data.worlds.new(PREFIX + asset_id)
        build_world(world, maps["hdr"], strength, rotation)
        mark(world, asset_id, attribute, kind)
    (scene or bpy.context.scene).world = world
    return world


def attach(asset_id, objects=None, kind=None, attribute=None, scene=None, **opts):
    """A material on objects (None = the selection), or an HDRI as the scene's world; the Material / World."""
    attribute, kind = _resolve(asset_id, attribute, kind)
    if kind == 'HDRI':
        return set_world(asset_id, attribute, scene=scene, **opts)
    mat = create_material(asset_id, attribute, **opts)
    if not assign(mat, bpy.context.selected_objects if objects is None else objects):
        mat.use_fake_user = True   # nothing uses it: a save would drop it
    return mat


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _world_update(self, context):
    world = self.id_data.world
    if world is not None and world.library is None and marked(world) is not None:
        _push_world(world, self.strength, self.rotation)


def _shown_page():
    return _search["page"] if _search is not None else _page


def _filter_update(self, context):
    """Search again for the settings, unless they are what the page already answers."""
    shown = _shown_page()
    if (shown.get("query"), shown.get("kind"), shown.get("sort")) == (self.query, self.kind, self.sort):
        return
    _start_search(self.query, self.kind, self.sort, 0)


def _query_update(self, context):
    # Enter in the search box; leaving the box fires this too, also on a blank box before any search.
    if self.query or _shown_page():
        _filter_update(self, context)


class AmbientCGSettings(bpy.types.PropertyGroup):
    query: StringProperty(
        name="Search", description="Words to look for, then press Enter; blank = everything", update=_query_update,
    )
    kind: EnumProperty(
        name="Type", description="What to look for", items=api.KIND_ITEMS, default='MATERIAL', update=_filter_update,
    )
    sort: EnumProperty(
        name="Sort", description="Order of the results", items=api.SORT_ITEMS, default='Popular', update=_filter_update,
    )
    resolution: EnumProperty(
        name="Resolution", description="Size of the download; the nearest one when the asset lacks it",
        items=api.RESOLUTION_ITEMS, default='2K',
    )
    format: EnumProperty(name="Format", description="Image format of the maps", items=api.FORMAT_ITEMS, default='JPG')
    projection: EnumProperty(
        name="Projection", description="How the maps are laid onto the object", items=api.PROJECTION_ITEMS, default='FLAT',
    )
    blend: FloatProperty(
        name="Blend", description="How softly Box projection blends between the sides", default=0.2, min=0.0, max=1.0,
        subtype='FACTOR',
    )
    scale: FloatProperty(
        name="Scale", description="How many times the texture repeats", default=1.0, min=0.001, soft_max=100.0,
    )
    use_ao: BoolProperty(name="Ambient Occlusion", description="Darken the crevices of the color map", default=True)
    assign: BoolProperty(name="Assign to Selection", description="Put the material on the selected objects", default=True)
    strength: FloatProperty(
        name="Strength", description="Brightness of the HDRI", default=1.0, min=0.0, soft_max=10.0, update=_world_update,
    )
    rotation: FloatProperty(
        name="Rotation", description="Turn the HDRI about the vertical", default=0.0, subtype='ANGLE',
        update=_world_update,
    )


# ---------------------------------------------------------------------------
# Jobs (module state, polled by a timer; never saved)
# ---------------------------------------------------------------------------

_results = []
_count = 0
_page = {}       # the query the results answer: query, kind, sort, offset
_picked = ""     # asset id
_previews = None # key = asset id; a preview collection, never Image data-blocks, so the .blend gains nothing
_search = None   # {"task", "page"}
_thumbs = None   # Task
_download = None # {"task", "asset", "attribute", "kind", "scene", "objects", "opts", "root", "cancelled"}
_status = ""
_browser = None  # the Region of the results popup, which can be gone: see _refresh_browser
_browser_columns = _BROWSER_COLUMNS  # the popup is narrowed to what fits beside the sidebar: see _browser_cursor


def _find(asset_id):
    return next((a for a in _results if a["id"] == asset_id), None)


def _load_preview(asset_id, path):
    if _previews is None or asset_id in _previews:
        return
    try:
        _previews.load(asset_id, path, 'IMAGE')
    except (KeyError, RuntimeError):
        pass


def _icon_of(asset_id):
    """The preview's icon id, else the plain image icon until its thumbnail lands."""
    if _previews is not None and asset_id in _previews:
        return _previews[asset_id].icon_id
    return bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items['IMAGE_DATA'].value


def _refresh_browser():
    """Rebuild the open results popup. Popups only rebuild after a click of their own, and a page or a thumbnail
    lands later. The Region may be freed by now: asking for it in a context override is how Blender's own Extensions
    code tells (bl_extension_notify._region_exists)."""
    global _browser
    if _browser is None:
        return
    wm = bpy.context.window_manager
    for win in (wm.windows if wm is not None else ()):
        try:
            with bpy.context.temp_override(window=win, region=_browser):
                _browser.tag_redraw()
                _browser.tag_refresh_ui()
                return
        except TypeError:
            continue
    _browser = None


def _browser_cursor(context, beside, mouse_y):
    """Where to put the cursor so that the results popup opens beside the region `beside` (the sidebar) instead of
    over it, and how many columns fit there. Blender places a wm.call_panel popup relative to the cursor: the
    depressed button, here the picked tile, lands on it at 80% of its width, or the popup's centre when nothing is
    depressed (ui_block_func_POPOVER). The offsets below were measured on 4.2 and 5.2 at scale 1 and hold in units;
    the window clamp catches the rest. Returns (x, y, columns)."""
    window = context.window
    system = context.preferences.system
    unit = round(18 * system.ui_scale) + 2 * system.pixel_size   # U.widget_unit
    gap, shadow, tile = 0.5 * unit, 2.25 * unit, 6 * unit      # the popup region is the layout plus 2.25 units
    room_left = beside.x - gap
    room_right = window.width - (beside.x + beside.width) - gap
    room = max(room_left, room_right)
    columns = int(min(_BROWSER_COLUMNS, max(1, (room - shadow) // tile)))
    layout_w = columns * tile
    rows = -(-len(_results) // columns)
    columns_used = -(-len(_results) // rows)     # grid_flow balances its rows: 11 tiles in 8 columns make 6, not 8 + 3
    anchor_x, depth = 1.1 * unit + layout_w / 2, 1.55 * unit
    index = next((i for i, found in enumerate(_results) if found["id"] == _picked), None)
    if index is not None:
        row, col = divmod(index, columns_used)
        pitch = (layout_w + 0.3 * unit) / columns_used
        anchor_x = 1.1 * unit + col * pitch + 0.8 * (pitch - 0.3 * unit)
        depth = (7.8 + 6.1 * row + (1.1 if status_text() else 0)) * unit
    if room_left >= room_right:
        left = beside.x - gap - (layout_w + shadow)
    else:
        left = beside.x + beside.width + gap
    x = left + anchor_x
    y = mouse_y + 1.5 * unit - depth             # the pager level with the button that opened the popup
    return int(min(max(x, 0), window.width - 1)), int(min(max(y, 0), window.height - 1)), columns


def _redraw():
    prefs.redraw()
    _refresh_browser()


def _ensure_timer():
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=_TICK, persistent=True)


def _start_search(query, kind, sort, offset, refresh=False):
    """A newer search replaces a running one: its answer is dropped."""
    global _search, _status
    root, online = cache_root(), bpy.app.online_access   # read here: the prefs belong to the main thread
    task = api.Task(lambda _task: api.search_assets(query, kind, sort, api.PAGE, offset, root, online, refresh)).start()
    _search = {"task": task, "page": {"query": query, "kind": kind, "sort": sort, "offset": offset}}
    _status = ""
    _ensure_timer()


def _start_thumbs(assets):
    """Load the thumbnails that are on disk, fetch the others on one task."""
    global _thumbs
    if _thumbs is not None:
        _thumbs.cancel()
        _thumbs = None
    root = cache_root()
    wanted = []
    for found in assets:
        path = api.thumb_path(root, found["id"])
        if os.path.isfile(path):
            _load_preview(found["id"], path)
        elif found["thumb"] and bpy.app.online_access:
            wanted.append((found["id"], found["thumb"], path))
    if wanted:
        _thumbs = api.Task(api.fetch_thumbs, wanted).start()
        _ensure_timer()


def _load_ready_thumbs():
    global _thumbs
    if _thumbs is None:
        return
    done = _thumbs.poll() == api.DONE   # asked first: nothing lands in ready after this
    with _thumbs.lock:
        ready = list(_thumbs.ready)
        _thumbs.ready.clear()
    for asset_id, path in ready:
        _load_preview(asset_id, path)
    if ready:
        _redraw()
    if done:
        _thumbs = None


def _start_download(asset_id, attribute, kind, scene, objects, opts, root):
    global _download, _status
    task = api.Task(lambda t: api.fetch_asset(asset_id, attribute, kind, root, lambda: t.cancelled, t.report)).start()
    _download = {"task": task, "asset": asset_id, "attribute": attribute, "kind": kind, "scene": scene,
                 "objects": objects, "opts": opts, "root": root, "cancelled": None}
    _status = ""
    _ensure_timer()


def _apply_from_timer(job):
    scene = bpy.data.scenes.get(job["scene"])
    if scene is None:
        return "the scene is no longer in the file"
    kw = dict(asset_id=job["asset"], attribute=job["attribute"], kind=job["kind"], scene=job["scene"],
              objects=json.dumps(job["objects"]), opts=json.dumps(job["opts"]), root=job["root"])
    wm = bpy.context.window_manager
    win = wm.windows[0] if wm is not None and wm.windows else None
    if win is not None:
        with bpy.context.temp_override(window=win):
            bpy.ops.ambientcg.apply('EXEC_DEFAULT', True, **kw)
    else:
        bpy.ops.ambientcg.apply('EXEC_DEFAULT', True, **kw)
    return None


def _tick_search():
    global _search, _results, _count, _page, _picked, _status
    if _search is None or _search["task"].poll() == api.RUNNING:
        return
    current, _search = _search, None
    try:
        _results, _count, saved = current["task"].finish()
    except api.FetchError as ex:
        if ex.code == "OFFLINE":
            _status = ex.text
        else:
            _status = "Failed: " + ex.text
            print("AmbientCG Browser: %s\n%s" % (ex.text, ex.detail))
        _redraw()
        return
    _page = current["page"]
    if _find(_picked) is None:
        _picked = _results[0]["id"] if _results else ""
    _status = "No results" if not _results else "Saved results, AmbientCG not reached" if saved else ""
    _start_thumbs(_results)
    _redraw()


def _tick_download():
    global _download, _status
    if _download is None:
        return
    if _download["task"].poll() == api.RUNNING:
        _redraw()
        return
    current, _download = _download, None
    try:
        current["task"].finish()
    except api.FetchError as ex:
        if ex.code == "CANCELLED":
            _status = current["cancelled"] or "Cancelled"
        else:
            _status = "Failed: " + ex.text
            print("AmbientCG Browser: %s\n%s" % (ex.text, ex.detail))
        _redraw()
        return
    if current["cancelled"]:   # the last chunk was in before the cancel: the file stays cached, unused
        _status = current["cancelled"]
    else:
        why = _apply_from_timer(current)
        if why:
            _status = "Failed: " + why
    _redraw()


def _tick():
    """Timer: poll the search, thumbnails and download. A raising timer is silently unregistered, so everything
    is caught; the headless suites call it in a loop."""
    global _search, _thumbs, _download, _status
    try:
        _load_ready_thumbs()
        _tick_search()
        _tick_download()
    except Exception:
        traceback.print_exc()
        _search = _thumbs = _download = None
        _status = "Failed: see the system console"
        return None
    return _TICK if running() else None


def running():
    return _search is not None or _thumbs is not None or _download is not None


def downloading():
    return _download is not None


def status_text():
    if _download is not None:
        if _download["cancelled"]:
            return "Cancelling…"
        name = "%s %s" % (_download["asset"], _download["attribute"])
        progress = _download["task"].progress
        if progress is None:
            return "Downloading %s" % name
        done, total = progress
        if total:
            if done >= total:
                return "Unpacking %s" % name
            return "Downloading %s %d %%" % (name, 100 * done // total)
        return "Downloading %s %s" % (name, api.size_text(done))
    return "Searching…" if _search is not None else _status


def cancel(why="Cancelled"):
    """Stop the running download. The thread ends at its next chunk and removes its partial file; until the timer
    sees that, downloading() stays True, so a new download can never meet the old one's file."""
    if _download is not None:
        _download["task"].cancel()
        _download["cancelled"] = why


@bpy.app.handlers.persistent
def _load_pre(*_args):
    # The job applies to a scene by name: never in another file.
    cancel("Cancelled: another file was opened")


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

def _options(settings, kind):
    if kind == 'HDRI':
        return {"strength": settings.strength, "rotation": settings.rotation}
    return {"projection": settings.projection, "blend": settings.blend, "scale": settings.scale,
            "use_ao": settings.use_ao}


def _attach_now(op, asset_id, attribute, kind, scene, objects, opts, root):
    """Build from a downloaded asset (the panel's own settings, nothing from AmbientCG); the operator's return."""
    global _status
    try:
        if kind == 'HDRI':
            set_world(asset_id, attribute, scene=scene, root=root, **opts)
        else:
            mat = create_material(asset_id, attribute, root=root, **opts)
            if not assign(mat, objects):
                mat.use_fake_user = True   # nothing uses it: a save would drop it
    except (api.FetchError, RuntimeError, KeyError, TypeError) as ex:
        _status = "Failed: %s" % ex
        op.report({'WARNING'}, "Cannot add %s: %s" % (asset_id, ex))
        return {'CANCELLED'}
    _status = "Added %s %s" % (asset_id, attribute)
    op.report({'INFO'}, _status)
    return {'FINISHED'}


class AMBIENTCG_OT_search(bpy.types.Operator):
    bl_idname = "ambientcg.search"
    bl_label = "Search AmbientCG"
    bl_description = "Look for materials or HDRIs on ambientcg.com"

    offset: IntProperty(default=0, min=0, options={'HIDDEN', 'SKIP_SAVE'})
    paged: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        s = context.scene.ambientcg_browser
        # Prev / Next stay on the query the page answers, whatever the search box holds by now.
        page = _page if self.paged and _page else {"query": s.query, "kind": s.kind, "sort": s.sort}
        # The button is the way to fresh results: Enter, the filters and the pages take the cache.
        _start_search(page["query"], page["kind"], page["sort"], self.offset, refresh=not self.paged)
        return {'FINISHED'}


class AMBIENTCG_OT_fetch(bpy.types.Operator):
    bl_idname = "ambientcg.fetch"
    bl_label = "Add from AmbientCG"
    bl_description = "Download the picked asset once and add it to this file"
    bl_options = {'REGISTER', 'UNDO'}

    asset_id: StringProperty(description="Blank = the picked result", options={'SKIP_SAVE'})
    attribute: StringProperty(description="Blank = from the Resolution and Format settings", options={'SKIP_SAVE'})
    kind: StringProperty(description="MATERIAL or HDRI; blank = the asset's own", options={'SKIP_SAVE'})

    def execute(self, context):
        global _status
        if _download is not None:
            self.report({'WARNING'}, "A download is already running")
            return {'CANCELLED'}
        asset_id = self.asset_id or _picked
        if not asset_id:
            self.report({'WARNING'}, "Search and pick a result first")
            return {'CANCELLED'}
        attribute, kind = self.attribute, self.kind
        scene = context.scene
        s = scene.ambientcg_browser
        found = _find(asset_id)
        try:
            if found is None and not (attribute and kind):
                found = asset(asset_id)
                if found is None:
                    raise api.FetchError("MISSING", "%s is not on AmbientCG" % asset_id)
            kind = kind or found["kind"]
            attribute = attribute or api.attribute_for(found, s.resolution, s.format)
            if not attribute:
                raise api.FetchError("MISSING", "%s has no download" % asset_id)
            root = cache_root()
            folder = api.asset_dir(root, asset_id, attribute)
        except (api.FetchError, RuntimeError) as ex:
            _status = "Failed: %s" % ex
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        objects = list(context.selected_objects) if kind == 'MATERIAL' and s.assign else []
        opts = _options(s, kind)
        if api.is_complete(folder, kind):
            return _attach_now(self, asset_id, attribute, kind, scene, objects, opts, root)
        if not bpy.app.online_access:
            _status = "Online access is off"
            self.report({'WARNING'}, _OFFLINE)
            return {'CANCELLED'}
        _start_download(asset_id, attribute, kind, scene.name, [ob.name for ob in objects], opts, root)
        self.report({'INFO'}, "Downloading %s %s" % (asset_id, attribute))
        return {'FINISHED'}


class AMBIENTCG_OT_apply(bpy.types.Operator):
    bl_idname = "ambientcg.apply"
    bl_label = "Add AmbientCG Asset"
    bl_description = "Add a downloaded AmbientCG asset to this file"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    # Hidden: the Adjust Last Operation panel would show them as text fields.
    asset_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    attribute: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    kind: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    scene: StringProperty(description="Scene name; blank = the current one", options={'HIDDEN', 'SKIP_SAVE'})
    objects: StringProperty(description="JSON object names", options={'HIDDEN', 'SKIP_SAVE'})
    opts: StringProperty(description="JSON settings", options={'HIDDEN', 'SKIP_SAVE'})
    root: StringProperty(description="Cache folder; blank = the current one", options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        scene = bpy.data.scenes.get(self.scene) if self.scene else context.scene
        if scene is None:
            self.report({'WARNING'}, "Scene %s not found" % self.scene)
            return {'CANCELLED'}
        try:
            names = json.loads(self.objects or "[]")
            opts = json.loads(self.opts or "{}")
        except ValueError as ex:
            self.report({'WARNING'}, "Cannot add %s: %s" % (self.asset_id, ex))
            return {'CANCELLED'}
        objects = [ob for ob in (scene.objects.get(n) for n in names) if ob is not None]
        return _attach_now(self, self.asset_id, self.attribute, self.kind, scene, objects, opts, self.root or None)


class AMBIENTCG_OT_cancel(bpy.types.Operator):
    bl_idname = "ambientcg.cancel"
    bl_label = "Cancel"
    bl_description = "Stop the download; nothing is added"

    @classmethod
    def poll(cls, context):
        return downloading()

    def execute(self, context):
        cancel()
        _redraw()
        return {'FINISHED'}


class AMBIENTCG_OT_pick(bpy.types.Operator):
    bl_idname = "ambientcg.pick"
    bl_label = "Pick Result"
    bl_description = "Pick this result"

    asset_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        global _picked
        if _find(self.asset_id) is None:
            return {'CANCELLED'}
        _picked = self.asset_id
        _redraw()
        return {'FINISHED'}


class AMBIENTCG_OT_browse(bpy.types.Operator):
    """Open the results popup. wm.call_panel with keep_open draws the popover once before its region is shown, and a
    block drawn then counts as a plain menu: template_popup_confirm attaches no close callback to the X. Blender 5.2
    refreshes right after opening for that reason (UI_popover_panel_invoke, #151778); 4.2 and 4.5 do not, so do it
    here."""
    bl_idname = "ambientcg.browse"
    bl_label = "Browse Results"
    bl_description = "Show the results as a grid of thumbnails"
    bl_options = {'INTERNAL'}

    columns: IntProperty(name="Columns", default=_BROWSER_COLUMNS, min=1, max=_BROWSER_COLUMNS,
                         options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return bool(_results)

    def invoke(self, context, event):
        # From the sidebar button the popup would cover the panel: narrow it to what fits beside the sidebar and
        # move the cursor so that it opens there. The cursor stays: warping it back closes the popup in 4.2.
        # context.region is not the sidebar here: a click on a button in an overlapping sidebar runs the operator
        # with the 3D view's main region. Find the sidebar under the cursor instead.
        area = context.area
        sidebar = next((r for r in (area.regions if area is not None else ())
                        if r.type == 'UI' and r.x <= event.mouse_x < r.x + r.width
                        and r.y <= event.mouse_y < r.y + r.height), None)
        if sidebar is not None:
            x, y, self.columns = _browser_cursor(context, sidebar, event.mouse_y)
            context.window.cursor_warp(x, y)
        return self.execute(context)

    def execute(self, context):
        global _browser_columns
        _browser_columns = self.columns
        result = bpy.ops.wm.call_panel(name=AMBIENTCG_PT_browser.bl_idname, keep_open=True)
        _refresh_browser()
        return result


class AMBIENTCG_OT_close(bpy.types.Operator):
    """Run by the X in the results popup. The button is a template_popup_confirm: Blender closes the popup itself
    when it is pressed. Here the region is only forgotten; a last redraw of the closing popup can note it again, and
    _refresh_browser copes with a freed region anyway."""
    bl_idname = "ambientcg.close"
    bl_label = "Close Results"
    bl_description = "Close the results"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        global _browser
        _browser = None
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _draw_search(layout, s):
    box = layout.box()
    box.label(text="Search")
    row = box.row(align=True)
    row.prop(s, "query", text="")
    row.operator(AMBIENTCG_OT_search.bl_idname, text="", icon='VIEWZOOM')
    box.row().prop(s, "kind", expand=True)
    box.prop(s, "sort")
    status = status_text()
    if status:
        box.label(text=status, icon='SORTTIME' if _search is not None or _download is not None else 'INFO')
    if not _results:
        return None
    found = _find(_picked) or _results[0]
    box.template_icon(icon_value=_icon_of(found["id"]), scale=7.0)
    box.operator(AMBIENTCG_OT_browse.bl_idname, icon='IMAGE_BACKGROUND')
    _draw_pages(box)
    col = box.column(align=True)
    col.label(text=found["name"])
    if found["maps"]:
        col.label(text="Maps: " + ", ".join(found["maps"]))
    if found["tags"]:
        col.label(text="Tags: " + ", ".join(found["tags"][:6]))
    return found


def _draw_pages(layout):
    offset = _page.get("offset", 0)
    row = layout.row(align=True)
    sub = row.row(align=True)
    sub.enabled = offset > 0
    op = sub.operator(AMBIENTCG_OT_search.bl_idname, text="", icon='TRIA_LEFT')
    op.offset, op.paged = max(0, offset - api.PAGE), True
    row.alignment = 'CENTER'
    row.label(text="%d–%d of %d" % (offset + 1, offset + len(_results), _count))
    sub = row.row(align=True)
    sub.enabled = offset + len(_results) < _count
    op = sub.operator(AMBIENTCG_OT_search.bl_idname, text="", icon='TRIA_RIGHT')
    op.offset, op.paged = offset + api.PAGE, True


def _draw_download(layout, s, found):
    box = layout.box()
    box.label(text="Download")
    kind = found["kind"]
    row = box.row(align=True)
    row.prop(s, "resolution", text="")
    if kind == 'MATERIAL':
        row.prop(s, "format", text="")
    attribute = api.attribute_for(found, s.resolution, s.format)
    root = cache_root()
    if attribute is None:
        box.label(text="No download for this asset", icon='ERROR')
    else:
        parts = ["%s %s" % (found["id"], attribute)]
        size = found["downloads"][attribute]["size"]
        if size:
            parts.append(api.size_text(size))
        if api.is_complete(api.asset_dir(root, found["id"], attribute), kind):
            parts.append("downloaded")
        box.label(text=" · ".join(parts))
    if kind == 'MATERIAL':
        box.row().prop(s, "projection", expand=True)
        if s.projection == 'BOX':
            box.prop(s, "blend")
        box.prop(s, "scale")
        box.prop(s, "use_ao")
        box.prop(s, "assign")
    else:
        box.prop(s, "strength")
        box.prop(s, "rotation")
    if downloading():
        box.operator(AMBIENTCG_OT_cancel.bl_idname, icon='CANCEL')
    else:
        row = box.row()
        row.enabled = attribute is not None
        row.operator(AMBIENTCG_OT_fetch.bl_idname,
                     text="Add Material" if kind == 'MATERIAL' else "Set World HDRI",
                     icon='MATERIAL' if kind == 'MATERIAL' else 'WORLD')
    if root == api.fallback_root():
        box.label(text="Temp folder: the system may clear it", icon='INFO')
        box.operator(prefs.AMBIENTCG_OT_cache_folder.bl_idname, text="Pick Folder…", icon='FILE_FOLDER')
    else:
        row = box.row(align=True)
        row.label(text=os.path.basename(root) or root, icon='FILE_FOLDER')
        row.operator("wm.path_open", text="", icon='FILEBROWSER').filepath = root
        row.operator(prefs.AMBIENTCG_OT_cache_folder.bl_idname, text="Change…")


class AMBIENTCG_PT_browser(bpy.types.Panel):
    """The results popup (wm.call_panel): Blender's own enum popup takes no extra buttons and never redraws."""
    bl_label = "AmbientCG Results"
    bl_idname = "AMBIENTCG_PT_browser"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = _BROWSER_COLUMNS * 6

    def draw(self, context):
        global _browser
        _browser = context.region_popup
        # bl_ui_units_x is fixed at registration; a column of fixed width narrows the popup instead, since Blender
        # sizes the block to its buttons. Everything goes into it. A plain column would be stretched to the parent's
        # width, a left-aligned row keeps it at its own.
        row = self.layout.row()
        row.alignment = 'LEFT'
        layout = row.column()
        layout.ui_units_x = _browser_columns * 6
        split = layout.split(factor=1 / 3)   # thirds: the pager centred in the middle one, the X at the right edge
        split.label(text="")
        rest = split.split(factor=0.5)
        _draw_pages(rest)
        sub = rest.row()
        sub.alignment = 'RIGHT'
        sub.emboss = 'NONE'
        # The X closes the popup: a template_popup_confirm button does, a plain operator button in a keep_open
        # popover cannot. An empty text would leave the button out, so a space; cancel_text="" leaves out Cancel.
        sub.template_popup_confirm(AMBIENTCG_OT_close.bl_idname, text=" ", icon='X', cancel_text="")
        status = status_text()
        if status:
            layout.label(text=status, icon='SORTTIME' if _search is not None else 'INFO')
        grid = layout.grid_flow(row_major=True, columns=_browser_columns, even_columns=True, even_rows=True)
        for found in _results:
            cell = grid.column(align=True)
            cell.template_icon(icon_value=_icon_of(found["id"]), scale=5.0)
            op = cell.operator(AMBIENTCG_OT_pick.bl_idname, text=found["name"], depress=found["id"] == _picked)
            op.asset_id = found["id"]


class AMBIENTCG_PT_main(bpy.types.Panel):
    bl_label = "AmbientCG"
    bl_idname = "AMBIENTCG_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AmbientCG"

    def draw(self, context):
        col = self.layout.column()
        s = context.scene.ambientcg_browser
        found = _draw_search(col, s)
        if found is not None:
            _draw_download(col, s, found)
        elif downloading():   # started by id from a script: no result on the page to draw it under
            col.operator(AMBIENTCG_OT_cancel.bl_idname, icon='CANCEL')


_classes = (
    AmbientCGSettings,
    AMBIENTCG_OT_search,
    AMBIENTCG_OT_fetch,
    AMBIENTCG_OT_apply,
    AMBIENTCG_OT_cancel,
    AMBIENTCG_OT_pick,
    AMBIENTCG_OT_browse,
    AMBIENTCG_OT_close,
    AMBIENTCG_PT_browser,
    AMBIENTCG_PT_main,
)


def register():
    global _previews
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ambientcg_browser = PointerProperty(type=AmbientCGSettings)
    _previews = bpy.utils.previews.new()
    bpy.app.handlers.load_pre.append(_load_pre)


def unregister():
    global _results, _count, _page, _picked, _previews, _search, _thumbs, _download, _status, _browser
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    cancel()
    if _thumbs is not None:
        _thumbs.cancel()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _results, _count, _page, _picked, _status = [], 0, {}, "", ""
    _search = _thumbs = _download = _browser = None
    _roots.clear()
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
    del bpy.types.Scene.ambientcg_browser
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
