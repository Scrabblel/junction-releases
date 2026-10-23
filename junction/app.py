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
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser

import webview

from . import (art, config, ea_account, epic_account, keys, launchers, library, live_store, logs, pad, playtime, running,
               signins, startup, steam_switch, titlebar, ubisoft_account, updates)

LIB_VERSION = 1          # bump when the cached library's shape changes
ACH_TTL = 10 * 60        # seconds a game's achievements stay cached
WATCH_EVERY = 5          # seconds between checks for games installed or removed while the app is open
STORE_CHECK = 30 * 60    # seconds between checks whether the Store's prices are due again (also while in the tray)
ENRICHED = ("friends", "friendOwners", "dlcSales", "news")
# Pages the settings dialogs may open in the browser (where people make their keys).
KEY_SITES = {"steamcommunity.com", "isthereanydeal.com", "www.steamgriddb.com"}
# Launchers you can sign in to for their play time: (module, name).
SIGN_INS = {"epic": (epic_account, "Epic"), "ea": (ea_account, "EA"), "ubisoft": (ubisoft_account, "Ubisoft")}


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
    signed_in = signins.overview()  # after signing out, that store's numbers go
    for g in new["games"]:
        p = prev.get(g["id"])
        if not p:
            continue
        for k in ("achievements", "sizeBytes"):
            if g.get(k) is None and p.get(k) is not None:
                g[k] = p[k]
        # (Ubisoft's play time is also on this PC, signed in or not.)
        if (p.get("playtimeFrom") or g["launcher"] in ("gog", "battlenet")) and (
                g["launcher"] not in SIGN_INS or g["launcher"] == "ubisoft" or g["launcher"] in signed_in):
            # launcher play time comes from enrich()
            if not g.get("playtimeMinutes") and p.get("playtimeMinutes"):
                g["playtimeMinutes"], g["playtimeFrom"] = p["playtimeMinutes"], p.get("playtimeFrom")
            g["lastPlayed"] = g.get("lastPlayed") or p.get("lastPlayed")


def _take_enriched(lib, enriched):
    """Put what a refresh found (friends, news, achievements, sizes, launcher play time) into `lib`, a newer scan
    that finished while the refresh ran. Unlike _merge_extras, the refresh's values win."""
    for k in ENRICHED:
        if k in enriched:
            lib[k] = enriched[k]
    pics = {a["steamId"]: a.get("avatar") for a in enriched.get("steamAccounts") or []}
    for a in lib.get("steamAccounts") or []:
        if pics.get(a["steamId"]):
            a["avatar"] = pics[a["steamId"]]
    found = {g["id"]: g for g in enriched.get("games") or []}
    for g in lib.get("games") or []:
        e = found.get(g["id"])
        if not e:
            continue
        if e.get("achievements") is not None:
            g["achievements"] = e["achievements"]
        if e.get("sizeBytes") is not None and g.get("installed") and g.get("installDir") == e.get("installDir"):
            g["sizeBytes"] = e["sizeBytes"]
        if e.get("playtimeFrom") == "launcher" and e.get("playtimeMinutes"):
            g["playtimeMinutes"], g["playtimeFrom"] = e["playtimeMinutes"], "launcher"
        g["lastPlayed"] = max(g.get("lastPlayed") or 0, e.get("lastPlayed") or 0) or None


BIG_WIDTH = 1280  # Big screen: how many pixels across the page is drawn for, before it's zoomed to the screen


