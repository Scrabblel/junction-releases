"""Where Junction keeps its settings and cache.

- Settings (API keys, Steam accounts): %APPDATA%\\Junction\\settings.json. Each person's own file;
  nothing in it is ever part of the app itself.
- Cache (last scan, store prices, lookups): %LOCALAPPDATA%\\Junction\\cache.

While developing from this folder (not as Junction.exe), a local_settings.json next to the code is
used instead whenever it exists, so running the installed app never takes the developer's keys away.

JUNCTION_HOME=<folder> keeps all of it (settings, cache, logs, play time) in that one folder instead,
as a separate copy of Junction: for testing a build without touching anyone's real settings.
"""
import http.client
import json
import os
import sys
import threading
import time
import zlib

APP_NAME = "Junction"
VERSION = "0.9.15"
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


# Everything a web request can fail with: URLError and timeouts are OSErrors, and a dropped connection or a cut-off
# answer (RemoteDisconnected, IncompleteRead) is an HTTPException that urllib doesn't wrap. ValueError: bad JSON.
NET_ERRORS = (OSError, http.client.HTTPException, ValueError)


def resource(*parts):
    """A file shipped with the app (web/...), also when it's packed into an .exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


def settings_path():
    if HOME or getattr(sys, "frozen", False):
        return SETTINGS_FILE
    return DEV_SETTINGS if os.path.isfile(DEV_SETTINGS) else SETTINGS_FILE


_settings_lock = threading.RLock()  # Windows can't replace a file while another thread is reading it


def replace_file(tmp, path, tries=10):
    """os.replace, trying again for a moment when the file is open elsewhere (another thread, a virus scanner)."""
    for i in range(tries):
        try:
            return os.replace(tmp, path)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.1)


def load_settings():
    with _settings_lock:
        path = settings_path()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except OSError:
            return {}
        except ValueError:
            # Damaged: keep it aside, so saving the next change doesn't quietly drop every key and account in it.
            try:
                os.replace(path, path + ".damaged")
            except OSError:
                pass
            return {}


def save_settings(settings):
    with _settings_lock:
        path = settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        replace_file(tmp, path)


_cache_lock = threading.RLock()


def read_cache(name, default=None):
    with _cache_lock:
        try:
            with open(os.path.join(CACHE_DIR, name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default


def write_cache(name, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name)
    with _cache_lock:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        replace_file(path + ".tmp", path)


def cache_age(name):
    """Seconds since the cache file was written, or None."""
    try:
        import time
        return time.time() - os.path.getmtime(os.path.join(CACHE_DIR, name))
    except OSError:
        return None
