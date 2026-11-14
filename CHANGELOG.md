# Changelog

All notable changes to **AmbientCG Browser** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The version is
`version` in `ambientcg_browser/blender_manifest.toml`; publishing a version requires its section here.

## [1.0.0] - 2026-11-14

First release. Free software under the GNU General Public License, either version 3 of the License, or (at your
option) any later version (GPL-3.0-or-later); the license text is in the extension zip.

### Added

- **AmbientCG panel** in the sidebar of the 3D Viewport (`N` › AmbientCG). Search
  [ambientcg.com](https://ambientcg.com) for CC0 materials and HDRIs by keyword, with thumbnails, in a results
  window with Prev and Next.
- **Add Material** downloads the maps in the picked resolution (1K to 16K, JPG or PNG; the size shows before the
  click) in the background, builds a material from color, roughness, normal, height, ambient occlusion, metal,
  opacity and emission, and puts it on the selected objects, by UV or Box, Sphere or Tube projection. A download
  can be cancelled.
- **Set World HDRI** makes an HDRI the scene's world, with strength and rotation sliders; the world you had stays
  in the file.
- **Cache folder.** Downloads, thumbnails and saved searches are kept in the system temp folder, or in a folder
  you pick in the panel or in the add-on preferences. A downloaded asset is never fetched twice and works offline.
- **Saved searches.** Results are kept for a day, so repeating a search or turning a page needs no request, and
  they stay as a fallback for 30 days, shown with their thumbnails offline or when ambientCG is down. The search
  button always asks ambientCG for fresh results.
- **Python API for scripts:** `search`, `asset`, `fetch`, `create_material`, `assign`, `set_world` and `attach`
  in the `ambientcg` module of the package, documented at the top of that module.
- **Check for Updates** in the panel and in the add-on preferences: compares with the published repository and
  lets Blender install the newer version.