class Api:
    """Everything the page can ask for. Only public methods are visible to the page."""

    def __init__(self):
        self._window = None
        self._big = False          # the Big screen layout: drawn larger to fill the screen
        self._full_later = None    # full screen to switch to once the window shows (it starts hidden in the tray)
        self._lib = None
        self._raw = None
        self._launch = config.read_cache("launch.json", {})
        self._lib_lock = threading.Lock()
        self._busy = threading.Lock()  # one background refresh at a time
        self._scan_lock = threading.RLock()  # one scan at a time (a refresh, Rescan or a new install)
        self._scanning = False
        self._swap = threading.Lock()  # replacing self._lib (a published library is never changed in place)
        self._wanted = 0  # a refresh asked for while one runs: 1 = refresh, 2 = rescan first
        self._wanted_lock = threading.Lock()
        self._store = None
        self._store_lock = threading.Lock()
        self._store_refreshing = False
        self._ach = {}
        self._installed_sig = None
        self._tray = None
        self._quitting = False
        self._own_frame = False  # True once Junction draws its own title bar
        self._tracker = playtime.Tracker(self._played)
        self._running = running.Games(lambda: self._js(f"window.junctionRunning && junctionRunning({json.dumps(self._running.view())})"))
        self._sign_in_lock = threading.Lock()
        self._programs_lock = threading.Lock()  # one "Start this program?" at a time
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

    def _scan(self, sizes=False):
        """Scan now, keep the extras of the last library, and remember how to launch each game.
        With sizes=True the disk sizes are measured before the library is published (the install watch)."""
        with self._scan_lock:
            self._scanning = True
            try:
                return self._scan_now(sizes)
            finally:
                self._scanning = False

    def _scan_now(self, sizes):
        lib, raw = library.scan(progress=self._progress)
        lib["version"] = LIB_VERSION
        if sizes:
            library.add_sizes(lib)
        with self._swap:
            _merge_extras(lib, self._lib)
            self._raw = raw
            self._launch = {g["id"]: g["launch"] for g in raw}
            self._lib = lib
        config.write_cache("launch.json", self._launch)
        config.write_cache("library.json", lib)
        # What's installed now, so the install watch doesn't rescan (and toast) for what this scan already saw.
        self._installed_sig = {g["id"] for g in raw if g.get("installed")}
        return lib

    def _enrich(self):
        base = self._lib
        lib = copy.deepcopy(base)
        store_appids = [sg["steamAppId"] for sg in ((self._store or {}).get("games") or {}).values() if sg.get("steamAppId")]
        library.enrich(lib, store_appids=store_appids)
        with self._swap:
            if self._lib is base:
                self._lib = lib
            else:  # a scan (Rescan, a new install) finished meanwhile: keep its games, add what this found
                merged = copy.deepcopy(self._lib)
                _take_enriched(merged, lib)
                self._lib = merged
            current = self._lib
        config.write_cache("library.json", current)
        self._push("library")

    def _background(self, rescan):
        """Refresh behind the page. Asked again while one runs, the running one goes round once more when it's
        done (with a rescan if any of the asks wanted one), so a saved key or a sign-in is never left out."""
        with self._wanted_lock:
            self._wanted = max(self._wanted, 2 if rescan else 1)
        while self._wanted and not self._quitting:
            if not self._busy.acquire(blocking=False):
                return  # the one running picks it up
            try:
                with self._wanted_lock:
                    rescan, self._wanted = self._wanted == 2, 0
                if rescan:
                    try:
                        self._scan()
                        self._push("library")
                    except Exception:  # noqa: BLE001 - offline or a launcher hiccup: the extras still refresh
                        traceback.print_exc()
                if self._lib:
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
            if self._installed_sig is None:
                self._installed_sig = {g["id"] for g in lib["games"] if g["installed"]}
            # A launcher that couldn't be read just now would look like an uninstall: keep its games as they were.
            failed = set(found["errors"])
            now = {g["id"] for g in found["games"]} | {i for i in self._installed_sig if i.split(":", 1)[0] in failed}
            # The last scan couldn't read a launcher that reads fine now: its games may be missing, so scan again.
            stale = not failed and any(k in launchers.LAUNCHERS for k in (lib.get("errors") or {}))
            if (now == self._installed_sig and not stale) or self._scanning or self._quitting:
                continue
            before = self._installed_sig
            try:
                old = {g["id"]: g["name"] for g in lib["games"]}
                new = self._scan(sizes=True)
                names = {g["id"]: g["name"] for g in new["games"]}
                self._push("library", {"installed": [names.get(i, i) for i in sorted(now - before)],
                                       "removed": [old.get(i, i) for i in sorted(before - now)]})
                self._installed_sig = now  # exactly what this look saw, so the watch settles
            except Exception:  # noqa: BLE001 - offline, say: try again in a minute (the change is still there)
                traceback.print_exc()
                time.sleep(60)

    def _view(self):
        """The library as the page sees it: with the play time Junction counted itself, and which launcher
        sign-ins have run out (so the page can ask to sign in again)."""
        return dict(playtime.apply(self._lib), signins=signins.overview()) if self._lib else self._lib

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
                    self._scan()               # first start: nothing to show yet
                    self._start(rescan=False)
            self._update_tray()
            return self._view()

    def rescan(self):
        self._scan()  # doesn't wait for the slower refresh behind the page; that one keeps this scan's games
        self._start(rescan=False)
        self._update_tray()
        return self._view()

    # ---------- playing ----------

    def _game(self, game_id):
        return next((g for g in (self._lib or {}).get("games", []) if g["id"] == game_id), None)

    @staticmethod
    def _program_known(path):
        """A program added by hand that the person already said yes to."""
        return os.path.normcase(os.path.abspath(path)) in (config.load_settings().get("allowedPrograms") or [])

    def _program_allowed(self, path):
        """A game added by hand is only a path the page hands over, so the first time each program starts, Windows
        asks whether that's right. Otherwise anything that got into the page could start any program on the PC."""
        key = os.path.normcase(os.path.abspath(path))
        with self._programs_lock:
            if self._program_known(path):
                return True
            if not _ask_windows(f"Start this program?\n\n{path}\n\nIt was added to Junction by hand. Junction asks once "
                                "for each program, so it never starts one you didn't choose."):
                return False
            s = config.load_settings()
            s["allowedPrograms"] = (s.get("allowedPrograms") or []) + [key]
            config.save_settings(s)
            return True

    def _run(self, game_id, steam_id, target):
        launch = self._launch.get(game_id)
        if game_id.startswith("manual:") and target:
            if not os.path.isfile(target):
                return {"error": f"{target} doesn't exist anymore."}
            if not self._program_allowed(target):
                return {"error": "Junction didn't start it."}
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
        exe = None
        if game_id.startswith("manual:") and target:
            game = {"id": game_id, "launcher": "manual", "installDir": os.path.dirname(target), "launch": target, "installed": True}
            exe = target
        if result.get("ok") and game and game.get("installed"):
            self._tracker.started(game, exe=exe)  # counts play time where no launcher does
            # "Playing" until it closes; the page needs to know when the game can't be watched
            result["watching"] = self._running.started(game_id, game.get("installDir"), exe)
        return result

    def get_running(self):
        """The games Junction started that are starting or running now."""
        return self._running.view()

    def focus_game(self, game_id):
        """Bring a running game's window to the front."""
        return {"ok": True} if self._running.focus(str(game_id)) else {"error": "Junction couldn't switch to the game. Use Alt+Tab to get to it."}

    def stop_game(self, game_id):
        """Close a running game (asked first in the page)."""
        how = self._running.stop(str(game_id))
        return {"ok": True, "how": how} if how else {"error": "The game isn't running anymore."}

    def install(self, game_id, steam_id=None, target=None):
        return self._run(game_id, steam_id, target)

    def uninstall(self, game_id):
        """Hand the uninstall to the game's own launcher (or uninstaller). Junction never deletes game files itself;
        the install watcher notices when the game is gone."""
        game = self._game(str(game_id or ""))
        if not game or not game.get("installed"):
            return {"error": "Junction can't find this game anymore. Press Rescan at the top right."}
        how = launchers.uninstall_for(game)
        if not how:
            name = launchers.LAUNCHERS.get(game.get("launcher"), "its launcher")
            return {"error": f"Junction can't uninstall this game. Uninstall it in {name} instead."}
        try:
            if how["type"] == "uri":
                os.startfile(how["target"])
            else:
                exe, args = (how["target"], "") if how["type"] == "exe" else launchers._split_command(how["target"])
                if not exe:
                    return {"error": "Windows' uninstall entry for this game looks broken. Uninstall it in Windows Settings instead."}
                # Through the Windows shell, so an uninstaller that needs administrator rights can ask for them.
                os.startfile(exe, "open", args, os.path.dirname(exe) if os.path.isabs(exe) else None)
        except OSError as e:
            return {"error": f"Windows couldn't start the uninstall ({e.strerror or e})."}
        logging.getLogger("junction").info("Uninstall handed to %s for %s", how.get("where") or game.get("launcher"), game_id)
        return {"ok": True, "how": how["how"], "where": how.get("where")}

    def set_earlier_playtime(self, game_id, hours):
        """Hours played before Junction counted this game, typed in by the person (0 removes them)."""
        game_id = str(game_id or "")
        if not (self._game(game_id) or game_id.startswith("manual:")):
            return {"error": "Junction can't find this game anymore."}
        try:
            hours = float(str(hours).replace(",", ".") or 0)
        except ValueError:
            return {"error": "Type the hours as a number, like 86 or 12.5."}
        if not 0 <= hours <= 100000:
            return {"error": "Type a number of hours between 0 and 100,000."}
        try:
            playtime.set_earlier(game_id, hours * 60)
        except OSError:
            return {"error": "Junction couldn't save that. Try again."}
        self._push("library")
        return {"ok": True}

    def open_folder(self, game_id, target=None):
        g = self._game(game_id)
        folder = (g or {}).get("installDir")
        if not folder and target:
            # A game added by hand: only the folder of a program the person said yes to (see _program_allowed).
            if not self._program_known(target):
                return {"error": "Start the game once first, so Junction knows its program."}
            folder = os.path.dirname(target)
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
        """Without an id: the empty shell the page starts from. With a Steam app id, or a GOG or Epic game's own id
        ("gog:<product id>", "epic:<app name>", "ea:<id>"): that game's achievements."""
        if appid is None:
            return {"icon": 48, "cols": 40, "games": {}, "perGame": True}
        appid = str(appid)
        hit = self._ach.get(appid)
        if hit and time.time() - hit[0] < ACH_TTL:
            return hit[1]
        try:
            if appid.startswith("gog:"):
                data = library.gog_achievements_for(appid[4:])
            elif appid.startswith(("epic:", "ea:")):
                g = next((g for g in (self._lib or {}).get("games", []) if g["id"] == appid), None)
                if not g:
                    data = None
                elif g["launcher"] == "epic":
                    ns = library.epic_namespace(g)
                    data = epic_account.achievement_list(ns) if ns else None
                else:  # EA knows its games by name here, as for play time
                    data = ea_account.achievement_list(art.normalize(g["name"]))
            else:
                owners = sorted({s for g in (self._lib or {}).get("games", []) if str(g.get("steamAppId")) == appid
                                 for s in g.get("owners") or []})
                data = library.achievements_for(appid, owners)
        except Exception:  # noqa: BLE001 - Steam didn't answer: ask again next time instead of keeping "none"
            traceback.print_exc()
            return None
        self._ach[appid] = (time.time(), data)
        return data

    def get_media(self, appid):
        try:
            return library.media_for(appid)
        except Exception:  # noqa: BLE001
            return None

    # ---------- store ----------

    def _build_store(self):
        with self._scan_lock:  # a scan already under way is waited for, not done twice
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
            if self._store is not None and self._store.get("country", "NL") != config.country():
                self._store = None  # fetched for another country: those prices are in the wrong currency
            if self._store is None:
                return self._build_store()
            age = config.cache_age("store.json")
            if age is not None and age > live_store.stale() and not self._store_refreshing:
                self._store_refreshing = True
                threading.Thread(target=self._refresh_store, daemon=True).start()
            return self._store

    def _keep_prices_fresh(self):
        """Junction left in the tray still checks prices now and then, so it can tell about wishlist deals."""
        while not self._quitting:
            time.sleep(STORE_CHECK)
            try:
                with self._store_lock:
                    age = config.cache_age("store.json")
                    if age is None or age <= live_store.stale() or self._store_refreshing:
                        continue
                    self._store_refreshing = True
                self._refresh_store()
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    def deal_alert(self, title, text):
        """The page found wishlist games that got cheaper: a Windows notice that opens the Wishlist."""
        if not self._tray or not config.load_settings().get("dealAlerts", True):
            return {"ok": False}
        self._tray.tip(str(title), str(text), on_click=lambda: self._js("window.junctionGoto && junctionGoto('wishlist')"))
        return {"ok": True}

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
        deals = (game or {}).get("deals") or []
        if line and deals and line[-1][1] != deals[0]["price"]:
            line = line + [[line[-1][0], deals[0]["price"]]]  # today's real best price
        if game is not None and line is not None:
            game["history"] = line
        return line

    def search_prices(self, query):
        """Games by this name from every store, also ones outside the Store's list. None when it didn't work."""
        try:
            return live_store.search(query)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return None

    def get_prices(self, ids):
        """Today's prices for Store games outside the Store's list (a wishlist game that left it)."""
        try:
            return live_store.prices_for(ids or [])
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return None

    def set_country(self, code):
        """Where the Store's prices are for (the setup, and Preferences > Prices). Saying it also when it stays the
        same marks it as chosen. The page fetches the Store again when the country changed."""
        code = str(code or "").upper()
        if not re.fullmatch("[A-Z]{2}", code):
            return {"error": "Pick a country from the list."}
        s = config.load_settings()
        before = config.country(s)
        s["country"] = code
        config.save_settings(s)
        if code != before:
            with self._store_lock:
                self._store = None
            self._start(False)  # the sales on DLC, in that country's prices
        return {"ok": True, "changed": code != before}

    # ---------- settings: the API keys, entered in the app ----------

    def get_settings(self):
        s = config.load_settings()
        return {**keys.overview(), "version": config.VERSION, "setupDone": bool(s.get("setupDone")),
                "country": config.country(s), "countryChosen": bool(s.get("country")),
                "windowsRegion": config.windows_region(), "money": config.windows_money_style(),
                "closeToTray": s.get("closeToTray", True), "trayNotice": s.get("trayNotice", True),
                "dealAlerts": s.get("dealAlerts", True),
                "startWithWindows": startup.enabled(),
                "tray": self._tray is not None, "signins": signins.overview()}

    def set_option(self, name, value):
        """The on/off settings: setupDone, closeToTray, trayNotice, dealAlerts, startWithWindows."""
        value = bool(value)
        try:
            if name == "startWithWindows":
                startup.set_enabled(value)
            elif name in ("setupDone", "closeToTray", "trayNotice", "dealAlerts"):
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
        """Bring the window to the front. Returns False when there's none to show (Junction is quitting)."""
        w = self._window
        if not w or self._quitting:
            return False
        try:
            form = w.native
            if form is None or form.IsDisposed:
                return False
            w.show()
            hwnd = int(str(w.native.Handle.ToInt64()))
            if ctypes.windll.user32.IsIconic(hwnd):
                w.restore()
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            if self._full_later is not None:
                self.big_screen(self._big, self._full_later)
            return True
        except Exception:  # noqa: BLE001
            if not self._quitting:
                traceback.print_exc()
            return False

    def window_info(self):
        return {"custom": self._own_frame, "maximized": bool(self._window) and titlebar.is_maximized(self._window),
                "fullScreen": self._full_screen()}

    def window_action(self, what):
        """The page's own minimize, maximize/restore and close buttons."""
        w = self._window
        if what == "minimize":
            w.minimize()
        elif what == "maximize" and self._full_screen():  # in full screen, this button leaves it
            self.big_screen(self._big, False)
        elif what == "maximize":
            w.restore() if titlebar.is_maximized(w) else w.maximize()
        elif what == "close" and self._closing():  # same as Windows' close button: to the tray, if that's on
            self._quit()

    # ---------- Big screen ----------

    def _full_screen(self):
        form = self._window and self._window.native
        return bool(form is not None and getattr(form, "is_fullscreen", False))

    def big_screen(self, on, full=None):
        """The Big screen layout, for a TV: Junction fills the screen and the page is drawn larger, like zooming in,
        so it reads from the sofa. Off: a normal window at the normal size again. `full` only switches full screen
        (F11 and the window button), staying in Big screen. Returns {"fullScreen"}."""
        w = self._window
        if not w or self._quitting:
            return {"fullScreen": False}
        self._big = bool(on)
        want = self._big if full is None else bool(full)
        form = w.native
        if form is None or not form.Visible:
            self._full_later = want  # it's in the tray: once it shows
        else:
            self._full_later = None
            if want != self._full_screen():
                w.toggle_fullscreen()
        self.fit_big_screen()
        threading.Timer(0.6, self.fit_big_screen).start()  # once Windows has finished resizing it
        return {"fullScreen": want if self._full_later is not None else self._full_screen()}

    def fit_big_screen(self):
        """Zoom the page to the window: Big screen is drawn for BIG_WIDTH pixels across, then made as large as fits."""
        form = self._window and self._window.native
        if form is None or self._quitting:
            return
        big = self._big

        def on_ui_thread():
            zoom = 1.0
            if big:
                dpi = ctypes.windll.user32.GetDpiForWindow(int(str(form.Handle.ToInt64()))) or 96
                across = form.ClientSize.Width / (dpi / 96)
                zoom = max(1.0, min(2.5, round(across / BIG_WIDTH * 20) / 20))
            if abs(form.webview.ZoomFactor - zoom) > 0.01:
                form.webview.ZoomFactor = zoom

        try:
            from System import Action
            form.Invoke(Action(on_ui_thread))
        except Exception:  # noqa: BLE001 - the window is closing, or an old WebView2: the page stays its own size
            if not self._quitting:
                traceback.print_exc()

    def _window_state(self):
        self._js(f"window.junctionWindowState && junctionWindowState({json.dumps(titlebar.is_maximized(self._window))})")

    def _tray_play(self, game_id):
        self._js(f"window.junctionTrayPlay && junctionTrayPlay({json.dumps(game_id)})")

    def _quit(self):
        if self._quitting:
            return
        self._quitting = True
        self._tracker.flush()
        if self._tray:
            try:
                self._tray.stop()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        # Every window, not just the main one: pywebview only stops once all are closed, and a sign-in window left
        # open would keep Junction running without a window to bring back.
        for w in [w for w in webview.windows if w is not self._window] + ([self._window] if self._window else []):
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass

    def _ending_session(self, sender, args):
        """Windows asks before shutting down, restarting or signing out (and Task Manager before ending Junction):
        answer yes instead of hiding in the tray, which held the shutdown up. Save the play time so far now; the
        window closes, and Junction quits, only when Windows goes ahead. A shutdown that's called off changes nothing."""
        try:
            reason = str(args.CloseReason)
        except Exception:  # noqa: BLE001
            return
        if reason in ("WindowsShutDown", "TaskManagerClosing"):
            args.Cancel = False
            try:
                self._tracker.save_now()
            except Exception:  # noqa: BLE001
                pass

    def _hook_closing(self):
        """Listen to the window's own closing, after pywebview's handler, to see why it closes."""
        try:
            self._window.native.FormClosing += self._ending_session
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _tidy(self):
        """pywebview keeps an entry for every evaluate_js call and every call from the page, and only removes the
        ones with a callback. Junction calls it often (controller presses, refreshes), so clear the empty ones now
        and then, or a Junction left in the tray for weeks keeps growing."""
        while not self._quitting:
            time.sleep(600)
            try:
                cb = getattr(self._window, "_callbacks", None)
                if isinstance(cb, dict):
                    for k in [k for k, v in list(cb.items()) if v is None]:
                        cb.pop(k, None)
            except Exception:  # noqa: BLE001
                pass

    def _closing(self):
        """Closing the window: hide it in the tray instead, unless that's switched off."""
        settings = config.load_settings()
        if self._quitting or not self._tray or not settings.get("closeToTray", True):
            return True
        if ctypes.windll.user32.GetSystemMetrics(0x2000):  # SM_SHUTTINGDOWN: Windows is ending the session
            return True
        threading.Thread(target=self._window.hide, daemon=True).start()
        if settings.get("trayNotice", True):  # every time: it's easy to forget it's still there
            self._tray.tip("Junction is still running", "It's in the tray, by the clock. Right-click it to quit. "
                           "You can turn this notice off in Preferences.")
        return False

    def _after_key(self, result, then):
        if result.get("ok"):
            threading.Thread(target=then, daemon=True).start()
        return result

    def _rescan_all(self):
        """An account or key changed: every launcher read again (games, play time, achievements)."""
        self._ach.clear()
        self._background(rescan=True)

    def set_steam_key(self, steam_id, key):
        return self._after_key(keys.set_steam_key(steam_id, key), self._rescan_all)

    def remove_steam_account(self, steam_id):
        return self._after_key(keys.remove_account(steam_id), self._rescan_all)

    def restore_steam_account(self, steam_id):
        return self._after_key(keys.restore_account(steam_id), self._rescan_all)

    def set_prices_key(self, key):
        def refresh():
            if self._store_refreshing:
                return
            self._store_refreshing = True
            self._refresh_store()
        return self._after_key(keys.set_prices_key(key), refresh)

    def set_art_key(self, key):
        return self._after_key(keys.set_art_key(key), self._rescan_all)

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

    # ---------- launcher sign-ins (for their play time) ----------

    def sign_in(self, store):
        """Open the store's own sign-in page in a Junction window; returns {ok, name} once signed in, or {error}."""
        module, label = SIGN_INS.get(store, (None, None))
        if not module:
            return {"error": "Junction can't sign in there yet."}
        if not self._sign_in_lock.acquire(blocking=False):
            return {"error": "A sign-in window is already open."}
        self._forget_web_sign_ins()  # so the store asks who you are, instead of reusing the last account
        try:
            name = module.sign_in(lambda title, url: webview.create_window(
                title, url, width=520, height=780, background_color="#12161d"))
        except module.SignInError as e:
            return {"error": str(e)}
        except config.NET_ERRORS:
            return {"error": f"Junction couldn't reach {label}. Check your internet connection and try again."}
        finally:
            self._sign_in_lock.release()
        threading.Thread(target=self._background, args=(False,), daemon=True).start()  # fetch the play time now
        return {"ok": True, "name": name}

    def sign_out(self, store):
        module, _ = SIGN_INS.get(store, (None, None))
        if module:
            module.sign_out()
            self._forget_web_sign_ins()
            threading.Thread(target=self._background, args=(True,), daemon=True).start()
        return {"ok": True, "signins": signins.overview()}

    def _forget_web_sign_ins(self):
        """Clear the cookies in Junction's browser storage, which the sign-in windows share: the stores' own "keep me
        signed in" lives there. Junction's pages use none, and Junction's saved sign-ins aren't cookies."""
        try:
            if self._window:
                self._window.clear_cookies()
        except Exception:  # noqa: BLE001 - at worst the store remembers the last account
            traceback.print_exc()

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


