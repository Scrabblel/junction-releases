"""Which of the games Junction started are running now, and what the Play button can do with one that is:
bring its window to the front, or close it.

After Play, Junction waits for a program from the game's folder (for a game you added yourself, that exact
program) to start, says "Playing" until the last of them is gone, and Play again after that. This works the
same for every launcher, Steam included: it only looks at which programs run, never inside them.
"""
import ctypes
import os
import threading
import time
import traceback
from ctypes import wintypes

from . import playtime

START_WAIT = playtime.START_WAIT  # a launcher may update or sign in first
CHECK_EVERY = 3                   # seconds between checks while a game runs
GONE_AFTER = 2                    # checks in a row without the game before it counts as closed
CLOSE_WAIT = 8                    # seconds a game gets to close itself before Junction ends it

_user = ctypes.WinDLL("user32", use_last_error=True)
_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user.EnumWindows.argtypes = [_EnumProc, wintypes.LPARAM]
_user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user.IsWindowVisible.argtypes = [wintypes.HWND]
_user.IsIconic.argtypes = [wintypes.HWND]
_user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user.GetWindow.restype = wintypes.HWND
_user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user.SetForegroundWindow.argtypes = [wintypes.HWND]
_user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_kernel.OpenProcess.restype = wintypes.HANDLE
_kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel.CloseHandle.argtypes = [wintypes.HANDLE]
GW_OWNER, SW_RESTORE, WM_CLOSE, TERMINATE = 4, 9, 0x0010, 0x0001

# Folders that hold many programs: a game "installed" straight into one of them can't be told apart.
_SHARED = ("\\program files", "\\program files (x86)", "\\windows", "\\xboxgames", "\\steamapps\\common",
           "\\epic games", "\\gog galaxy\\games", "\\ea games", "\\ubisoft game launcher\\games")


def _norm(path):
    return os.path.normcase(os.path.abspath(path)).rstrip("\\")


def watchable(folder, exe=None):
    """Whether a game can be watched by this folder (or program): it exists, and it's the game's own folder."""
    if exe:
        return os.path.isfile(exe)
    if not folder or not os.path.isdir(folder):
        return False
    d = _norm(folder)
    return d.count("\\") >= 1 and not d[2:].endswith(_SHARED) and "\\windows\\" not in d + "\\"


def processes(folder, exe=None):
    """The ids of the game's programs running now: any from its folder, or with `exe`, that program."""
    if exe:
        exe = _norm(exe)
        return [pid for pid, p in playtime.running_processes() if os.path.normcase(p) == exe]
    d = _norm(folder) + "\\"
    return [pid for pid, p in playtime.running_processes()
            if os.path.normcase(p).startswith(d) and not any(s in os.path.basename(p) for s in playtime.SKIP_EXES)]


def windows(pids):
    """The game's own windows: visible, top-level, with a title. Biggest first (the game, not a splash or chat)."""
    pids, out = set(pids), []

    def each(hwnd, _):
        pid = wintypes.DWORD()
        _user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and _user.IsWindowVisible(hwnd) and not _user.GetWindow(hwnd, GW_OWNER) \
                and _user.GetWindowTextLengthW(hwnd) > 0:
            r = wintypes.RECT()
            _user.GetWindowRect(hwnd, ctypes.byref(r))
            out.append(((r.right - r.left) * (r.bottom - r.top), hwnd))
        return True

    _user.EnumWindows(_EnumProc(each), 0)
    return [hwnd for _, hwnd in sorted(out, key=lambda x: -x[0])]


class Games:
    """The games Junction started: "starting" until a program of theirs runs, then "playing" until it's gone.
    on_change() runs on every change."""

    def __init__(self, on_change=lambda: None):
        self.on_change = on_change
        self._lock = threading.Lock()
        self._games = {}  # game id -> {"state", "since", "folder", "exe"}

    def view(self):
        """{game id: {"state": "starting" | "playing", "since": seconds}} for the page."""
        with self._lock:
            return {gid: {"state": g["state"], "since": g["since"]} for gid, g in self._games.items()}

    def started(self, game_id, folder, exe=None):
        """Play was pressed: watch for the game. Nothing happens for a game that's already watched or can't be."""
        if not watchable(folder, exe):
            return
        with self._lock:
            if game_id in self._games:
                return
            self._games[game_id] = {"state": "starting", "since": time.time(), "folder": folder, "exe": exe}
        self.on_change()
        threading.Thread(target=self._watch, args=(game_id, folder, exe), daemon=True).start()

    def _watch(self, game_id, folder, exe):
        try:
            t0 = time.time()
            while not processes(folder, exe):
                if time.time() - t0 > START_WAIT:
                    return
                time.sleep(CHECK_EVERY)
            with self._lock:
                self._games[game_id].update(state="playing", since=time.time())
            self.on_change()
            missing = 0
            while missing < GONE_AFTER:
                time.sleep(CHECK_EVERY)
                missing = 0 if processes(folder, exe) else missing + 1
        except Exception:  # noqa: BLE001 - never take the app down over this
            traceback.print_exc()
        finally:
            with self._lock:
                self._games.pop(game_id, None)
            self.on_change()

    def _where(self, game_id):
        with self._lock:
            g = self._games.get(game_id)
            return (g["folder"], g["exe"]) if g else (None, None)

    def focus(self, game_id):
        """Bring the game's window to the front. False when it has none (yet)."""
        folder, exe = self._where(game_id)
        if not folder and not exe:
            return False
        wins = windows(processes(folder, exe))
        if not wins:
            return False
        if _user.IsIconic(wins[0]):
            _user.ShowWindow(wins[0], SW_RESTORE)
        return bool(_user.SetForegroundWindow(wins[0]))

    def stop(self, game_id):
        """Close the game: first ask its windows to close (as their X button would), then end what's left.
        Returns "closed", "ended" (it had to be forced) or None (it isn't running)."""
        folder, exe = self._where(game_id)
        if not folder and not exe:
            return None
        pids = processes(folder, exe)
        if not pids:
            return None
        for hwnd in windows(pids):
            _user.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        end = time.time() + CLOSE_WAIT
        while time.time() < end:
            time.sleep(0.5)
            if not processes(folder, exe):
                return "closed"
        for pid in processes(folder, exe):
            h = _kernel.OpenProcess(TERMINATE, False, pid)
            if h:
                try:
                    _kernel.TerminateProcess(h, 1)
                finally:
                    _kernel.CloseHandle(h)
        return "ended"
