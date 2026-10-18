"""Where Junction keeps its settings and cache.

- Settings (API keys, Steam accounts): %APPDATA%\\Junction\\settings.json. Each person's own file;
  nothing in it is ever part of the app itself.
- Cache (last scan, store prices, lookups): %LOCALAPPDATA%\\Junction\\cache.

While developing from this folder (not as Junction.exe), a local_settings.json next to the code is
used instead whenever it exists, so running the installed app never takes the developer's keys away.

JUNCTION_HOME=<folder> keeps all of it (settings, cache, logs, play time) in that one folder instead,
as a separate copy of Junction: for testing a build without touching anyone's real settings.
"""
import json
import os
import sys
import zlib

APP_NAME = "Junction"
VERSION = "0.9.1"
HOME = os.environ.get("JUNCTION_HOME")
SETTINGS_DIR = HOME or os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
CACHE_DIR = os.path.join(HOME or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP_NAME), "cache")
# Only one Junction runs at a time; a separate copy (JUNCTION_HOME) counts as another app.
INSTANCE = APP_NAME + (f"-{zlib.crc32(os.path.abspath(HOME).lower().encode()):08x}" if HOME else "")
# The page's local address. It has to stay the same between runs, because the page saves favourites,
# collections and covers per address; a separate copy gets an address of its own.
HTTP_PORT = 42001 if not HOME else 42101 + zlib.crc32(os.path.abspath(HOME).lower().encode()) % 800
DEV_SETTINGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "local_settings.json")


def resource(*parts):
    """A file shipped with the app (web/...), also when it's packed into an .exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


def settings_path():
    if HOME or getattr(sys, "frozen", False):
        return SETTINGS_FILE
    return DEV_SETTINGS if os.path.isfile(DEV_SETTINGS) else SETTINGS_FILE


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_cache(name, default=None):
    try:
        with open(os.path.join(CACHE_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_cache(name, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(path + ".tmp", path)


def cache_age(name):
    """Seconds since the cache file was written, or None."""
    try:
        import time
        return time.time() - os.path.getmtime(os.path.join(CACHE_DIR, name))
    except OSError:
        return None