_browser_open = webbrowser.open


def _open_web_link(url, *args, **kwargs):
    """Every link that leaves Junction ends here: its own (open_url) and the page's, which pywebview hands to
    webbrowser.open. Windows opens any address it's given, a file or another program's link too, and news and deal
    links come from other sites: so only web pages on the internet get through."""
    u = urllib.parse.urlparse(str(url))
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host or host == "localhost" or host.startswith("127.") or host == "::1":
        logging.getLogger("junction").warning("Didn't open a link that isn't a web page (%s:)", u.scheme or "no scheme")
        return False
    return _browser_open(url, *args, **kwargs)


webbrowser.open = _open_web_link


def _keep_on_page(window):
    """pywebview gives Junction's API to any page its window shows, so the window never leaves Junction's own page:
    a link or file dragged onto it, or anything else that would send it elsewhere, is stopped (a web page opens in
    the browser instead). Hooked once the page has loaded, on the window's own thread (see ea_account.sign_in)."""
    origin = []

    def going(_sender, args):
        uri = str(args.Uri)
        if not uri.startswith(origin[0]):
            args.Cancel = True
            threading.Thread(target=_open_web_link, args=(uri,), daemon=True).start()

    def loaded():
        if origin:
            return
        u = urllib.parse.urlparse(window.get_current_url() or "")
        if u.scheme != "http" or u.hostname != "127.0.0.1":
            return
        origin.append(f"http://{u.netloc}/")

        def attach():
            control = window.native.browser.webview
            control.NavigationStarting += going
            logging.getLogger("junction").info("The window stays on %s", origin[0])
        try:
            from System import Action
            window.native.Invoke(Action(attach))
        except Exception:  # noqa: BLE001
            logging.getLogger("junction").exception("Couldn't keep the window on Junction's page")

    window.events.loaded += loaded


