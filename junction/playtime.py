"""Play time for games that no launcher counts: Junction times them itself.

Steam counts its own games, and GOG Galaxy counts the ones it starts. For everything else (Epic,
Ubisoft, EA, Battle.net, Rockstar, Xbox, games you added yourself), Junction watches for a program running
from the game's folder and adds up how long it runs: after you press Play, and also, while Junction is
open or in the tray, when you start an installed game some other way (from its launcher or a shortcut).

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
LOOK_EVERY = 30        # seconds between looks for installed games started outside Junction
SKIP_EXES = ("unins", "uninstall", "crashreport", "crashhandler", "redist", "vcredist", "dxsetup", "dotnet")

_lock = threading.RLock()  # reading and saving playtime.json never overlap (Windows can't replace a file being read)


def counts(game):
    """Whether Junction times this game: not when Steam or GOG Galaxy already does."""
    launcher = game.get("launcher")
    if launcher == "steam":
        return False
    if launcher == "gog" and "GalaxyClient" in str(game.get("launch") or ""):
        return False
    return bool(game.get("installDir"))


def load():
    with _lock:
        try:
            data = config.read_held(lambda: config._read_json(FILE))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except OSError:  # held by another program: shows nothing for now, and saving waits (see _save)
            return config.Unread()
        except ValueError:
            # A damaged file: keep it aside instead of saving over everyone's totals with just one game.
            try:
                os.replace(FILE, FILE + ".damaged")
            except OSError:
                pass
            return {}


def _save(data):
    config.refuse_unread(data, "play time")
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    with open(FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    config.replace_file(FILE + ".tmp", FILE)


def _add(game_id, minutes=0.0, session=False):
    with _lock:
        data = load()
        e = data.setdefault(game_id, {"minutes": 0.0, "sessions": 0})
        e["minutes"] = round(e.get("minutes", 0) + minutes, 2)
        e["lastPlayed"] = int(time.time())
        if session:
            e["sessions"] = e.get("sessions", 0) + 1
        _save(data)


def _safe_add(*a, **k):
    """_add, but a failed save never stops the timing (the next save catches up)."""
    try:
        _add(*a, **k)
        return True
    except OSError:
        import traceback
        traceback.print_exc()
        return False


def set_earlier(game_id, minutes):
    """The play time from before Junction counted a game, as the person typed it in (minutes; 0 or None removes it).
    It's kept apart from Junction's own count ("minutes"), so changing it never touches what Junction timed."""
    minutes = max(0.0, float(minutes or 0))
    with _lock:
        data = load()
        e = data.get(game_id)
        if minutes:
            data.setdefault(game_id, {"minutes": 0.0, "sessions": 0})["earlier"] = round(minutes, 2)
        elif e is not None:
            e.pop("earlier", None)
            if not e.get("minutes") and not e.get("sessions") and not e.get("lastPlayed"):
                data.pop(game_id)
        _save(data)


def apply(lib):
    """The library with Junction's own play time added. Returns a new dict; `lib` isn't changed.
    Games you added yourself aren't in the library (the page keeps them), so their times go along as `ownTimes`.
    Each game also says how much of its total Junction timed (`playtimeCounted`) and how much was typed in as
    earlier play time (`playtimeEarlier`), for the dialog where you change the latter."""
    data = load()
    if not data:
        return lib
    games = []
    for g in lib.get("games") or []:
        e = data.get(g["id"])
        if e:
            mine, theirs, earlier = e.get("minutes", 0), g.get("playtimeMinutes") or 0, e.get("earlier") or 0
            # A launcher's own total (Ubisoft's stats) already holds the time Junction saw, so take the larger.
            total = (max(mine, theirs) if g.get("playtimeFrom") == "launcher" else mine + theirs) + earlier
            g = dict(g, playtimeMinutes=round(total) or None, playtimeCounted=round(mine), playtimeEarlier=round(earlier),
                     lastPlayed=max(g.get("lastPlayed") or 0, e.get("lastPlayed") or 0) or None)
        games.append(g)
    own = {k: {"minutes": round(e.get("minutes", 0) + (e.get("earlier") or 0)) or None, "lastPlayed": e.get("lastPlayed"),
               "counted": round(e.get("minutes", 0)), "earlier": round(e.get("earlier") or 0)}
           for k, e in data.items() if k.startswith("manual:")}
    return dict(lib, games=games, ownTimes=own)


# ---------- which programs are running (Windows) ----------

_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel.OpenProcess.restype = wintypes.HANDLE
_kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel.CloseHandle.argtypes = [wintypes.HANDLE]
QUERY_LIMITED = 0x1000


def running_processes():
    """(process id, full path in lower case) of the programs running now (those Windows lets Junction look at)."""
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
                out.append((pid, buf.value.lower()))
        finally:
            _kernel.CloseHandle(h)
    return out


def running_programs():
    """The full paths of the programs running now (those Windows lets Junction look at), lower case."""
    return [path for _, path in running_processes()]


def _is_running(folder, programs=None, exe=None):
    """Whether a program from this folder runs (or, with `exe`, that exact program)."""
    programs = programs if programs is not None else running_programs()
    if exe:
        exe = os.path.normcase(os.path.abspath(exe))
        return any(os.path.normcase(p) == exe for p in programs)
    folder = os.path.normcase(os.path.abspath(folder)).rstrip("\\") + "\\"
    for p in programs:
        if p.startswith(folder) and not any(s in os.path.basename(p) for s in SKIP_EXES):
            return True
    return False


def _timeable(folder):
    """A folder Junction may time a game by: not a drive root (anything on that drive would count), and not
    inside Steam's library (Steam counts those itself, also for a Ubisoft or EA copy bought on Steam)."""
    d = os.path.normcase(os.path.abspath(folder or "")).rstrip("\\")
    return bool(folder) and d.count("\\") >= 1 and "\\steamapps\\common\\" not in d + "\\" and os.path.isdir(d)


