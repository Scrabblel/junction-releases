"""Play time for games that no launcher counts: Junction times the games it starts itself.

Steam counts its own games, and GOG Galaxy counts the ones it starts. For everything else (Epic,
Ubisoft, EA, Battle.net, Rockstar, Xbox, games you added yourself), Junction watches for a program running
from the game's folder after you press Play, and adds up how long it runs.

The totals are kept in %APPDATA%\\Junction\\playtime.json, next to the settings, so clearing the
cache doesn't lose them.
"""
import ctypes
import json
import os
import threading
import time
from ctypes import wintypes

from . import config

FILE = os.path.join(config.SETTINGS_DIR, "playtime.json")
START_WAIT = 4 * 60    # seconds a launcher may take (updates, sign-in) before the game is running
CHECK_EVERY = 10       # seconds between checks while a game runs
SAVE_EVERY = 60        # seconds between saves, so a crash loses at most a minute
GONE_AFTER = 2         # checks in a row without the game before the session ends
SKIP_EXES = ("unins", "uninstall", "crashreport", "crashhandler", "redist", "vcredist", "dxsetup", "dotnet")

_lock = threading.Lock()


def counts(game):
    """Whether Junction times this game: not when Steam or GOG Galaxy already does."""
    launcher = game.get("launcher")
    if launcher == "steam":
        return False
    if launcher == "gog" and "GalaxyClient" in str(game.get("launch") or ""):
        return False
    return bool(game.get("installDir"))


def load():
    try:
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    with open(FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(FILE + ".tmp", FILE)


def _add(game_id, minutes=0.0, session=False):
    with _lock:
        data = load()
        e = data.setdefault(game_id, {"minutes": 0.0, "sessions": 0})
        e["minutes"] = round(e.get("minutes", 0) + minutes, 2)
        e["lastPlayed"] = int(time.time())
        if session:
            e["sessions"] = e.get("sessions", 0) + 1
        _save(data)


def apply(lib):
    """The library with Junction's own play time added. Returns a new dict; `lib` isn't changed."""
    data = load()
    if not data:
        return lib
    games = []
    for g in lib.get("games") or []:
        e = data.get(g["id"])
        if e:
            g = dict(g, playtimeMinutes=round((g.get("playtimeMinutes") or 0) + e.get("minutes", 0)) or None,
                     lastPlayed=max(g.get("lastPlayed") or 0, e.get("lastPlayed") or 0) or None)
        games.append(g)
    return dict(lib, games=games)


# ---------- which programs are running (Windows) ----------

_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel.OpenProcess.restype = wintypes.HANDLE
_kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel.CloseHandle.argtypes = [wintypes.HANDLE]
QUERY_LIMITED = 0x1000


def running_programs():
    """The full paths of the programs running now (those Windows lets Junction look at), lower case."""
    ids = (wintypes.DWORD * 4096)()
    got = wintypes.DWORD()
    if not _psapi.EnumProcesses(ids, ctypes.sizeof(ids), ctypes.byref(got)):
        return []
    out, buf = [], ctypes.create_unicode_buffer(1024)
    for pid in ids[:got.value // ctypes.sizeof(wintypes.DWORD)]:
        if not pid or pid == os.getpid():
            continue
        h = _kernel.OpenProcess(QUERY_LIMITED, False, pid)
        if not h:
            continue
        try:
            size = wintypes.DWORD(len(buf))
            if _kernel.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                out.append(buf.value.lower())
        finally:
            _kernel.CloseHandle(h)
    return out


def _is_running(folder, programs=None):
    folder = os.path.normcase(os.path.abspath(folder)).rstrip("\\") + "\\"
    for p in programs if programs is not None else running_programs():
        if p.startswith(folder) and not any(s in os.path.basename(p) for s in SKIP_EXES):
            return True
    return False


class Tracker:
    """Times one session per game started from Junction. on_change() runs when a session starts or ends."""

    def __init__(self, on_change=lambda: None):
        self.on_change = on_change
        self.playing = {}  # game id -> start time
        self._watching = set()

    def started(self, game):
        if not counts(game) or game["id"] in self._watching:
            return
        self._watching.add(game["id"])
        threading.Thread(target=self._watch, args=(game["id"], game["installDir"]), daemon=True).start()

    def _watch(self, game_id, folder):
        try:
            t0 = time.time()
            while not _is_running(folder):
                if time.time() - t0 > START_WAIT:
                    return
                time.sleep(3)
            self.playing[game_id] = start = last = saved = time.time()
            _add(game_id, session=True)  # shows up in Continue playing straight away
            self.on_change()
            missing = 0
            while missing < GONE_AFTER:
                time.sleep(CHECK_EVERY)
                missing = 0 if _is_running(folder) else missing + 1
                if missing == 0:
                    last = time.time()
                if time.time() - saved >= SAVE_EVERY:
                    _add(game_id, (last - saved) / 60)
                    saved = last
            _add(game_id, max(0.0, last - saved) / 60)
            self.playing.pop(game_id, None)
            print(f"Played {game_id} for {round((last - start) / 60)} min", flush=True)
            self.on_change()
        except Exception:  # noqa: BLE001 - never take the app down over a timer
            import traceback
            traceback.print_exc()
        finally:
            self._watching.discard(game_id)
            self.playing.pop(game_id, None)