def _ask_windows(text):
    """Yes or no in a Windows message box: an answer the page can't give for the person."""
    MB_YESNO, MB_ICONQUESTION, MB_SETFOREGROUND, MB_TOPMOST, IDYES = 0x4, 0x20, 0x10000, 0x40000, 6
    return ctypes.windll.user32.MessageBoxW(None, text, "Junction", MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST) == IDYES


def _first_instance(quiet, wait=20):
    """True when this is the only Junction. Otherwise the open one is asked to come to the front. If that one is
    just closing (it doesn't confirm), this start waits until it's gone and then starts after all."""
    global _instance
    shown = _kernel.CreateEventW(None, False, False, f"Local\\{config.INSTANCE}Shown")
    end = time.time() + wait
    asked = False
    while True:
        _instance = _kernel.CreateMutexW(None, False, f"Local\\{config.INSTANCE}App")
        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            return True
        _kernel.CloseHandle(_instance)
        _instance = None
        if quiet:
            return False
        if not asked:
            ev = _kernel.OpenEventW(0x0002, False, f"Local\\{config.INSTANCE}Show")  # EVENT_MODIFY_STATE
            if ev:
                _kernel.SetEvent(ev)
                _kernel.CloseHandle(ev)
            asked = True
        if shown and _kernel.WaitForSingleObject(shown, 250) == 0:  # it came to the front
            return False
        if time.time() > end:
            return False