def _folders(games):
    """{game id: install folder} for the installed games Junction may time without being asked: not a drive
    root or a folder holding another game's folder, so one game never counts for another."""
    out = {}
    for g in games:
        d = g.get("installDir")
        if not g.get("installed") or not d or not counts(g):
            continue
        d = os.path.normcase(os.path.abspath(d)).rstrip("\\")
        # Two copies in one folder (a Ubisoft game bought on Epic shows up in both): time only one of them,
        # or the title would count the same session twice.
        if _timeable(d) and d not in out.values():
            out[g["id"]] = d
    inside = {gid for gid, d in out.items() if any(o != d and o.startswith(d + "\\") for o in out.values())}
    return {gid: d for gid, d in out.items() if gid not in inside}


class Tracker:
    """Times one session per game. on_change() runs when a session starts or ends."""

    def __init__(self, on_change=lambda: None):
        self.on_change = on_change
        self.playing = {}  # game id -> start time
        self._watching = {}  # game id -> the folder (or program) it's timed by
        self._open = {}  # game id -> [time saved up to, time last seen running]
        self._lock = threading.Lock()  # one save of a session's time at a time (its own loop, or save_now)
        self._stopping = False

    def _key(self, folder, exe=None):
        return os.path.normcase(os.path.abspath(exe or folder)).rstrip("\\")

    def started(self, game, exe=None):
        """A game Junction started. With `exe` (a game you added yourself), only that program counts."""
        if not counts(game) or game["id"] in self._watching or self._stopping:
            return
        if not exe and not _timeable(game["installDir"]):
            return
        key = self._key(game["installDir"], exe)
        if key in self._watching.values():  # another copy of this game (same folder) is already being timed
            return
        self._watching[game["id"]] = key
        threading.Thread(target=self._watch, args=(game["id"], game["installDir"], exe), daemon=True).start()

    def _save(self, game_id):
        """Count a session's time since its last save. False when the save failed (it's tried again later)."""
        with self._lock:
            o = self._open.get(game_id)
            if not o or o[1] <= o[0]:
                return True
            if _safe_add(game_id, (o[1] - o[0]) / 60):
                o[0] = o[1]
                return True
            return False

    def save_now(self):
        """Save what's been played so far (Windows may be about to shut down). Timing carries on."""
        for game_id in list(self._open):
            self._save(game_id)

    def flush(self):
        """Junction is quitting: save the time of the games being played now, and stop timing."""
        self._stopping = True
        self.save_now()

    def look_around(self, games):
        """In the background, notice installed games started outside Junction. `games()` gives the library's games."""
        threading.Thread(target=self._look_loop, args=(games,), daemon=True).start()

    def _look_loop(self, games):
        while not self._stopping:
            time.sleep(LOOK_EVERY)
            if self._stopping:
                return
            try:
                busy = set(self._watching.values())
                folders = {gid: d for gid, d in _folders(games() or []).items()
                           if gid not in self._watching and self._key(d) not in busy}
                if not folders:
                    continue
                programs = running_programs()
                for gid, folder in folders.items():
                    if _is_running(folder, programs):
                        self._watching[gid] = self._key(folder)
                        threading.Thread(target=self._watch, args=(gid, folder), daemon=True).start()
            except Exception:  # noqa: BLE001 - never take the app down over a timer
                import traceback
                traceback.print_exc()

    def _watch(self, game_id, folder, exe=None):
        try:
            t0 = time.time()
            while not _is_running(folder, exe=exe):
                if time.time() - t0 > START_WAIT or self._stopping:
                    return
                time.sleep(3)
            self.playing[game_id] = start = checked = time.time()
            o = self._open[game_id] = [start, start]  # [saved up to, last seen running]
            asleep = 0.0
            _safe_add(game_id, session=True)  # shows up in Continue playing straight away
            self.on_change()
            missing = 0
            while missing < GONE_AFTER and not self._stopping:
                time.sleep(CHECK_EVERY)
                now = time.time()
                if now - checked > 3 * CHECK_EVERY:
                    # The PC slept (or hibernated) with the game open: that gap isn't play time.
                    self._save(game_id)
                    with self._lock:
                        asleep += now - o[1]
                        o[0] = o[1] = now
                checked = now
                missing = 0 if _is_running(folder, exe=exe) else missing + 1
                if missing == 0:
                    o[1] = now
                if now - o[0] >= SAVE_EVERY:
                    self._save(game_id)
            self._save(game_id)  # (also when quitting: saving twice counts nothing twice)
            if self._stopping:
                return
            print(f"Played {game_id} for {round((o[1] - start - asleep) / 60)} min", flush=True)
            self.playing.pop(game_id, None)
            self.on_change()
        except Exception:  # noqa: BLE001 - never take the app down over a timer
            import traceback
            traceback.print_exc()
        finally:
            self._watching.pop(game_id, None)
            self._open.pop(game_id, None)
            self.playing.pop(game_id, None)
