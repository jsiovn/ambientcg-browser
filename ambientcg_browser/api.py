"""Transport of AmbientCG Browser: search ambientcg.com, download a CC0 material or HDRI into the cache folder.

No bpy here: the downloads run on daemon threads and the panel's timer polls the Task, so this module also runs
under a plain `python -I` for its tests. The API needs no key; `download_file` streams, so Cancel works per chunk.
"""

import concurrent.futures
import hashlib
import http.client
import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib


def _version():
    # A regex, not tomllib: the tests run this module under any Python 3.
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_manifest.toml"),
                  encoding="utf-8") as f:
            m = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M)
    except OSError:
        m = None
    return m.group(1) if m else "0"


API_URL = "https://ambientcg.com/api/v2/full_json"
GET_URL = "https://ambientcg.com/get"
USER_AGENT = "AmbientCG-Browser-Blender/%s (+https://github.com/jsiovn/ambientcg-browser)" % _version()
PAGE = 24
TIMEOUT = 15
MAX_LIMIT = 250
CHUNK = 256 * 1024
THUMB_WORKERS = 6
THUMBS = "thumbs"
API_CACHE = "api"
CACHE_TTL = 24 * 3600         # seconds an answer is used without asking
CACHE_KEEP = 30 * 24 * 3600   # seconds it stays as the offline fallback

RUNNING, DONE = "RUNNING", "DONE"

# Saved in files as their numbers: the numbers are fixed, never renumber.
KIND_ITEMS = (
    ("MATERIAL", "Material", "PBR materials", 0),
    ("HDRI", "HDRI", "Environment lighting", 1),
)
SORT_ITEMS = (
    ("Popular", "Popular", "Most popular first", 0),
    ("Latest", "Latest", "Newest first", 1),
    ("Alphabet", "A-Z", "By name", 2),
    ("Downloads", "Downloads", "Most downloaded first", 3),
)
RESOLUTION_ITEMS = (
    ("1K", "1K", "1024 px, about 10 MB", 0),
    ("2K", "2K", "2048 px, about 30 MB", 1),
    ("4K", "4K", "4096 px, about 120 MB", 2),
    ("8K", "8K", "8192 px, about 500 MB", 3),
    ("12K", "12K", "HDRIs and a few materials", 4),
    ("16K", "16K", "HDRIs and a few materials", 5),
)
FORMAT_ITEMS = (
    ("JPG", "JPG", "Smaller files", 0),
    ("PNG", "PNG", "Lossless, several times bigger", 1),
)
PROJECTION_ITEMS = (
    ("FLAT", "UV", "Use the object's UV map", 0),
    ("BOX", "Box", "Project from the object's sides, no UV map needed", 1),
    ("SPHERE", "Sphere", "Project from a sphere, no UV map needed", 2),
    ("TUBE", "Tube", "Project from a tube, no UV map needed", 3),
)

_TYPES = {"MATERIAL": "Material", "HDRI": "HDRI"}
_KINDS = {v: k for k, v in _TYPES.items()}
MAP_SUFFIXES = {
    "color": "color", "roughness": "roughness", "normalgl": "normal", "normaldx": "normal_dx",
    "displacement": "displacement", "ambientocclusion": "ao", "metalness": "metalness", "opacity": "opacity",
    "emission": "emission", "hdr": "hdr", "tonemapped": "tonemapped",
}
_REQUIRED = {"MATERIAL": "color", "HDRI": "hdr"}
_FILE_TYPES = (".jpg", ".jpeg", ".png", ".exr")
_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")   # asset ids and attributes become folder names
_ATTRIBUTE = re.compile(r"^(\d+)K(?:-(JPG|PNG))?$")

_TEXTS = {
    "HTTP": "AmbientCG cannot be reached",
    "OFFLINE": "Online access is off and this search is not saved",
    "MISSING": "AmbientCG has no such download",
    "ZIP": "The download is damaged, try again",
    "MAPS": "The download holds no usable maps",
    "ID": "Not an AmbientCG asset id",
    "KIND": "That asset is of another type",
    "DISK": "Cannot write to the cache folder",
    "CANCELLED": "Cancelled",
}


