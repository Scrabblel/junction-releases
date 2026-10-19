"""Junction's desktop window: web/index.html in a native window, with this API behind it.

    python -m junction.app

The page calls these methods through window.pywebview.api (see `host` in web/index.html).
The window opens on the last scan straight away and refreshes it in the background; when fresh
data is ready, the page is told to redraw.
"""
import copy
import ctypes
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser

import webview

from . import config, keys, launchers, library, live_store, logs, pad, playtime, startup, steam_switch, titlebar, updates

LIB_VERSION = 1          # bump when the cached library's shape changes
ACH_TTL = 10 * 60        # seconds a game's achievements stay cached
WATCH_EVERY = 5          # seconds between checks for games installed or removed while the app is open
ENRICHED = ("friends", "friendOwners", "dlcSales", "news")
# Pages the settings dialogs may open in the browser (where people make their keys).
KEY_SITES = {"steamcommunity.com", "isthereanydeal.com", "www.steamgriddb.com"}


def _merge_extras(new, old):
    """Keep the slow extras of the previous library until enrich() has fresh ones."""
    if not old:
        return
    for k in ENRICHED:
        if k in old and k not in new:
            new[k] = old[k]
    pics = {a["steamId"]: a.get("avatar") for a in old.get("steamAccounts") or []}
    for a in new.get("steamAccounts") or []:
        a.setdefault("avatar", pics.get(a["steamId"]))
    prev = {g["id"]: g for g in old.get("games") or []}
    for g in new["games"]:
        p = prev.get(g["id"])
        if not p:
            continue
        for k in ("achievements", "sizeBytes"):
            if g.get(k) is None and p.get(k) is not None:
                g[k] = p[k]
        if g["launcher"] == "gog":  # GOG play time comes from enrich()
            g["playtimeMinutes"] = g.get("playtimeMinutes") or p.get("playtimeMinutes")
            g["lastPlayed"] = g.get("lastPlayed") or p.get("lastPlayed")


