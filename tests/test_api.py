"""Tests of ambientcg_browser/api.py, which has no bpy: no Blender and no network needed.

    python -I -m unittest discover tests

The module is loaded by its path: importing the package would import bpy.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import tempfile
import time
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "ambientcg_browser")
DATA = os.path.join(ROOT, "tests", "data")

_spec = importlib.util.spec_from_file_location("ambientcg_browser_api", os.path.join(PACKAGE, "api.py"))
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


def saved(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def make_zip(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"x")
    return path


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ambientcg-browser-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class Identity(unittest.TestCase):
    def test_user_agent_names_the_add_on_and_its_version(self):
        with open(os.path.join(PACKAGE, "blender_manifest.toml"), encoding="utf-8") as f:
            version = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M).group(1)
        self.assertEqual(api.USER_AGENT,
                         "AmbientCG-Browser-Blender/%s (+https://github.com/jsiovn/ambientcg-browser)" % version)

    def test_urls(self):
        url = api.search_url("old wood", "HDRI", "Latest", 9999, -5)
        self.assertTrue(url.startswith(api.API_URL + "?"))
        self.assertIn("q=old+wood", url)
        self.assertIn("type=HDRI", url)
        self.assertIn("limit=%d" % api.MAX_LIMIT, url)
        self.assertIn("offset=0", url)
        self.assertEqual(api.download_url("Rock035", "1K-JPG"), api.GET_URL + "?file=Rock035_1K-JPG.zip")


class ParseAssets(unittest.TestCase):
    def test_saved_material_answer(self):
        data = saved("search_rock.json")
        assets = api.parse_assets(data)
        self.assertEqual([a["id"] for a in assets], ["Ground111", "Rock064"])
        self.assertEqual(api.parse_count(data), 76)
        first = assets[0]
        self.assertEqual(first["kind"], "MATERIAL")
        self.assertEqual(sorted(first["downloads"]),
                         ["1K-JPG", "1K-PNG", "2K-JPG", "2K-PNG", "4K-JPG", "4K-PNG", "8K-JPG", "8K-PNG"])
        self.assertEqual(first["downloads"]["1K-JPG"], {
            "url": "https://ambientcg.com/get?file=Ground111_1K-JPG.zip", "file": "Ground111_1K-JPG.zip",
            "size": 9761806,
        })
        self.assertTrue(first["thumb"].endswith("/256-PNG/Ground111.png"))
        self.assertIn("color", first["maps"])
        self.assertIn("brick", first["tags"])

    def test_saved_hdri_answer(self):
        assets = api.parse_assets(saved("search_hdri.json"))
        self.assertEqual([(a["id"], a["kind"]) for a in assets], [("EveningSkyHDRI049B", "HDRI")])
        self.assertEqual(sorted(assets[0]["downloads"]), ["12K", "16K", "1K", "2K", "4K", "8K"])

    def test_unusable_entries_are_skipped_and_never_raise(self):
        entry = {"dataType": "Material", "downloadFolders": {"default": {"downloadFiletypeCategories": {"zip": {
            "downloads": [{"attribute": "1K-JPG"}, {"attribute": "../1K"}, {"attribute": 3}, "junk"]}}}}}
        data = {"foundAssets": [
            dict(entry, assetId="Good001"),
            dict(entry, assetId="../Evil"),         # would become a folder name
            dict(entry, assetId="Bad Name"),
            dict(entry, assetId=7),
            dict(entry, assetId="Model001", dataType="3DModel"),
            {"assetId": "Bare001", "dataType": "HDRI"},
            "junk", None,
        ]}
        assets = api.parse_assets(data)
        self.assertEqual([a["id"] for a in assets], ["Good001", "Bare001"])
        self.assertEqual(list(assets[0]["downloads"]), ["1K-JPG"])
        self.assertEqual(assets[0]["downloads"]["1K-JPG"], {
            "url": api.download_url("Good001", "1K-JPG"), "file": "Good001_1K-JPG.zip", "size": 0})
        self.assertEqual(assets[1], {"id": "Bare001", "name": "Bare001", "kind": "HDRI", "tags": [], "maps": [],
                                     "thumb": "", "downloads": {}})
        for junk in (None, [], "text", {"foundAssets": "no"}, {"foundAssets": None}):
            self.assertEqual(api.parse_assets(junk), [])
            self.assertEqual(api.parse_count(junk), 0)


class AttributeFor(unittest.TestCase):
    def asset(self, kind, *attributes):
        return {"kind": kind, "downloads": {a: {} for a in attributes}}

    def test_exact(self):
        material = self.asset("MATERIAL", "1K-JPG", "2K-JPG", "2K-PNG", "4K-JPG")
        self.assertEqual(api.attribute_for(material, "2K", "JPG"), "2K-JPG")
        self.assertEqual(api.attribute_for(material, "2K", "PNG"), "2K-PNG")
        self.assertEqual(api.attribute_for(self.asset("HDRI", "1K", "4K"), "4K"), "4K")

    def test_largest_under_it_in_that_format(self):
        material = self.asset("MATERIAL", "1K-JPG", "2K-JPG", "2K-PNG", "8K-PNG")
        self.assertEqual(api.attribute_for(material, "4K", "JPG"), "2K-JPG")
        self.assertEqual(api.attribute_for(material, "16K", "PNG"), "8K-PNG")
        self.assertEqual(api.attribute_for(self.asset("HDRI", "1K", "2K", "8K"), "4K"), "2K")

    def test_else_the_smallest_there_is(self):
        self.assertEqual(api.attribute_for(self.asset("MATERIAL", "4K-JPG", "8K-JPG", "2K-PNG"), "1K", "JPG"), "4K-JPG")
        self.assertEqual(api.attribute_for(self.asset("MATERIAL", "4K-PNG", "2K-PNG"), "1K", "JPG"), "2K-PNG")
        self.assertEqual(api.attribute_for(self.asset("HDRI", "8K", "4K"), "1K"), "4K")

    def test_none_without_a_download(self):
        self.assertIsNone(api.attribute_for(self.asset("MATERIAL"), "2K", "JPG"))
        self.assertIsNone(api.attribute_for(self.asset("MATERIAL", "Preview", "LOD0"), "2K", "JPG"))

    def test_saved_answer(self):
        rock = api.parse_assets(saved("search_rock.json"))[1]
        self.assertEqual(api.attribute_for(rock, "2K", "JPG"), "2K-JPG")
        self.assertEqual(api.attribute_for(rock, "16K", "PNG"), "8K-PNG")


class Folders(TempDir):
    def test_asset_dir_checks_the_id(self):
        self.assertEqual(api.asset_dir(self.tmp, "Rock035", "1K-JPG"), os.path.join(self.tmp, "Rock035_1K-JPG"))
        for asset_id, attribute in (("../Rock035", "1K-JPG"), ("Rock035", "..\\1K"), ("Rock/035", "1K-JPG"),
                                    ("C:Rock", "1K-JPG"), ("", "1K-JPG"), ("Rock035", ""), (None, "1K-JPG"),
                                    ("Rock035", None), ("Rock 035", "1K-JPG")):
            with self.assertRaises(api.FetchError) as raised:
                api.asset_dir(self.tmp, asset_id, attribute)
            self.assertEqual(raised.exception.code, "ID")

    def test_cache_root_is_the_picked_folder_itself(self):
        picked = os.path.join(self.tmp, "picked")
        os.makedirs(picked)
        self.assertEqual(api.cache_root(picked), picked)
        self.assertEqual(os.listdir(picked), [])

    def test_cache_root_falls_back_to_the_temp_folder(self):
        with mock.patch.object(api.tempfile, "gettempdir", return_value=self.tmp):
            fallback = os.path.join(self.tmp, "ambientcg-browser")
            self.assertEqual(api.fallback_root(), fallback)
            self.assertEqual(api.cache_root(""), fallback)
            self.assertTrue(os.path.isdir(fallback))
            with mock.patch.object(api, "_writable", side_effect=lambda folder: folder == fallback):
                self.assertEqual(api.cache_root(os.path.join(self.tmp, "read-only")), fallback)

    def test_maps_in(self):
        folder = os.path.join(self.tmp, "Rock035_1K-JPG")
        os.makedirs(folder)
        for name in ("Rock035_1K-JPG_Color.jpg", "Rock035_1K-JPG_NormalGL.jpg", "Rock035_1K-JPG_NormalDX.jpg",
                     "Rock035_1K-JPG_AmbientOcclusion.JPG", "Rock035_1K-JPG_Unknown.jpg", "Rock035.png",
                     "Other_1K-JPG_Color.jpg", "Rock035_1K-JPG_Color.txt"):
            open(os.path.join(folder, name), "wb").close()
        self.assertFalse(api.is_complete(os.path.join(self.tmp, "missing"), "MATERIAL"))
        self.assertTrue(api.is_complete(folder, "MATERIAL"))
        self.assertFalse(api.is_complete(folder, "HDRI"))
        self.assertEqual({role: os.path.basename(path) for role, path in api.maps_in(folder).items()}, {
            "color": "Rock035_1K-JPG_Color.jpg", "normal": "Rock035_1K-JPG_NormalGL.jpg",
            "normal_dx": "Rock035_1K-JPG_NormalDX.jpg", "ao": "Rock035_1K-JPG_AmbientOcclusion.JPG",
        })


class Zips(TempDir):
    NAMES = ("Rock035_1K-JPG_Color.jpg", "Rock035_1K-JPG_Roughness.PNG", "Rock035_1K-JPG.blend", "Rock035.usdc",
             "Rock035.mtlx", "readme.txt", "../evil.jpg", "..\\evil2.jpg", "sub/inner.png", "sub\\inner2.png",
             "/abs.png", "C:evil.png", "C:\\Windows\\evil.png", "a..b.png", "folder/")

    def test_safe_members_keeps_plain_image_names_only(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name in self.NAMES:
                zf.writestr(name, b"x")
        with zipfile.ZipFile(buf) as zf:
            self.assertEqual(api.safe_members(zf), ["Rock035_1K-JPG_Color.jpg", "Rock035_1K-JPG_Roughness.PNG"])

    def test_extract_lands_nothing_outside_the_folder(self):
        inner = os.path.join(self.tmp, "cache")
        os.makedirs(inner)
        zip_path = make_zip(os.path.join(inner, "Rock035_1K-JPG.zip"), self.NAMES)
        folder = api.extract(zip_path, os.path.join(inner, "Rock035_1K-JPG"))
        self.assertEqual(sorted(os.listdir(folder)), ["Rock035_1K-JPG_Color.jpg", "Rock035_1K-JPG_Roughness.PNG"])
        self.assertEqual(sorted(os.listdir(inner)), ["Rock035_1K-JPG", "Rock035_1K-JPG.zip"])
        self.assertEqual(os.listdir(self.tmp), ["cache"])

    def test_extract_replaces_an_older_folder(self):
        folder = os.path.join(self.tmp, "Rock035_1K-JPG")
        os.makedirs(folder)
        open(os.path.join(folder, "stale.jpg"), "wb").close()
        api.extract(make_zip(os.path.join(self.tmp, "a.zip"), ["Rock035_1K-JPG_Color.jpg"]), folder)
        self.assertEqual(os.listdir(folder), ["Rock035_1K-JPG_Color.jpg"])

    def test_a_damaged_zip_is_removed(self):
        zip_path = os.path.join(self.tmp, "bad.zip")
        with open(zip_path, "wb") as f:
            f.write(b"not a zip")
        folder = os.path.join(self.tmp, "Rock035_1K-JPG")
        with self.assertRaises(api.FetchError) as raised:
            api.extract(zip_path, folder)
        self.assertEqual(raised.exception.code, "ZIP")
        self.assertEqual(os.listdir(self.tmp), [])

    def fake_download(self, names):
        def download(url, dest, cancelled=None, progress=None):
            self.urls.append(url)
            return make_zip(dest, names)
        self.urls = []
        return mock.patch.object(api, "download_file", side_effect=download)

    def test_fetch_asset_downloads_once(self):
        with self.fake_download(["Rock035_1K-JPG_Color.jpg", "Rock035_1K-JPG_Roughness.jpg"]):
            folder = api.fetch_asset("Rock035", "1K-JPG", "MATERIAL", self.tmp)
            self.assertEqual(api.fetch_asset("Rock035", "1K-JPG", "MATERIAL", self.tmp), folder)
        self.assertEqual(self.urls, [api.download_url("Rock035", "1K-JPG")])
        self.assertEqual(os.listdir(self.tmp), ["Rock035_1K-JPG"])
        self.assertEqual(sorted(api.maps_in(folder)), ["color", "roughness"])

    def test_fetch_asset_without_usable_maps(self):
        with self.fake_download(["Rock035_1K-JPG_Roughness.jpg"]):
            with self.assertRaises(api.FetchError) as raised:
                api.fetch_asset("Rock035", "1K-JPG", "MATERIAL", self.tmp)
        self.assertEqual(raised.exception.code, "MAPS")
        self.assertEqual(os.listdir(self.tmp), [])


class FakeResponse(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


class Download(TempDir):
    def test_download_file_streams_and_reports(self):
        body = b"abc" * 1000
        seen = []
        dest = os.path.join(self.tmp, "a.zip")
        response = FakeResponse(body)
        response.headers = {"Content-Length": str(len(body))}
        with mock.patch.object(api, "_open", return_value=response):
            api.download_file("https://example.invalid/a.zip", dest,
                              progress=lambda done, total: seen.append((done, total)))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), body)
        self.assertEqual(seen[-1], (len(body), len(body)))
        self.assertEqual(os.listdir(self.tmp), ["a.zip"])

    def test_cancel_leaves_no_file(self):
        with mock.patch.object(api, "_open", return_value=FakeResponse(b"abc")):
            with self.assertRaises(api.FetchError) as raised:
                api.download_file("https://example.invalid/a.zip", os.path.join(self.tmp, "a.zip"),
                                  cancelled=lambda: True)
        self.assertEqual(raised.exception.code, "CANCELLED")
        self.assertEqual(os.listdir(self.tmp), [])

    def test_a_short_answer_is_an_error(self):
        response = FakeResponse(b"abc")
        response.headers = {"Content-Length": "10"}
        with mock.patch.object(api, "_open", return_value=response):
            with self.assertRaises(api.FetchError) as raised:
                api.download_file("https://example.invalid/a.zip", os.path.join(self.tmp, "a.zip"))
        self.assertEqual(raised.exception.code, "HTTP")
        self.assertEqual(os.listdir(self.tmp), [])


class Cache(TempDir):
    URL = api.search_url("rock")

    def setUp(self):
        super().setUp()
        self.answer = saved("search_rock.json")
        self.asked = []
        self.failing = False
        patcher = mock.patch.object(api, "fetch_json", side_effect=self.fetch_json)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch_json(self, url):
        self.asked.append(url)
        if self.failing:
            raise api.FetchError("HTTP", detail=url)
        return self.answer

    def age(self, seconds):
        path = api.cache_path(self.tmp, self.URL)
        then = time.time() - seconds
        os.utime(path, (then, then))

    def test_a_fresh_answer_is_not_asked_again(self):
        self.assertEqual(api.cached_json(self.URL, self.tmp), (self.answer, False))
        self.assertEqual(api.cached_json(self.URL, self.tmp), (self.answer, False))
        self.assertEqual(len(self.asked), 1)
        self.assertTrue(os.path.isfile(api.cache_path(self.tmp, self.URL)))

    def test_a_file_dated_just_ahead_is_fresh(self):
        # The clock that dates files is finer than time.time(), which ticks every 16 ms on Windows before
        # Python 3.13: a file just written can be a few milliseconds ahead.
        api.cached_json(self.URL, self.tmp)
        self.age(-0.5)
        self.assertEqual(api.cached_json(self.URL, self.tmp, online=False), (self.answer, False))
        self.assertEqual(api.cached_json(self.URL, self.tmp), (self.answer, False))
        self.assertEqual(len(self.asked), 1)
        self.age(-3600)   # a clock that was set back: not trusted, asked again
        self.assertEqual(api.cached_json(self.URL, self.tmp), (self.answer, False))
        self.assertEqual(len(self.asked), 2)

    def test_refresh_asks_first(self):
        api.cached_json(self.URL, self.tmp)
        api.cached_json(self.URL, self.tmp, refresh=True)
        self.assertEqual(len(self.asked), 2)

    def test_after_the_ttl_it_is_asked_again(self):
        api.cached_json(self.URL, self.tmp)
        self.age(api.CACHE_TTL + 60)
        self.assertEqual(api.cached_json(self.URL, self.tmp), (self.answer, False))
        self.assertEqual(len(self.asked), 2)

    def test_offline_takes_the_saved_answer_whatever_its_age(self):
        api.cached_json(self.URL, self.tmp)
        self.assertEqual(api.cached_json(self.URL, self.tmp, online=False), (self.answer, False))
        self.age(api.CACHE_TTL + 60)
        self.assertEqual(api.cached_json(self.URL, self.tmp, online=False), (self.answer, True))
        self.assertEqual(api.cached_json(self.URL, self.tmp, online=False, refresh=True), (self.answer, True))
        self.assertEqual(len(self.asked), 1)

    def test_offline_without_a_saved_answer(self):
        with self.assertRaises(api.FetchError) as raised:
            api.cached_json(self.URL, self.tmp, online=False)
        self.assertEqual(raised.exception.code, "OFFLINE")
        self.assertEqual(self.asked, [])

    def test_an_older_answer_stands_in_when_the_request_fails(self):
        api.cached_json(self.URL, self.tmp)
        self.age(api.CACHE_TTL + 60)
        self.failing = True
        self.assertEqual(api.cached_json(self.URL, self.tmp), (self.answer, True))
        with self.assertRaises(api.FetchError):
            api.cached_json(api.search_url("never asked"), self.tmp)

    def test_nothing_found_is_never_kept(self):
        self.answer = {"foundAssets": [], "numberOfResults": 0}
        api.cached_json(self.URL, self.tmp)
        api.cached_json(self.URL, self.tmp)
        self.assertEqual(len(self.asked), 2)
        self.assertFalse(os.path.exists(api.cache_path(self.tmp, self.URL)))

    def test_no_root_no_cache(self):
        self.assertEqual(api.cached_json(self.URL, None), (self.answer, False))
        self.assertEqual(api.cached_json(self.URL, None), (self.answer, False))
        self.assertEqual(len(self.asked), 2)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_search_assets_saves_and_reads_back(self):
        assets, count, was_saved = api.search_assets("rock", root=self.tmp)
        self.assertEqual(([a["id"] for a in assets], count, was_saved), (["Ground111", "Rock064"], 76, False))
        self.age(api.CACHE_TTL + 60)
        assets, count, was_saved = api.search_assets("rock", root=self.tmp, online=False)
        self.assertEqual(([a["id"] for a in assets], count, was_saved), (["Ground111", "Rock064"], 76, True))

    def test_one_word_that_found_nothing_is_asked_again_as_an_id(self):
        by_id = self.answer

        def fetch_json(url):
            self.asked.append(url)
            return by_id if url == api.asset_url("Rock064") else {"foundAssets": [], "numberOfResults": 0}

        with mock.patch.object(api, "fetch_json", side_effect=fetch_json):
            assets, count, _saved = api.search_assets("Rock064", root=self.tmp)
            self.assertEqual(self.asked, [api.search_url("Rock064"), api.asset_url("Rock064")])
            self.assertEqual((len(assets), count), (2, 2))
            self.assertEqual(api.search_assets("Rock064", kind="HDRI", root=self.tmp)[:2], ([], 0))
            del self.asked[:]
            self.assertEqual(api.search_assets("two words", root=self.tmp)[:2], ([], 0))
            self.assertEqual(self.asked, [api.search_url("two words")])


class Tasks(unittest.TestCase):
    def test_result(self):
        task = api.Task(lambda task, a, b: a + b, 1, 2).start()
        task.thread.join(5)
        self.assertEqual(task.poll(), api.DONE)
        self.assertEqual(task.finish(), 3)

    def test_error_is_raised_by_finish(self):
        def fail(_task):
            raise api.FetchError("MISSING")

        task = api.Task(fail).start()
        task.thread.join(5)
        self.assertEqual(task.poll(), api.DONE)
        with self.assertRaises(api.FetchError) as raised:
            task.finish()
        self.assertEqual(raised.exception.text, "AmbientCG has no such download")

    def test_size_text(self):
        self.assertEqual(api.size_text(0), "1 MB")
        self.assertEqual(api.size_text(9761806), "9 MB")
        self.assertEqual(api.size_text(1153485925), "1.1 GB")


if __name__ == "__main__":
    unittest.main()