class FetchError(Exception):
    def __init__(self, code, text="", detail=""):
        text = text or _TEXTS.get(code, code)
        super().__init__(text)
        self.code = code
        self.text = text
        self.detail = detail


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_url(query="", kind="MATERIAL", sort="Popular", limit=PAGE, offset=0):
    return API_URL + "?" + urllib.parse.urlencode({
        "q": query, "type": _TYPES[kind], "sort": sort, "limit": max(1, min(int(limit), MAX_LIMIT)),
        "offset": max(0, int(offset)), "include": "downloadData",
    })


def asset_url(asset_id):
    return API_URL + "?" + urllib.parse.urlencode({"id": asset_id, "include": "downloadData"})


def _open(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def fetch_json(url):
    try:
        with _open(url) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException) as ex:
        raise FetchError("HTTP", detail="%s: %s" % (url, ex)) from None


def _dig(data, *keys):
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def parse_assets(data):
    """The assets of an API answer, as plain dicts; another dataType, an unsafe id or a missing key never raises."""
    found = _dig(data, "foundAssets")
    out = []
    for raw in found if isinstance(found, list) else ():
        kind = _KINDS.get(_dig(raw, "dataType"))
        asset_id = _dig(raw, "assetId")
        if kind is None or not isinstance(asset_id, str) or not _SAFE.match(asset_id):
            continue
        downloads = {}
        for entry in _dig(raw, "downloadFolders", "default", "downloadFiletypeCategories", "zip", "downloads") or ():
            attribute = _dig(entry, "attribute")
            if isinstance(attribute, str) and _SAFE.match(attribute):
                downloads[attribute] = {"url": _dig(entry, "downloadLink") or download_url(asset_id, attribute),
                                        "file": _dig(entry, "fileName") or download_name(asset_id, attribute),
                                        "size": _dig(entry, "size") or 0}
        preview = _dig(raw, "previewImage") or {}
        out.append({
            "id": asset_id, "name": _dig(raw, "displayName") or asset_id, "kind": kind,
            "tags": list(_dig(raw, "tags") or ()), "maps": list(_dig(raw, "maps") or ()),
            "thumb": next((preview[k] for k in ("256-PNG", "128-PNG", "64-PNG") if preview.get(k)), ""),
            "downloads": downloads,
        })
    return out


def parse_count(data):
    count = _dig(data, "numberOfResults")
    return count if isinstance(count, int) else 0


def search_assets(query="", kind="MATERIAL", sort="Popular", limit=PAGE, offset=0, root=None, online=True,
                  refresh=False):
    """(assets, total count, saved), saved = an older answer stood in for AmbientCG. The q parameter matches names
    and tags, never an exact id: one word that found nothing is asked again as an id."""
    data, saved = cached_json(search_url(query, kind, sort, limit, offset), root, online, refresh)
    assets = parse_assets(data)
    word = query.strip()
    if assets or offset or not _SAFE.match(word):
        return assets, parse_count(data), saved
    data, saved = cached_json(asset_url(word), root, online, refresh)
    assets = [a for a in parse_assets(data) if a["kind"] == kind]
    return assets, len(assets), saved


def attr_key(kind, resolution, fmt="JPG"):
    return resolution if kind == "HDRI" else "%s-%s" % (resolution, fmt)


def attribute_for(asset, resolution, fmt="JPG"):
    """The download of asset for a resolution: exact, else the largest under it in that format, else the smallest
    there is, else None."""
    downloads = asset.get("downloads") or {}
    key = attr_key(asset["kind"], resolution, fmt)
    if key in downloads:
        return key
    want = int(resolution.rstrip("K"))
    fmt = None if asset["kind"] == "HDRI" else fmt
    sizes = [(int(m.group(1)), m.group(2), m.group(0)) for m in map(_ATTRIBUTE.match, downloads) if m]
    under = [s for s in sizes if s[1] == fmt and s[0] <= want]
    if under:
        return max(under)[2]
    same = [s for s in sizes if s[1] == fmt] or sizes
    return min(same, key=lambda s: (s[0], s[2]))[2] if same else None


def size_text(size):
    if size >= 1024 ** 3:
        return "%.1f GB" % (size / 1024 ** 3)
    return "%d MB" % max(1, round(size / 1024 ** 2))