class Api:
    """Everything the page can ask for. Only public methods are visible to the page."""

    def __init__(self):
        self._window = None
        self._lib = None
        self._raw = None
        self._launch = config.read_cache("launch.json", {})
        self._lib_lock = threading.Lock()
        self._busy = threading.Lock()  # one background refresh at a time
        self._store = None
        self._store_lock = threading.Lock()
        self._store_refreshing = False
        self._ach = {}
        self._installed_sig = None
        self._tray = None
        self._quitting = False
        self._own_frame = False  # True once Junction draws its own title bar
        self._tracker = playtime.Tracker(self._played)
        self._updates = updates.Updater(lambda info: self._js(f"window.junctionUpdate && junctionUpdate({json.dumps(info)})"))

    # ---------- talking back to the page ----------

    def _js(self, code):
        try:
            if self._window:
                self._window.evaluate_js(code)
        except Exception:  # noqa: BLE001 - the window may be closing
            pass

    def _progress(self, msg):
        self._js(f"window.junctionProgress && junctionProgress({json.dumps(str(msg))})")

    def _push(self, what, info=None):
        self._js(f"window.junctionRefresh && junctionRefresh({json.dumps(what)}, {json.dumps(info)})")

    # ---------- library ----------

    def _scan(self):
        """Scan now, keep the extras of the last library, and remember how to launch each game."""
        lib, raw = library.scan(progress=self._progress)
        lib["version"] = LIB_VERSION
        _merge_extras(lib, self._lib)
        self._raw = raw
        self._launch = {g["id"]: g["launch"] for g in raw}
        config.write_cache("launch.json", self._launch)
        config.write_cache("library.json", lib)
        self._lib = lib
        return lib

    def _enrich(self):
        lib = copy.deepcopy(self._lib)
        store_appids = [sg["steamAppId"] for sg in ((self._store or {}).get("games") or {}).values() if sg.get("steamAppId")]
        library.enrich(lib, store_appids=store_appids)
        self._lib = lib
        config.write_cache("library.json", lib)
        self._push("library")

    def _background(self, rescan):
        if not self._busy.acquire(blocking=False):
            return
        try:
            if rescan:
                self._scan()
                self._push("library")
            self._enrich()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            self._busy.release()

    def _start(self, rescan):
        threading.Thread(target=self._background, args=(rescan,), daemon=True).start()

    # ---------- noticing installs and uninstalls ----------

    def _watch(self):
        """Every few seconds, ask the launchers which games are installed (a ~0.1 s local check).
        When that changes, rescan and tell the page which games came and went."""
        while True:
            time.sleep(WATCH_EVERY)
            lib = self._lib
            if not lib:
                continue
            try:
                found = launchers.find_all()
            except Exception:  # noqa: BLE001
                continue
            if found["errors"]:  # a launcher that failed to read would look like an uninstall
                continue
            now = {g["id"] for g in found["games"]}
            if self._installed_sig is None:
                self._installed_sig = {g["id"] for g in lib["games"] if g["installed"]}
            if now == self._installed_sig or not self._busy.acquire(blocking=False):
                continue
            before = self._installed_sig
            try:
                old = {g["id"]: g["name"] for g in lib["games"]}
                new = self._scan()
                library.add_sizes(new)
                config.write_cache("library.json", new)
                names = {g["id"]: g["name"] for g in new["games"]}
                self._push("library", {"installed": [names.get(i, i) for i in sorted(now - before)],
                                       "removed": [old.get(i, i) for i in sorted(before - now)]})
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            finally:
                self._installed_sig = now
                self._busy.release()

    def _view(self):
        """The library as the page sees it: with the play time Junction counted itself."""
        return playtime.apply(self._lib) if self._lib else self._lib

    def _played(self):
        """A game Junction started began or stopped: update Continue playing and the tray."""
        self._update_tray()
        self._push("library")

    def get_library(self):
        with self._lib_lock:
            if self._lib is None:
                cached = config.read_cache("library.json")
                if cached and cached.get("version") == LIB_VERSION:
                    self._lib = cached
                    self._start(rescan=True)   # show the last scan now, refresh behind it
                else:
                    with self._busy:
                        self._scan()           # first start: nothing to show yet
                    self._start(rescan=False)
            self._update_tray()
            return self._view()

    def rescan(self):
        with self._busy:
            self._scan()
        self._start(rescan=False)
        self._update_tray()
        return self._view()

    # ---------- playing ----------

    def _game(self, game_id):
        return next((g for g in (self._lib or {}).get("games", []) if g["id"] == game_id), None)

    def _run(self, game_id, steam_id, target):
        launch = self._launch.get(game_id)
        if game_id.startswith("manual:") and target:
            if not os.path.isfile(target):
                return {"error": f"{target} doesn't exist anymore."}
            launch = {"type": "exe", "target": target, "cwd": os.path.dirname(target)}
        if not launch:
            return {"error": "Junction can't find this game anymore. Press Rescan at the top right."}
        try:
            t = launch["target"]
            if launch["type"] == "uri":
                if steam_id and t.startswith("steam://"):
                    switched = steam_switch.switch_and_open(steam_id, t)
                    return {"ok": True, "switched": switched}
                os.startfile(t)
            elif launch["type"] == "exe":
                subprocess.Popen([t], cwd=launch.get("cwd") or os.path.dirname(t))
            else:  # "cmd": a launcher's own command line
                subprocess.Popen(t, cwd=launch.get("cwd"))
        except ValueError as e:
            return {"error": str(e)}
        except OSError as e:
            return {"error": f"Windows couldn't start it ({e.strerror or e})."}
        return {"ok": True}

    def play(self, game_id, steam_id=None, target=None):
        result = self._run(game_id, steam_id, target)
        game = self._game(game_id)
        if game_id.startswith("manual:") and target:
            game = {"id": game_id, "launcher": "manual", "installDir": os.path.dirname(target), "launch": target, "installed": True}
        if result.get("ok") and game and game.get("installed"):
            self._tracker.started(game)  # counts play time where no launcher does
        return result

    def install(self, game_id, steam_id=None, target=None):
        return self._run(game_id, steam_id, target)

    def open_folder(self, game_id, target=None):
        g = self._game(game_id)
        folder = (g or {}).get("installDir") or (os.path.dirname(target) if target else None)
        if not folder or not os.path.isdir(folder):
            return {"error": "That folder doesn't exist anymore."}
        os.startfile(folder)
        return {"ok": True}

    def get_downloads(self):
        try:
            return launchers.steam_downloads()
        except Exception:  # noqa: BLE001
            return []

    # ---------- game page details ----------

    def get_achievements(self, appid=None):
        """Without an app id: the empty shell the page starts from. With one: that game's achievements."""
        if appid is None:
            return {"icon": 48, "cols": 40, "games": {}, "perGame": True}
        appid = str(appid)
        hit = self._ach.get(appid)
        if hit and time.time() - hit[0] < ACH_TTL:
            return hit[1]
        owners = sorted({s for g in (self._lib or {}).get("games", []) if str(g.get("steamAppId")) == appid
                         for s in g.get("owners") or []})
        try:
            data = library.achievements_for(appid, owners)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            data = None
        self._ach[appid] = (time.time(), data)
        return data

    def get_media(self, appid):
        try:
            return library.media_for(appid)
        except Exception:  # noqa: BLE001
            return None

    # ---------- store ----------

    def _build_store(self):
        if self._raw is None:
            with self._busy:
                if self._raw is None:
                    self._scan()
        data = live_store.build(self._raw, (self._lib or {}).get("steamAccounts") or [], progress=self._progress)
        if data:
            config.write_cache("store.json", data)
            self._store = data
        return data

    def _refresh_store(self):
        try:
            self._build_store()
            self._push("store")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            self._store_refreshing = False

    def get_store(self):
        with self._store_lock:
            if self._store is None:
                self._store = config.read_cache("store.json")
            if self._store is None:
                return self._build_store()
            age = config.cache_age("store.json")
            if age is not None and age > live_store.stale() and not self._store_refreshing:
                self._store_refreshing = True
                threading.Thread(target=self._refresh_store, daemon=True).start()
            return self._store

    def get_price_history(self, itad_id):
        """A year of best prices for one Store game, for its page. None when it can't be fetched."""
        itad_id = str(itad_id)
        try:
            line = live_store.history(itad_id)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return None
        # Kept with the prices, so the page has it again if it reloads them.
        game = ((self._store or {}).get("games") or {}).get(itad_id)
        if game is not None and line is not None:
            game["history"] = line
        return line

    def search_prices(self, query):
        try:
            return live_store.search(query)
        except Exception:  # noqa: BLE001
            return []

    # ---------- settings: the API keys, entered in the app ----------

    def get_settings(self):
        s = config.load_settings()
        return {**keys.overview(), "version": config.VERSION, "setupDone": bool(s.get("setupDone")),
                "closeToTray": s.get("closeToTray", True), "startWithWindows": startup.enabled(),
                "tray": self._tray is not None}

    def set_option(self, name, value):
        """The on/off settings: setupDone, closeToTray, startWithWindows."""
        value = bool(value)
        try:
            if name == "startWithWindows":
                startup.set_enabled(value)
            elif name in ("setupDone", "closeToTray"):
                s = config.load_settings()
                s[name] = value
                config.save_settings(s)
            else:
                return {"error": "Unknown setting."}
        except OSError as e:
            return {"error": f"Windows didn't allow that ({e.strerror or e})."}
        return {"ok": True}

    # ---------- when something goes wrong ----------

    def _report(self):
        return logs.report(self._lib, {"Tray": "on" if self._tray else "off"})

    def problem_report(self):
        return self._report()

    def copy_report(self):
        return {"ok": logs.copy_to_clipboard(self._report())}

    def show_report(self):
        """Save the report as a file and show it in Explorer, to attach to a message."""
        os.makedirs(logs.LOG_DIR, exist_ok=True)
        with open(logs.REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(self._report())
        subprocess.Popen(["explorer", "/select,", logs.REPORT_FILE])
        return {"ok": True}

    def log_error(self, message):
        logs.log.error("Page: %s", str(message)[:2000])

    # ---------- the window and the tray ----------

    def _update_tray(self):
        if not self._tray or not self._lib:
            return
        seen, recent = set(), []
        for g in sorted(self._view()["games"], key=lambda g: g.get("lastPlayed") or 0, reverse=True):
            name = g["name"].strip()
            if not g.get("installed") or not g.get("lastPlayed") or g.get("hiddenByDefault") or name.lower() in seen:
                continue
            seen.add(name.lower())
            recent.append((g["id"], name))
            if len(recent) == 6:
                break
        self._tray.recent = recent

    def show_window(self):
        w = self._window
        if not w:
            return
        try:
            w.show()
            hwnd = int(str(w.native.Handle.ToInt64()))
            if ctypes.windll.user32.IsIconic(hwnd):
                w.restore()
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def window_info(self):
        return {"custom": self._own_frame, "maximized": bool(self._window) and titlebar.is_maximized(self._window)}

    def window_action(self, what):
        """The page's own minimize, maximize/restore and close buttons."""
        w = self._window
        if what == "minimize":
            w.minimize()
        elif what == "maximize":
            w.restore() if titlebar.is_maximized(w) else w.maximize()
        elif what == "close" and self._closing():  # same as Windows' close button: to the tray, if that's on
            self._quit()

    def _window_state(self):
        self._js(f"window.junctionWindowState && junctionWindowState({json.dumps(titlebar.is_maximized(self._window))})")

    def _tray_play(self, game_id):
        self._js(f"window.junctionTrayPlay && junctionTrayPlay({json.dumps(game_id)})")

    def _quit(self):
        self._quitting = True
        if self._window:
            self._window.destroy()

    def _closing(self):
        """Closing the window: hide it in the tray instead, unless that's switched off."""
        settings = config.load_settings()
        if self._quitting or not self._tray or not settings.get("closeToTray", True):
            return True
        threading.Thread(target=self._window.hide, daemon=True).start()
        if not settings.get("trayTipShown"):
            self._tray.tip("Junction is still running", "It's here in the tray. Right-click it for your recent games or to quit.")
            settings["trayTipShown"] = True
            config.save_settings(settings)
        return False

    def _after_key(self, result, then):
        if result.get("ok"):
            threading.Thread(target=then, daemon=True).start()
        return result

    def set_steam_key(self, steam_id, key):
        def refresh():
            self._ach.clear()
            self._background(rescan=True)  # that account's games, play time and achievements
        return self._after_key(keys.set_steam_key(steam_id, key), refresh)

    def remove_steam_account(self, steam_id):
        def refresh():
            self._ach.clear()
            self._background(rescan=True)  # its games leave the library
        return self._after_key(keys.remove_account(steam_id), refresh)

    def restore_steam_account(self, steam_id):
        return self._after_key(keys.restore_account(steam_id), lambda: self._background(rescan=True))

    def set_prices_key(self, key):
        def refresh():
            if self._store_refreshing:
                return
            self._store_refreshing = True
            self._refresh_store()
        return self._after_key(keys.set_prices_key(key), refresh)

    def set_art_key(self, key):
        return self._after_key(keys.set_art_key(key), lambda: self._background(rescan=True))

    # ---------- updates ----------

    def update_info(self):
        return self._updates.info

    def check_updates(self):
        return self._updates.check()

    def install_update(self):
        return self._updates.install(
            progress=lambda p: self._js(f"window.junctionUpdateProgress && junctionUpdateProgress({json.dumps(p)})"),
            quit_app=self._quit)

    def whats_new(self):
        """Once, on the first start after an update: the notes of every version since the one that ran before.
        None otherwise (also on a brand-new install, which gets the welcome instead)."""
        s = config.load_settings()
        last = s.get("lastVersion")
        if last == config.VERSION:
            return None
        s["lastVersion"] = config.VERSION
        try:
            config.save_settings(s)
        except OSError:
            return None
        # Versions before 0.9.9 didn't remember theirs; someone already set up has updated from one of them.
        if not last:
            if not s.get("setupDone"):
                return None
            last = "0.9.8"
        old, new = updates.parse_version(last), updates.parse_version(config.VERSION)
        if not old or not new or old >= new:
            return None
        try:
            releases = updates.notes_since(last)
        except Exception as e:  # noqa: BLE001 - offline: still say it updated, the notes are on GitHub
            logging.getLogger("junction").warning("Couldn't load what's new: %s", e)
            releases = []
        return {"version": config.VERSION, "from": last, "releases": releases,
                "page": f"https://github.com/{updates.RELEASES}/releases"}

    def open_url(self, url):
        """Open one of the key sites, or Junction's release page, in the person's browser."""
        u = urllib.parse.urlparse(str(url))
        releases = u.hostname == "github.com" and u.path.lower().startswith(f"/{updates.RELEASES}/")
        if u.scheme != "https" or not (u.hostname in KEY_SITES or releases):
            return {"error": "Junction only opens the sites where you make your keys."}
        webbrowser.open(url)
        return {"ok": True}


# Only one Junction at a time: starting it again brings the open one to the front.
_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel.CreateMutexW.restype = _kernel.CreateEventW.restype = _kernel.OpenEventW.restype = ctypes.c_void_p
_kernel.OpenMutexW.restype = ctypes.c_void_p
_kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_kernel.SetEvent.argtypes = _kernel.CloseHandle.argtypes = [ctypes.c_void_p]
_instance = None


def _first_instance(quiet):
    global _instance
    _instance = _kernel.CreateMutexW(None, False, f"Local\\{config.INSTANCE}App")
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return True
    if not quiet:
        ev = _kernel.OpenEventW(0x0002, False, f"Local\\{config.INSTANCE}Show")  # EVENT_MODIFY_STATE
        if ev:
            _kernel.SetEvent(ev)
    return False


def _listen_for_second_start(api):
    ev = _kernel.CreateEventW(None, False, False, f"Local\\{config.INSTANCE}Show")
    while ev:
        _kernel.WaitForSingleObject(ev, 0xFFFFFFFF)
        api.show_window()


def _listen_for_quit(api):
    ev = _kernel.CreateEventW(None, False, False, f"Local\\{config.INSTANCE}Quit")
    if ev:
        _kernel.WaitForSingleObject(ev, 0xFFFFFFFF)
        logging.getLogger("junction").info("Asked to quit (installer)")
        api._quit()


def _ask_to_quit(wait=15):
    """`Junction.exe --quit`, which the installer runs: closes a running Junction and waits until it's gone."""
    ev = _kernel.OpenEventW(0x0002, False, f"Local\\{config.INSTANCE}Quit")  # EVENT_MODIFY_STATE
    if ev:
        _kernel.SetEvent(ev)
    # The single-instance mutex lasts as long as that Junction does.
    end = time.time() + wait
    while time.time() < end:
        mutex = _kernel.OpenMutexW(0x00100000, False, f"Local\\{config.INSTANCE}App")  # SYNCHRONIZE
        if not mutex:
            return
        _kernel.CloseHandle(mutex)
        time.sleep(0.2)


def http_port():
    """The port the page is served on: Junction's usual one, unless another program already holds it.

    Then this run takes a free port instead. Otherwise the window would open that program's page and hand it
    Junction's API. (The page's saved favourites and covers belong to the usual port, so they're back next run.)"""
    import socket
    for port in (config.HTTP_PORT, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(("127.0.0.1", port))
            if port != config.HTTP_PORT:
                logging.getLogger("junction").warning("Port %s is taken by another program; using %s", config.HTTP_PORT, s.getsockname()[1])
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return None


def page_url():
    """web/index.html, with the version and the file's date in its address.

    pywebview's server means to tell WebView2 not to keep the page, but those headers get lost on the way, so
    after an update WebView2 could go on showing the previous version's page for hours. An address it has never
    seen can't match an old copy.
    """
    path = config.resource("web", "index.html")
    try:
        stamp = int(os.path.getmtime(path))
    except OSError:
        stamp = 0
    return f"{path}?v={config.VERSION}-{stamp}"


def _pad_sender(api):
    """Controller presses go to the page from their own thread, so reading the controller never waits for the
    page: a press made while the page is busy is handled right after it instead of being missed."""
    presses = queue.Queue(maxsize=8)  # a few at most; holding a direction while the page is stuck mustn't pile up

    def send():
        while True:
            api._js(f"window.junctionPad && junctionPad({json.dumps(presses.get())})")

    threading.Thread(target=send, daemon=True).start()

    def press(k):
        try:
            presses.put_nowait(k)
        except queue.Full:
            pass
    return press


def main():
    if "--quit" in sys.argv:
        return _ask_to_quit()
    in_tray = "--tray" in sys.argv  # started with Windows: stay in the tray
    if not _first_instance(quiet=in_tray):
        return
    logs.setup()
    api = Api()
    icon = config.resource("web", "icon.ico")
    own_frame = titlebar.prepare()
    window = webview.create_window(
        "Junction", url=page_url(), js_api=api, hidden=in_tray,
        width=1440, height=900, min_size=(900, 600), background_color="#0B0E13", text_select=False)
    api._window = window
    try:
        from . import tray
        api._tray = tray.Tray(icon, on_open=api.show_window, on_play=api._tray_play, on_quit=api._quit)
        api._tray.start()
    except Exception:  # noqa: BLE001 - Junction works without its tray icon
        traceback.print_exc()
        api._tray = None
    window.events.closing += api._closing
    if own_frame:  # Junction draws its own title bar; take Windows' away before the window first shows
        window.events.before_show += lambda: setattr(api, "_own_frame", titlebar.attach(window))
        window.events.maximized += api._window_state
        window.events.restored += api._window_state
    threading.Thread(target=api._watch, daemon=True).start()
    api._updates.start()
    threading.Thread(target=_listen_for_second_start, args=(api,), daemon=True).start()
    threading.Thread(target=_listen_for_quit, args=(api,), daemon=True).start()
    # Controllers, read from Windows and passed to the page as button names.
    threading.Thread(target=pad.run, daemon=True, args=(
        _pad_sender(api),
        lambda on: api._js(f"window.junctionPadConnected && junctionPadConnected({json.dumps(on)})"))).start()
    # Keep the page's own storage (favourites, collections, covers) between runs.
    storage = os.path.join(os.path.dirname(config.CACHE_DIR), "webview")
    webview.start(private_mode=False, storage_path=storage, icon=icon, http_port=http_port())
    if api._tray:
        api._tray.stop()
    logging.getLogger("junction").info("Junction closed")


if __name__ == "__main__":
    main()
