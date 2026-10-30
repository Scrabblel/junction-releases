"""Where Junction keeps its settings and cache.

- Settings (API keys, Steam accounts): %APPDATA%\\Junction\\settings.json. Each person's own file;
  nothing in it is ever part of the app itself. The keys in it are encrypted (see PROTECTED).
- Cache (last scan, store prices, lookups): %LOCALAPPDATA%\\Junction\\cache.

While developing from this folder (not as Junction.exe), a local_settings.json next to the code is
used instead whenever it exists, so running the installed app never takes the developer's keys away.

JUNCTION_HOME=<folder> keeps all of it (settings, cache, logs, play time) in that one folder instead,
as a separate copy of Junction: for testing a build without touching anyone's real settings.
"""
import base64
import copy
import ctypes
import http.client
import json
import os
import re
import sys
import threading
import time
import zlib

APP_NAME = "Junction"
VERSION = "0.9.29"
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


class Unread(dict):
    """What a file reads as when it couldn't be read just now (another program held it, say): empty for whoever only
    looks, but never saved over the file, which still holds everything (settings, sign-ins, play time)."""


def read_held(read, tries=10):
    """read(), trying again for a moment while another program holds the file, as replace_file does for saving.
    A file that isn't there (FileNotFoundError) and other errors go to the caller."""
    for i in range(tries):
        try:
            return read()
        except PermissionError:  # Windows' "being used by another process"
            if i == tries - 1:
                raise
            time.sleep(0.1)


def refuse_unread(data, what):
    """Saving what was read from a file that couldn't be read would put "nothing" over everything in it."""
    if isinstance(data, Unread):
        raise OSError(f"Junction couldn't read its {what} just now, so it didn't save over them. Try again.")


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_settings():
    path = settings_path()
    try:
        data = read_held(lambda: _read_json(path))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except OSError:
        return Unread()
    except ValueError:
        # Damaged: keep it aside, so saving the next change doesn't quietly drop every key and account in it.
        try:
            os.replace(path, path + ".damaged")
        except OSError:
            pass
        return {}


# The API keys in settings.json are saved encrypted with Windows' own per-user protection (DPAPI), like the
# sign-ins: another program can't just read them from the file. Only the developer's local_settings.json stays
# plain, so it can be edited by hand. Everything else in Junction gets the keys back as plain text.
PROTECTED = "protected:"


def _key_fields(settings):
    """(dict, name) for every API key in the settings."""
    fields = [(settings, "itadApiKey"), (settings, "steamGridDbKey")]
    for acc in settings.get("steamAccounts") or []:
        if isinstance(acc, dict):
            fields.append((acc, "apiKey"))
    return [(d, k) for d, k in fields if isinstance(d.get(k), str) and d[k]]


def _protects():
    return settings_path() != DEV_SETTINGS


def load_settings():
    with _settings_lock:
        data = _read_settings()
        from .signins import unprotect
        for d, k in _key_fields(data):
            if d[k].startswith(PROTECTED):
                try:
                    d[k] = unprotect(base64.b64decode(d[k][len(PROTECTED):])).decode("utf-8")
                except (OSError, ValueError):  # saved by another Windows user or PC: it has to be entered again
                    d[k] = ""
        return data


def save_settings(settings):
    refuse_unread(settings, "settings")
    with _settings_lock:
        path = settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if _protects():
            from .signins import protect
            settings = copy.deepcopy(settings)
            for d, k in _key_fields(settings):
                if not d[k].startswith(PROTECTED):
                    d[k] = PROTECTED + base64.b64encode(protect(d[k].encode("utf-8"))).decode("ascii")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        replace_file(tmp, path)


def protect_saved_keys():
    """An older Junction saved the keys as plain text: save them again, protected."""
    with _settings_lock:
        if _protects() and any(not d[k].startswith(PROTECTED) for d, k in _key_fields(_read_settings())):
            save_settings(load_settings())


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
        return time.time() - os.path.getmtime(os.path.join(CACHE_DIR, name))
    except OSError:
        return None


# ---------- where prices are for ----------

def windows_region():
    """The country set in Windows (Settings > Time & language > Region) as a two-letter code, or None."""
    buf = ctypes.create_unicode_buffer(16)
    try:
        if ctypes.windll.kernel32.GetUserDefaultGeoName(buf, len(buf)) and re.fullmatch("[A-Z]{2}", buf.value):
            return buf.value
    except (AttributeError, OSError):  # older Windows 10
        pass
    return None


def windows_money_style():
    """How Windows writes money for this person: {"locale": "en-NL", "decimal": ",", "group": "."}. The page
    writes prices with that locale, but with Windows' own marks: browsers don't always agree with Windows on those
    (English (Netherlands) is "1.234,50" in Windows, "1,234.50" in the page)."""
    def info(kind):
        buf = ctypes.create_unicode_buffer(85)
        try:
            return buf.value if ctypes.windll.kernel32.GetLocaleInfoEx(None, kind, buf, len(buf)) else None
        except (AttributeError, OSError):
            return None
    LOCALE_SNAME, LOCALE_SMONDECIMALSEP, LOCALE_SMONTHOUSANDSEP = 0x5C, 0x16, 0x17
    return {"locale": info(LOCALE_SNAME), "decimal": info(LOCALE_SMONDECIMALSEP), "group": info(LOCALE_SMONTHOUSANDSEP)}


def country(settings=None):
    """The country the Store's prices are for: the one the person chose, else Windows' region."""
    if settings is None:
        with _settings_lock:  # the file as it is: the country isn't encrypted, so no need to unlock every key for it
            settings = _read_settings()
    chosen = str(settings.get("country") or "")
    return chosen if re.fullmatch("[A-Z]{2}", chosen) else windows_region() or "US"
