"""Node building of AmbientCG Browser inside Blender, from generated dummy maps: no .blend, no network.

Run headless with an isolated profile so your own preferences are never touched:
  $env:BLENDER_USER_RESOURCES="<scratch>\\profile"
  & blender --background --factory-startup --python-exit-code 1 --python tests\\test_nodes.py [-- <repo root>]

Prints `NODES PASSED` (exit 0) or `NODES FAILED` (exit 1). Under a plain `python -m unittest discover` the
module is skipped: it needs bpy.
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

try:
    import bpy
except ImportError:
    raise unittest.SkipTest("needs Blender (bpy)")

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ROOT = os.path.abspath(argv[0] if argv else os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MATERIAL = ("Dummy001", "1K-PNG")
HDRI = ("DummyHDRI001", "1K")
ROLES = {"Color": (0.8, 0.4, 0.2), "Roughness": (0.5, 0.5, 0.5), "NormalGL": (0.5, 0.5, 1.0),
         "NormalDX": (0.5, 0.5, 1.0), "Displacement": (0.5, 0.5, 0.5), "AmbientOcclusion": (1.0, 1.0, 1.0),
         "Metalness": (0.0, 0.0, 0.0), "Opacity": (1.0, 1.0, 1.0), "Emission": (0.0, 0.0, 0.0)}

pkg = ambientcg = api = None
root = ""


def _save(path, color, file_format, float_buffer=False):
    img = bpy.data.images.new("dummy", 4, 4, float_buffer=float_buffer)
    img.pixels = list(color + (1.0,)) * 16
    img.filepath_raw = path
    img.file_format = file_format
    img.save()
    bpy.data.images.remove(img)


def _dummy_cache():
    """A cache folder that already holds a material and an HDRI, as a download would leave them."""
    folder = api.asset_dir(root, *MATERIAL)
    os.makedirs(folder)
    for role, color in ROLES.items():
        _save(os.path.join(folder, "%s_%s_%s.png" % (MATERIAL + (role,))), color, 'PNG')
    folder = api.asset_dir(root, *HDRI)
    os.makedirs(folder)
    _save(os.path.join(folder, "%s_%s_HDR.exr" % HDRI), (0.2, 0.4, 0.8), 'OPEN_EXR', float_buffer=True)
    _save(os.path.join(folder, "%s_%s_TONEMAPPED.png" % HDRI), (0.2, 0.4, 0.8), 'PNG')


def setUpModule():
    global pkg, ambientcg, api, root
    sys.path.insert(0, ROOT)
    pkg = importlib.import_module("ambientcg_browser")
    ambientcg, api = pkg.ambientcg, pkg.api
    pkg.register()
    root = tempfile.mkdtemp(prefix="ambientcg-browser-nodes-")
    _dummy_cache()


def tearDownModule():
    pkg.unregister()
    shutil.rmtree(root, ignore_errors=True)


def node(block, name):
    return block.node_tree.nodes.get(ambientcg.PREFIX + name)


def source(socket):
    """The node feeding an input socket, None when it is not linked. Compare nodes with ==: bpy makes a new
    wrapper on every access."""
    return socket.links[0].from_node if socket.is_linked else None


class Material(unittest.TestCase):
    def setUp(self):
        self.maps = api.maps_in(api.asset_dir(root, *MATERIAL))

    def new(self):
        mat = bpy.data.materials.new("Test")
        self.addCleanup(bpy.data.materials.remove, mat)
        return mat

    def bsdf(self, mat):
        found = ambientcg.principled_of(mat.node_tree)
        self.assertIsNotNone(found)
        return found

    def test_dummy_maps_are_complete(self):
        self.assertTrue(api.is_complete(api.asset_dir(root, *MATERIAL), "MATERIAL"))
        self.assertEqual(sorted(self.maps), ["ao", "color", "displacement", "emission", "metalness", "normal",
                                             "normal_dx", "opacity", "roughness"])

    def test_every_map_is_wired(self):
        mat = ambientcg.build_material(self.new(), self.maps)
        bsdf = self.bsdf(mat)
        out = mat.node_tree.get_output_node('ALL')
        self.assertEqual(source(out.inputs["Surface"]), bsdf)

        mix = source(bsdf.inputs["Base Color"])
        self.assertEqual((mix.name, mix.bl_idname, mix.data_type, mix.blend_type),
                         ("AmbientCG AO Multiply", 'ShaderNodeMix', 'RGBA', 'MULTIPLY'))
        self.assertEqual({link.from_node.name for s in mix.inputs for link in s.links},
                         {"AmbientCG Color", "AmbientCG Ambient Occlusion"})
        for socket, name in (("Roughness", "Roughness"), ("Metallic", "Metalness"), ("Alpha", "Opacity"),
                             ("Emission Color", "Emission")):
            self.assertEqual(source(bsdf.inputs[socket]).name, "AmbientCG " + name, socket)
        normal = source(bsdf.inputs["Normal"])
        self.assertEqual(normal.bl_idname, 'ShaderNodeNormalMap')
        self.assertEqual(os.path.basename(source(normal.inputs["Color"]).image.filepath),
                         "%s_%s_NormalGL.png" % MATERIAL)
        disp = source(out.inputs["Displacement"])
        self.assertEqual(disp.bl_idname, 'ShaderNodeDisplacement')
        self.assertEqual(source(disp.inputs["Height"]).name, "AmbientCG Height")
        self.assertEqual(mat.displacement_method, 'BUMP')
        self.assertEqual(bsdf.inputs["Emission Strength"].default_value, 1.0)

        self.assertEqual(node(mat, "Color").image.colorspace_settings.name, 'sRGB')
        for name in ("Roughness", "Normal", "Height", "Ambient Occlusion", "Metalness", "Opacity"):
            self.assertEqual(node(mat, name).image.colorspace_settings.name, 'Non-Color', name)

    def test_projection(self):
        mat = ambientcg.build_material(self.new(), self.maps, projection="FLAT", scale=3.0)
        mapping = node(mat, "Mapping")
        self.assertEqual(mapping.inputs["Vector"].links[0].from_socket.name, "UV")
        self.assertEqual(tuple(mapping.inputs["Scale"].default_value), (3.0, 3.0, 3.0))
        self.assertEqual(node(mat, "Color").projection, 'FLAT')
        self.assertEqual(source(node(mat, "Color").inputs["Vector"]), mapping)

        ambientcg.build_material(mat, self.maps, projection="BOX", blend=0.5)
        self.assertEqual(node(mat, "Mapping").inputs["Vector"].links[0].from_socket.name, "Object")
        self.assertEqual((node(mat, "Color").projection, node(mat, "Color").projection_blend), ('BOX', 0.5))
        for projection in ("SPHERE", "TUBE"):
            ambientcg.build_material(mat, self.maps, projection=projection)
            self.assertEqual(node(mat, "Roughness").projection, projection)

    def test_without_ao_the_color_goes_straight_in(self):
        mat = ambientcg.build_material(self.new(), self.maps, use_ao=False)
        self.assertEqual(source(self.bsdf(mat).inputs["Base Color"]).name, "AmbientCG Color")
        self.assertIsNone(node(mat, "AO Multiply"))
        mat = ambientcg.build_material(self.new(), {"color": self.maps["color"]})
        self.assertEqual(sorted(n.name for n in mat.node_tree.nodes if n.name.startswith(ambientcg.PREFIX)),
                         ["AmbientCG Color", "AmbientCG Coordinates", "AmbientCG Mapping"])

    def test_a_rebuild_keeps_the_bsdf_the_output_and_foreign_nodes(self):
        mat = ambientcg.build_material(self.new(), self.maps)
        nt = mat.node_tree
        bsdf, out = self.bsdf(mat), nt.get_output_node('ALL')
        bsdf.name, out.name = "Kept BSDF", "Kept Output"
        nt.nodes.new('ShaderNodeValue').name = "Mine"
        count = len(nt.nodes)
        ambientcg.build_material(mat, self.maps)
        self.assertEqual(len(nt.nodes), count)
        self.assertEqual({"Kept BSDF", "Kept Output", "Mine"} - {n.name for n in nt.nodes}, set())
        self.assertEqual(len([n for n in nt.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled']), 1)

    def test_create_material_reuses_the_marked_one(self):
        mat = ambientcg.create_material(*MATERIAL, root=root)
        self.addCleanup(bpy.data.materials.remove, mat)
        self.assertEqual(mat.name, "AmbientCG %s %s" % MATERIAL)
        self.assertEqual(ambientcg.marked(mat), {
            "v": 1, "asset": MATERIAL[0], "attribute": MATERIAL[1], "kind": "MATERIAL",
            "opts": {"projection": "FLAT", "blend": 0.2, "scale": 1.0, "use_ao": True}})
        self.assertEqual(ambientcg.MARK_KEY, "ambientcg_browser")
        self.assertIn(ambientcg.MARK_KEY, mat.keys())
        self.assertNotIn(ambientcg.LEGACY_KEY, mat.keys())   # the older key is read, never written
        count = len(bpy.data.materials)
        color = node(mat, "Color")
        self.assertEqual(ambientcg.create_material(*MATERIAL, root=root), mat)
        self.assertEqual(ambientcg.create_material(MATERIAL[0], root=root), mat)   # any attribute of that asset
        self.assertEqual(node(mat, "Color"), color)
        # Other options rebuild the nodes of the same material.
        self.assertEqual(ambientcg.create_material(*MATERIAL, projection="BOX", root=root), mat)
        self.assertEqual(node(mat, "Color").projection, 'BOX')
        self.assertEqual(ambientcg.marked(mat)["opts"]["projection"], "BOX")
        self.assertEqual(len(bpy.data.materials), count)

    def test_a_material_of_the_older_panel_is_reused(self):
        mat = self.new()
        opts = {"projection": "FLAT", "blend": 0.2, "scale": 1.0, "use_ao": True}
        ambientcg.build_material(mat, self.maps)
        mat[ambientcg.LEGACY_KEY] = json.dumps({"v": 1, "asset": MATERIAL[0], "attribute": MATERIAL[1],
                                                "kind": "MATERIAL", "opts": opts})
        self.assertEqual(ambientcg.material_for(*MATERIAL), mat)
        self.assertEqual(ambientcg.create_material(*MATERIAL, root=root), mat)
        self.assertNotIn(ambientcg.MARK_KEY, mat.keys())   # untouched: the options are the same
        ambientcg.create_material(*MATERIAL, scale=2.0, root=root)
        self.assertEqual(ambientcg.marked(mat)["opts"]["scale"], 2.0)   # rebuilt: the new key wins

    def test_adding_again_brings_back_maps_that_went_missing(self):
        """The temp folder can be cleared while a .blend still uses its files."""
        folder = api.asset_dir(root, *MATERIAL)
        mat = ambientcg.create_material(*MATERIAL, root=root)
        self.addCleanup(bpy.data.materials.remove, mat)
        img = node(mat, "Color").image
        self.assertEqual(tuple(img.size), (4, 4))
        parked = folder + ".parked"
        os.rename(folder, parked)

        def download(*_args):
            os.rename(parked, folder)
            return folder

        try:
            img.reload()   # what reopening the file finds: no image
            with mock.patch.object(ambientcg, "fetch", side_effect=download) as fetch:
                self.assertEqual(ambientcg.create_material(*MATERIAL, root=root), mat)
            self.assertEqual(fetch.call_count, 1)
        finally:
            if os.path.isdir(parked):
                os.rename(parked, folder)
        self.assertEqual(node(mat, "Color").image, img)
        self.assertEqual(tuple(img.size), (4, 4))

    def test_unmarked_blocks_are_not_ours(self):
        mat = self.new()
        self.assertIsNone(ambientcg.marked(mat))
        mat[ambientcg.MARK_KEY] = "not json"
        self.assertIsNone(ambientcg.marked(mat))
        mat[ambientcg.MARK_KEY] = json.dumps({"v": 99, "asset": MATERIAL[0]})
        self.assertIsNone(ambientcg.marked(mat))
        self.assertIsNone(ambientcg.material_for("Nothing001"))

    def test_assign(self):
        mat = self.new()
        mesh = bpy.data.meshes.new("Test")
        with_slot, without_slot = bpy.data.objects.new("A", mesh.copy()), bpy.data.objects.new("B", mesh)
        empty = bpy.data.objects.new("Empty", None)
        with_slot.data.materials.append(bpy.data.materials.new("Old"))
        for ob in (with_slot, without_slot, empty):
            bpy.context.scene.collection.objects.link(ob)
            self.addCleanup(bpy.data.objects.remove, ob)
        self.assertEqual(ambientcg.assign(mat, [with_slot, without_slot, empty]), [with_slot, without_slot])
        self.assertEqual(with_slot.active_material, mat)
        self.assertEqual(without_slot.active_material, mat)


class World(unittest.TestCase):
    def test_set_world(self):
        scene = bpy.context.scene
        before = scene.world
        world = ambientcg.set_world(*HDRI, strength=2.0, rotation=0.5, scene=scene, root=root)
        self.addCleanup(bpy.data.worlds.remove, world)
        self.assertEqual(scene.world, world)
        self.assertNotEqual(world, before)
        self.assertEqual(world.name, "AmbientCG " + HDRI[0])
        self.assertEqual(ambientcg.marked(world), {"v": 1, "asset": HDRI[0], "attribute": HDRI[1], "kind": "HDRI",
                                                    "opts": None})
        env, background, output = node(world, "Environment"), node(world, "Background"), node(world, "Output")
        self.assertEqual(os.path.basename(env.image.filepath), "%s_%s_HDR.exr" % HDRI)
        self.assertEqual(source(background.inputs["Color"]), env)
        self.assertEqual(source(output.inputs["Surface"]), background)
        self.assertEqual(source(env.inputs["Vector"]), node(world, "Mapping"))
        self.assertEqual(background.inputs["Strength"].default_value, 2.0)
        self.assertAlmostEqual(node(world, "Mapping").inputs["Rotation"].default_value[2], 0.5, places=5)

        # Again: the same world, only its strength and rotation move.
        self.assertEqual(ambientcg.set_world(*HDRI, strength=0.5, rotation=0.0, scene=scene, root=root), world)
        self.assertEqual(node(world, "Environment"), env)
        self.assertEqual(background.inputs["Strength"].default_value, 0.5)

        # The panel sliders push into the scene's world only while it is ours.
        scene.ambientcg_browser.strength = 3.0
        self.assertEqual(background.inputs["Strength"].default_value, 3.0)
        scene.world = before
        scene.ambientcg_browser.strength = 4.0
        self.assertEqual(background.inputs["Strength"].default_value, 3.0)


class Operators(unittest.TestCase):
    def test_apply_adds_a_downloaded_material_to_the_named_objects(self):
        scene = bpy.context.scene
        ob = bpy.data.objects.new("Target", bpy.data.meshes.new("Target"))
        scene.collection.objects.link(ob)
        self.addCleanup(bpy.data.objects.remove, ob)
        result = bpy.ops.ambientcg.apply(
            asset_id=MATERIAL[0], attribute=MATERIAL[1], kind="MATERIAL", scene=scene.name,
            objects=json.dumps([ob.name, "Gone"]), opts=json.dumps({"projection": "TUBE", "scale": 2.0}), root=root)
        self.assertEqual(result, {'FINISHED'})
        mat = ob.active_material
        self.addCleanup(bpy.data.materials.remove, mat)
        self.assertEqual(ambientcg.marked(mat)["opts"]["projection"], "TUBE")
        self.assertEqual(ambientcg.status_text(), "Added %s %s" % MATERIAL)

    def test_apply_sets_a_downloaded_hdri_as_the_world(self):
        scene = bpy.context.scene
        result = bpy.ops.ambientcg.apply(asset_id=HDRI[0], attribute=HDRI[1], kind="HDRI", scene=scene.name,
                                         opts=json.dumps({"strength": 1.5, "rotation": 0.0}), root=root)
        self.assertEqual(result, {'FINISHED'})
        self.addCleanup(bpy.data.worlds.remove, scene.world)
        self.assertEqual(ambientcg.marked(scene.world)["asset"], HDRI[0])

    def test_apply_reports_what_is_not_in_the_cache(self):
        self.assertFalse(bpy.app.online_access, "run this suite without --online-mode")
        result = bpy.ops.ambientcg.apply(asset_id="Missing001", attribute="1K-JPG", kind="MATERIAL", root=root)
        self.assertEqual(result, {'CANCELLED'})
        self.assertTrue(ambientcg.status_text().startswith("Failed: "), ambientcg.status_text())

    def test_the_timer_survives_an_idle_tick(self):
        self.assertFalse(ambientcg.running())
        self.assertIsNone(ambientcg._tick())


if __name__ == "__main__":
    outcome = unittest.main(argv=["test_nodes"], exit=False, verbosity=2).result
    print("NODES PASSED" if outcome.wasSuccessful() and outcome.testsRun else "NODES FAILED")
    sys.stdout.flush()
    if not (outcome.wasSuccessful() and outcome.testsRun):
        sys.exit(1)