def _listen_for_second_start(api):
    ev = _kernel.CreateEventW(None, False, False, f"Local\\{config.INSTANCE}Show")
    while ev:
        _kernel.WaitForSingleObject(ev, 0xFFFFFFFF)
        if api.show_window():
            shown = _kernel.OpenEventW(0x0002, False, f"Local\\{config.INSTANCE}Shown")
            if shown:
                _kernel.SetEvent(shown)
                _kernel.CloseHandle(shown)


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
    try:
        config.protect_saved_keys()
    except Exception:  # noqa: BLE001 - the keys still work as they are
        traceback.print_exc()
    api = Api()
    icon = config.resource("web", "icon.ico")
    own_frame = titlebar.prepare()
    window = webview.create_window(
        "Junction", url=page_url(), js_api=api, hidden=in_tray,
        width=1440, height=900, min_size=(900, 600), background_color="#0B0E13", text_select=False)
    api._window = window
    _keep_on_page(window)
    try:
        from . import tray
        api._tray = tray.Tray(icon, on_open=api.show_window, on_play=api._tray_play, on_quit=api._quit)
        api._tray.start()
    except Exception:  # noqa: BLE001 - Junction works without its tray icon
        traceback.print_exc()
        api._tray = None
    window.events.closing += api._closing
    window.events.before_show += api._hook_closing
    if own_frame:  # Junction draws its own title bar; take Windows' away before the window first shows
        window.events.before_show += lambda: setattr(api, "_own_frame", titlebar.attach(window))
        window.events.maximized += api._window_state
        window.events.restored += api._window_state
    threading.Thread(target=api._watch, daemon=True).start()
    threading.Thread(target=api._tidy, daemon=True).start()
    threading.Thread(target=api._keep_prices_fresh, daemon=True).start()
    api._tracker.look_around(lambda: (api._lib or {}).get("games"))  # games started outside Junction
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
    api._quitting = True
    api._tracker.flush()
    logging.getLogger("junction").info("Junction closed")
    # Don't wait for background lookups that are still queued (friends, achievements, DLC sales): they'd keep the
    # process, and the single-instance lock, alive for up to ten seconds after the window has gone. Every file
    # Junction writes is replaced in one step, so stopping here can't leave half a file.
    logging.shutdown()
    os._exit(0)


if __name__ == "__main__":
    main()
