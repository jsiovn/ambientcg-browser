"""Tests of tools/make_site.py, the landing page generator: no Blender, no network.

    python -I -m unittest discover tests
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location("make_site", os.path.join(ROOT, "tools", "make_site.py"))
make_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_site)

# As `blender --command extension server-generate` writes it.
PACKAGE = {
    "schema_version": "1.0.0", "id": "ambientcg_browser", "name": "AmbientCG Browser", "version": "1.2.3",
    "tagline": "Search ambientCG and add its CC0 materials and HDRIs", "type": "add-on",
    "blender_version_min": "4.2.0", "website": "https://jsiovn.github.io/ambientcg-browser/",
    "archive_url": "./ambientcg_browser-1.2.3.zip", "archive_size": 22740, "archive_hash": "sha256:00",
}


class InstallLink(unittest.TestCase):
    def test_the_form_blender_reads_on_a_drop(self):
        self.assertEqual(make_site.install_href(PACKAGE),
                         "./ambientcg_browser-1.2.3.zip?repository=.%2Findex.json&blender_version_min=4.2.0")

    def test_every_parameter_of_blenders_own_page(self):
        package = dict(PACKAGE, blender_version_max="6.0.0", platforms=["windows-x64", "linux-x64"],
                       python_versions=["3.11"])
        self.assertEqual(
            make_site.install_href(package),
            "./ambientcg_browser-1.2.3.zip?repository=.%2Findex.json&blender_version_min=4.2.0"
            "&blender_version_max=6.0.0&platforms=windows-x64%2Clinux-x64&python_versions=3.11")


class Values(unittest.TestCase):
    def test_values(self):
        found = make_site.values(PACKAGE, changelog=os.devnull)
        self.assertEqual(found["version"], "1.2.3")
        self.assertEqual(found["zip_name"], "ambientcg_browser-1.2.3.zip")
        self.assertEqual(found["zip_href"], "./ambientcg_browser-1.2.3.zip")
        self.assertEqual(found["size"], "22 KB")
        self.assertEqual(found["blender_min"], "4.2")
        self.assertEqual(found["repo_url"], "https://jsiovn.github.io/ambientcg-browser/index.json")
        self.assertEqual(found["site_url"], "https://jsiovn.github.io/ambientcg-browser/")
        self.assertRegex(found["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(make_site.values(PACKAGE, "https://example.org/x", os.devnull)["repo_url"],
                         "https://example.org/x/index.json")

    def test_sizes_and_versions(self):
        self.assertEqual(make_site.size_text(100), "1 KB")
        self.assertEqual(make_site.size_text(3 * 1024 ** 2), "3.0 MB")
        self.assertEqual(make_site.short_version("4.2.0"), "4.2")
        self.assertEqual(make_site.short_version("4.2.1"), "4.2.1")

    def test_the_date_is_the_one_of_the_changelog_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            changelog = os.path.join(tmp, "CHANGELOG.md")
            with open(changelog, "w", encoding="utf-8") as f:
                f.write("# Changelog\n\n## [1.2.4] - 2027-01-01\n\n## [1.2.3] - 2026-12-24\n\n- x\n")
            self.assertEqual(make_site.release_date("1.2.3", changelog), "2026-12-24")

    def test_this_release_is_in_the_changelog(self):
        with open(os.path.join(ROOT, "ambientcg_browser", "blender_manifest.toml"), encoding="utf-8") as f:
            version = next(line.split('"')[1] for line in f if line.startswith("version"))
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
            self.assertIn("## [%s] - " % version, f.read(),
                          "the Publish workflow refuses a version without its section")


class Page(unittest.TestCase):
    def test_render_escapes_and_refuses_unknown_values(self):
        self.assertEqual(make_site.render('<a href="{{href}}">{{name}}</a>', {"href": "a?b=1&c=2", "name": "<x>"}),
                         '<a href="a?b=1&amp;c=2">&lt;x&gt;</a>')
        with self.assertRaises(KeyError):
            make_site.render("{{missing}}", {})

    def test_build_writes_the_page_of_this_repository(self):
        tmp = tempfile.mkdtemp(prefix="ambientcg-browser-site-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with open(os.path.join(tmp, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"version": "v1", "blocklist": [], "data": [dict(PACKAGE, id="other"), PACKAGE]}, f)
        make_site.build(tmp)
        with open(os.path.join(tmp, "index.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertFalse("{{" in page or "}}" in page, "a value of the template was left unfilled")
        self.assertEqual(page.count(
            'href="./ambientcg_browser-1.2.3.zip?repository=.%2Findex.json&amp;blender_version_min=4.2.0"'), 2)
        for needle in ('data-copy="https://jsiovn.github.io/ambientcg-browser/index.json"', "not affiliated with",
                       "Version 1.2.3", "GPL-3.0-or-later",
                       "either version 3 of the License, or (at your option) any later version"):
            self.assertTrue(needle in page, needle)
        # Self-contained: nothing is loaded from another site.
        for needle in ('src="http', "src='http", '<link rel="stylesheet"', "@import", "url(http"):
            self.assertFalse(needle in page, needle)
        for name in os.listdir(os.path.join(ROOT, "site", "assets")):
            self.assertTrue(os.path.isfile(os.path.join(tmp, "assets", name)), name)
            self.assertTrue("assets/" + name in page, "%s is in site/assets but not on the page" % name)

    def test_the_page_is_in_english_and_vietnamese(self):
        tmp = tempfile.mkdtemp(prefix="ambientcg-browser-site-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with open(os.path.join(tmp, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"version": "v1", "blocklist": [], "data": [PACKAGE]}, f)
        make_site.build(tmp)
        with open(os.path.join(tmp, "index.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertFalse("{{" in page or "}}" in page, "a value of the template was left unfilled")
        for needle in ('<html lang="en">', 'data-lang="en"', 'data-lang="vi"', ">Tiếng Việt<",
                       "Kéo thả vào Blender để cài đặt", "Phiên bản 1.2.3", "Không chính thức"):
            self.assertTrue(needle in page, needle)
        # Each Vietnamese span follows an English one (text outside any span is not caught here).
        self.assertEqual(page.count('<span lang="en">'), page.count('<span lang="vi">'))
        self.assertEqual(page.count('</span><span lang="vi">'), page.count('<span lang="vi">'))
        self.assertEqual(page.count("data-vi-alt="), page.count("<img "))

    def test_the_video_guide_is_on_the_page(self):
        with open(os.path.join(ROOT, "site", "index.html"), encoding="utf-8") as f:
            page = f.read()
        for name, needle in (("guide.mp4", 'src="assets/guide.mp4"'),
                             ("guide-poster.webp", 'poster="assets/guide-poster.webp"')):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "site", "assets", name)), name)
            self.assertIn(needle, page)

    def test_a_missing_package_is_an_error(self):
        with self.assertRaises(LookupError):
            make_site.package_of({"data": [dict(PACKAGE, id="other")]})


if __name__ == "__main__":
    unittest.main()