def download_name(asset_id, attribute):
    return "%s_%s.zip" % (asset_id, attribute)


def download_url(asset_id, attribute):
    return GET_URL + "?" + urllib.parse.urlencode({"file": download_name(asset_id, attribute)})


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def fallback_root():
    return os.path.join(tempfile.gettempdir(), "ambientcg-browser")


def _writable(folder):
    try:
        os.makedirs(folder, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


def cache_root(folder=""):
    """The picked folder itself when set and writable, else the temp fallback."""
    if folder and _writable(folder):
        return folder
    root = fallback_root()
    _writable(root)
    return root


def asset_dir(root, asset_id, attribute):
    if not (_SAFE.match(asset_id or "") and _SAFE.match(attribute or "")):
        raise FetchError("ID", detail="%r %r" % (asset_id, attribute))
    return os.path.join(root, "%s_%s" % (asset_id, attribute))


def thumb_path(root, asset_id):
    return os.path.join(root, THUMBS, asset_id + ".png")


def cache_path(root, url):
    return os.path.join(root, API_CACHE, hashlib.blake2b(url.encode("utf-8"), digest_size=16).hexdigest() + ".json")


def _write_atomic(path, data):
    """Through a unique temp file: two threads may write the same path, and a reader never sees half of it."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(tmp, path)
    except OSError:
        _remove(tmp)
        raise


def _read_cached(path):
    """(answer, age in seconds), None when there is no readable file. The file's mtime is its age."""
    try:
        age = time.time() - os.path.getmtime(path)
        if -1.0 < age < 0.0:
            # Just written: the clock that dates files is finer than time.time(), which ticks every 16 ms on
            # Windows before Python 3.13 (Blender 4.2 and 4.5).
            age = 0.0
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8")), age
    except (OSError, ValueError):
        return None


_pruned = set()   # roots swept this session


def _prune(root):
    if root in _pruned:
        return
    _pruned.add(root)
    folder = os.path.join(root, API_CACHE)
    limit = time.time() - CACHE_KEEP
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        try:
            if os.path.getmtime(path) < limit:
                os.remove(path)
        except OSError:
            pass


def cached_json(url, root, online=True, refresh=False):
    """(answer, saved). A fresh file answers without asking unless refresh; an older one stands in, saved = True, when
    AmbientCG cannot be asked. Only answers with assets are kept: "nothing found" is always asked again."""
    if root is None:
        return fetch_json(url), False
    path = cache_path(root, url)
    cached = _read_cached(path)
    if cached is not None and not refresh and 0 <= cached[1] < CACHE_TTL:
        return cached[0], False
    if not online:
        if cached is None:
            raise FetchError("OFFLINE", detail=url)
        return cached[0], True
    try:
        data = fetch_json(url)
    except FetchError:
        if cached is None:
            raise
        return cached[0], True
    if parse_assets(data):
        try:
            _write_atomic(path, json.dumps(data).encode("utf-8"))
            _prune(root)
        except OSError:   # a read-only cache folder costs the next request, not this answer
            pass
    return data, False


def maps_in(folder):
    """{role: path} of the maps extracted into folder, whose name is the prefix of its files."""
    pattern = re.compile(r"^%s_([A-Za-z]+)\.(?:jpg|jpeg|png|exr)$" % re.escape(os.path.basename(folder)), re.I)
    try:
        names = os.listdir(folder)
    except OSError:
        return {}
    found = {}
    for name in names:
        m = pattern.match(name)
        role = MAP_SUFFIXES.get(m.group(1).lower()) if m else None
        if role is not None:
            found[role] = os.path.join(folder, name)
    return found


def is_complete(folder, kind):
    return _REQUIRED[kind] in maps_in(folder)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def safe_members(zf):
    """The image files of a zip with a plain file name: nothing can land outside the folder, and the .blend, .usdc,
    .mtlx and .tres that come along are left out."""
    out = []
    for info in zf.infolist():
        name = info.filename
        if info.is_dir() or any(bad in name for bad in ("/", "\\", ":", "..")):
            continue
        if os.path.splitext(name)[1].lower() in _FILE_TYPES:
            out.append(name)
    return out


def extract(zip_path, folder):
    """Unpack into folder, all or nothing: is_complete must never see half a download."""
    part = folder + ".part"
    shutil.rmtree(part, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            os.makedirs(part)
            for name in safe_members(zf):
                with zf.open(name) as src, open(os.path.join(part, name), "wb") as dst:
                    shutil.copyfileobj(src, dst, CHUNK)
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(part, folder)
    except (zipfile.BadZipFile, EOFError, zlib.error) as ex:
        shutil.rmtree(part, ignore_errors=True)
        _remove(zip_path)
        raise FetchError("ZIP", detail=str(ex)) from None
    except OSError as ex:
        shutil.rmtree(part, ignore_errors=True)
        raise FetchError("DISK", detail=str(ex)) from None
    return folder


def download_file(url, dest, cancelled=None, progress=None):
    """Stream url to dest. urllib cannot be aborted, so cancelled() is asked once per chunk."""
    part = dest + ".part"
    done = 0
    try:
        with _open(url) as response, open(part, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0) or None
            while True:
                if cancelled is not None and cancelled():
                    raise FetchError("CANCELLED")
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        if total is not None and done < total:
            raise FetchError("HTTP", detail="%s: %d of %d bytes" % (url, done, total))
        os.replace(part, dest)
    except urllib.error.HTTPError as ex:
        raise FetchError("MISSING" if ex.code == 404 else "HTTP", detail="%s: %s" % (url, ex)) from None
    except (OSError, http.client.HTTPException) as ex:
        raise FetchError("HTTP", detail="%s: %s" % (url, ex)) from None
    finally:
        _remove(part)
    return dest


def fetch_asset(asset_id, attribute, kind, root, cancelled=None, progress=None):
    """The blocking core: the folder of the asset's maps, downloaded unless it is in the cache already."""
    folder = asset_dir(root, asset_id, attribute)
    if is_complete(folder, kind):
        return folder
    zip_path = os.path.join(root, download_name(asset_id, attribute))
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as ex:
        raise FetchError("DISK", detail=str(ex)) from None
    try:
        download_file(download_url(asset_id, attribute), zip_path, cancelled, progress)
        extract(zip_path, folder)
    finally:
        _remove(zip_path)
    if not is_complete(folder, kind):
        shutil.rmtree(folder, ignore_errors=True)
        raise FetchError("MAPS", detail=folder)
    return folder


def fetch_thumb(url, path):
    """Download a preview to path; a replaced search may fetch the same one."""
    try:
        with _open(url) as response:
            _write_atomic(path, response.read())
    except (OSError, http.client.HTTPException) as ex:
        raise FetchError("HTTP", detail="%s: %s" % (url, ex)) from None
    return path


def fetch_thumbs(task, wanted):
    """Task body: fetch (asset id, url, path) rows side by side, each done row into task.ready; a thumbnail that
    fails stays an icon. The page shows once they are in, so one at a time would keep it waiting."""
    def fetch(row):
        if task.cancelled:
            return
        try:
            fetch_thumb(row[1], row[2])
        except FetchError:
            return
        with task.lock:
            task.ready.append((row[0], row[2]))

    with concurrent.futures.ThreadPoolExecutor(max_workers=THUMB_WORKERS) as pool:
        list(pool.map(fetch, wanted))


class Task:
    """fn(task, *args) on a daemon thread, with the surface the panel's timer polls."""

    def __init__(self, fn, *args):
        self.fn = fn
        self.args = args
        self.thread = None
        self.started = None
        self.cancelled = False
        self.progress = None   # (done, total or None)
        self.lock = threading.Lock()
        self.ready = []
        self.result = None
        self.error = None
        self._done = threading.Event()

    def start(self):
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def _run(self):
        try:
            self.result = self.fn(self, *self.args)
        except Exception as ex:  # finish() raises it on the main thread
            self.error = ex
        self._done.set()

    def report(self, done, total):
        self.progress = (done, total)

    def elapsed(self):
        return 0.0 if self.started is None else time.monotonic() - self.started

    def poll(self):
        return DONE if self._done.is_set() else RUNNING

    def cancel(self):
        self.cancelled = True

    def finish(self):
        if self.error is not None:
            raise self.error
        return self.result


_classes = ()


def register():
    pass


def unregister():
    pass
