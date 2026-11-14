"""Preferences of AmbientCG Browser: the cache folder the downloads, thumbnails and saved searches go into."""

import os

import bpy
from bpy.props import BoolProperty, StringProperty

ISSUES_URL = "https://github.com/jsiovn/ambientcg-browser/issues"
AMBIENTCG_URL = "https://ambientcg.com"


def redraw():
    wm = bpy.context.window_manager
    for win in (wm.windows if wm is not None else ()):
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _preferences(context=None):
    """The add-on preferences, None when the package is not an enabled add-on (headless runs import it from
    sys.path)."""
    addon = (context or bpy.context).preferences.addons.get(__package__)
    return getattr(addon, "preferences", None)


def cache_folder(context=None):
    """The picked cache folder ('' = none picked: the temp folder is used)."""
    p = _preferences(context)
    return os.path.normpath(bpy.path.abspath(p.cache_dir)) if p is not None and p.cache_dir else ""


def folder_state(folder):
    """None when folder can be used, else the hint to show."""
    if not folder:
        return "No folder picked"
    if not os.path.isdir(folder):
        return "Folder not found"
    return None


def _remember(op, context, folder):
    """Store folder ('' = the temp folder) in the preferences and save them; the operator's return."""
    p = _preferences(context)
    if p is None:
        op.report({'WARNING'}, "AmbientCG Browser is not an enabled add-on: cannot remember the folder")
        return {'CANCELLED'}
    p.cache_dir = folder
    where = folder or "the temp folder"
    try:
        bpy.ops.wm.save_userpref()
    except RuntimeError:
        op.report({'WARNING'}, "Downloads go to %s, for this session only (the preferences could not be saved)"
                  % where)
    else:
        op.report({'INFO'}, "Downloads go to %s" % where)
    redraw()
    return {'FINISHED'}


class AMBIENTCG_OT_cache_folder(bpy.types.Operator):
    bl_idname = "ambientcg.cache_folder"
    bl_label = "Pick Folder"
    bl_description = ("Pick the folder the downloads, thumbnails and saved searches go into; it is remembered in "
                      "the preferences")

    directory: StringProperty(subtype='DIR_PATH')
    filter_folder: BoolProperty(default=True, options={'HIDDEN'})

    def invoke(self, context, event):
        folder = cache_folder(context)
        if folder and os.path.isdir(folder):
            self.directory = folder + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        folder = os.path.normpath(bpy.path.abspath(self.directory)) if self.directory else ""
        if not folder or not os.path.isdir(folder):
            self.report({'WARNING'}, "Folder not found: %s" % (folder or "(none)"))
            return {'CANCELLED'}
        return _remember(self, context, folder)


class AMBIENTCG_OT_cache_reset(bpy.types.Operator):
    bl_idname = "ambientcg.cache_reset"
    bl_label = "Use Temp Folder"
    bl_description = "Forget the picked folder: downloads go to the system temp folder again. Nothing is deleted"

    @classmethod
    def poll(cls, context):
        return bool(cache_folder(context))

    def execute(self, context):
        return _remember(self, context, "")


class AmbientCGPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    cache_dir: StringProperty(
        name="Cache Folder",
        description="Folder of the downloads, thumbnails and saved searches; blank = the system temp folder",
        subtype='DIR_PATH', update=lambda _self, _context: redraw(),
    )

    def draw(self, context):
        from . import update   # registered after this module

        col = self.layout.column()
        row = col.row(align=True)
        row.prop(self, "cache_dir")
        row.operator(AMBIENTCG_OT_cache_reset.bl_idname, text="", icon='X')
        folder = cache_folder(context)
        if not folder:
            col.label(text="Downloads go to the system temp folder, which the system may clear", icon='INFO')
        elif folder_state(folder) is not None:
            col.label(text="Folder not found: downloads go to the system temp folder", icon='ERROR')
        if not update.from_blender_org(context):
            col.separator()
            update.draw(col)
        col.separator()
        row = col.row(align=True)
        row.operator("wm.url_open", text="Install Page", icon='URL').url = update.INSTALL_PAGE
        row.operator("wm.url_open", text="Report a Bug", icon='URL').url = ISSUES_URL
        row.operator("wm.url_open", text="ambientCG", icon='URL').url = AMBIENTCG_URL


_classes = (AMBIENTCG_OT_cache_folder, AMBIENTCG_OT_cache_reset, AmbientCGPreferences)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
