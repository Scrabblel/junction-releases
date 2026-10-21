"""The live library for the desktop app: the same data as web/library.json, built on this PC.

Two steps, so the window has something to show quickly:
- scan(): launchers, owned games, Steam accounts, art and store details. Takes seconds.
- enrich(): achievements, disk space, launcher play time, DLC sales, friends, profile pictures and news.
  Slower; runs in the background after a scan.

Art stays as web addresses here (the app can load pictures from Steam itself); the artifact export
in tools/ shrinks and embeds them instead.
"""
import base64
import concurrent.futures
import mimetypes
import os
import time
import urllib.error
import urllib.request

from . import art, config, ea_account, epic_account, extras, launchers, owned_local, steam_owned, steam_switch, stores
from .config import NET_ERRORS


def _log(progress, msg):
    if progress:
        progress(msg)


def file_uri(path):
    """A small local picture (a launcher's game icon) as a data: URI."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) > 400_000:
        return None
    kind = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{kind};base64," + base64.b64encode(data).decode()


def web_uri(url, timeout=15):
    """A small picture from the web as a data: URI (friends' avatars), or None."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=art.UA), timeout=timeout) as r:
            return "data:image/jpeg;base64," + base64.b64encode(r.read()).decode()
    except Exception:  # noqa: BLE001 - a missing picture is never worth failing for
        return None


def _steam_appids(games, progress):
    """Find the Steam app id of games from other launchers, for art and genres. Lookups are cached."""
    known = config.read_cache("appids.json", {})
    missing = [g for g in games if g["artAppId"] is None and g["name"] not in known]
    if missing:
        _log(progress, f"Looking up {len(missing)} games on Steam")
    def look(name):
        try:
            return name, art.find_steam_appid(name), True
        except (*NET_ERRORS, ValueError, TimeoutError):
            return name, None, False
    # A few at a time: Steam's store search slows down when asked too fast.
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        for name, appid, ok in pool.map(look, dict.fromkeys(g["name"] for g in missing)):
            if ok:
                known[name] = appid
    if missing:
        config.write_cache("appids.json", known)
    for g in games:
        if g["artAppId"] is None:
            g["artAppId"] = known.get(g["name"])


def _logos_that_exist(appids):
    """Steam's classic logo address is a guess for newer games; check each once and remember."""
    seen = config.read_cache("logos.json", {})
    todo = [a for a in appids if str(a) not in seen]

    def head(appid):
        req = urllib.request.Request(art.art_url(appid, "logo"), headers=art.UA, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return appid, r.status == 200
        except Exception:  # noqa: BLE001
            return appid, False
    if todo:
        with concurrent.futures.ThreadPoolExecutor(12) as pool:
            for appid, ok in pool.map(head, todo):
                seen[str(appid)] = ok
        config.write_cache("logos.json", seen)
    return {int(a) for a, ok in seen.items() if ok}


def accounts_and_keys(settings):
    """[(account settings)], {steamid: api key}. An account without a key borrows another account's."""
    accs = steam_owned.steam_accounts(settings)
    spare = next((a["apiKey"].strip() for a in accs if (a.get("apiKey") or "").strip()), "")
    return accs, {str(a["steamId"]): (a.get("apiKey") or "").strip() or spare for a in accs}


_grid_known = None


def _grid_cover(key, name, max_age=7 * 86400):
    """A SteamGridDB cover by name, remembered in the cache (a miss too, for a week)."""
    global _grid_known
    if _grid_known is None:
        _grid_known = config.read_cache("griddb.json", {})
    hit = _grid_known.get(name)
    if hit and (hit[0] or time.time() - hit[1] < max_age):
        return hit[0]
    cover = extras.steamgriddb_cover(key, name)
    _grid_known[name] = [cover or "", time.time()]
    try:
        config.write_cache("griddb.json", _grid_known)
    except OSError:
        pass
    return cover


def scan(settings=None, progress=None):
    """Scan this PC. Returns (library dict for the page, the raw game records)."""
    settings = settings if settings is not None else config.load_settings()
    _log(progress, "Finding installed games")
    result = launchers.find_all()
    games = result["games"]
    _log(progress, "Reading your Steam accounts")
    accounts = steam_owned.add_accounts(games, settings)
    _log(progress, "Reading the other launchers")
    owned = owned_local.add_owned(games)
    _steam_appids(games, progress)

    appids = {g["artAppId"] for g in games if g["artAppId"]}
    _log(progress, "Fetching art and genres")
    try:
        urls = art.steam_art(list(appids))
    except (*NET_ERRORS, ValueError, TimeoutError):
        urls = {}
    try:
        details = stores.steam_details(appids)
    except (*NET_ERRORS, ValueError, TimeoutError):
        details = {}
    logos = _logos_that_exist(sorted(a for a in appids if urls.get(a, {}).get("hero")))

    grid_key = (settings.get("steamGridDbKey") or "").strip()
    out = []
    for g in games:
        u = urls.get(g["artAppId"], {}) if g["artAppId"] else {}
        images = {}
        if not g["installed"] and g.get("coverUrl"):  # the launcher's own cover is always the right game
            images["cover"] = g["coverUrl"]
        for kind in ("cover", "hero", "header"):
            if kind not in images and u.get(kind):
                images[kind] = u[kind]
        if g["artAppId"] in logos:
            images["logo"] = u["logo"]
        if g.get("localArt"):
            icon = file_uri(g["localArt"])
            if icon:
                images["icon"] = icon
        if grid_key and not images.get("cover") and not images.get("header"):
            cover = _grid_cover(grid_key, g["name"])
            if cover:
                images["cover"] = cover
        d = details.get(g["artAppId"], {})
        out.append({
            "id": g["id"], "name": g["name"], "launcher": g["launcher"], "installed": g["installed"],
            "installDir": g["installDir"], "launch": g["launch"]["target"],
            "steamAppId": g["artAppId"], "playtimeMinutes": g["playtimeMinutes"],
            "lastPlayed": g["lastPlayed"], "hiddenByDefault": g["hiddenByDefault"],
            "owners": g["owners"], "art": images, "media": None, "achievements": None,
            **{k: d.get(k) for k in ("tags", "released", "score", "scoreCount")},
        })
    lib = {"scannedAt": int(time.time()), "launchers": launchers.LAUNCHERS,
           "steamAccounts": accounts, "ownedSources": owned, "steamSignedIn": steam_switch.signed_in_steamid(),
           "games": out, "errors": result["errors"], "clientsOnPc": launchers.clients_on_pc()}
    return lib, games


def folder_size(path):
    total, stack = 0, [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def add_sizes(lib, max_age=86400):
    """Disk space of installed games. Folder sizes are remembered for a day."""
    known = config.read_cache("sizes.json", {})
    now = time.time()
    for g in lib["games"]:
        d = g.get("installDir") if g["installed"] else None
        if not d:
            g["sizeBytes"] = None
            continue
        hit = known.get(d)
        if not hit or now - hit[1] > max_age:
            hit = known[d] = [folder_size(d), now]
        g["sizeBytes"] = hit[0]
    config.write_cache("sizes.json", known)


def add_launcher_playtime(games):
    """Play time the launchers count themselves: GOG Galaxy, Ubisoft Connect and (when signed in) Epic and EA, and
    Battle.net's last played.
    `playtimeFrom: "launcher"` tells playtime.apply() that this total already holds any time Junction counted."""
    by_id, by_name = (lambda g: g["id"].split(":", 1)[1]), (lambda g: art.normalize(g["name"]))
    readers = {"gog": (owned_local.gog_playtime, by_id), "ubisoft": (owned_local.ubisoft_playtime, by_id),
               "epic": (epic_account.playtime, by_id), "ea": (ea_account.playtime, by_name),
               "battlenet": (lambda: {k: (None, t) for k, t in owned_local.battlenet_last_played().items()},
                             lambda g: launchers.bnet_product(by_id(g)))}
    for launcher, (read, key) in readers.items():
        try:
            times = read()
        except Exception:  # noqa: BLE001 - a changed file format or an expired sign-in must not stop the rest
            continue
        for g in games:
            hit = g["launcher"] == launcher and times.get(key(g))
            if not hit:
                continue
            minutes, last = hit
            if minutes:
                g["playtimeMinutes"], g["playtimeFrom"] = minutes, "launcher"
            g["lastPlayed"] = max(g.get("lastPlayed") or 0, last or 0) or None


def add_extras(lib, settings=None, inline=web_uri, store_appids=(), progress=None):
    """Launcher play time, DLC on sale, Steam friends and your own profile pictures (also used by tools/export_extras.py)."""
    settings = settings if settings is not None else config.load_settings()
    games = lib["games"]

    add_launcher_playtime(games)

    _log(progress, "Checking DLC sales")
    appids = sorted({g["steamAppId"] for g in games if g["launcher"] == "steam" and g.get("steamAppId")
                     and (g.get("playtimeMinutes") or g["installed"])})
    lib["dlcSales"] = {str(a): v for a, v in extras.steam_dlc_sales(appids).items()}

    _log(progress, "Loading Steam friends")
    accs, keys = accounts_and_keys(settings)
    spare = next(iter(k for k in keys.values() if k), "")
    try:
        friends, owned = extras.steam_friends({k: v for k, v in keys.items() if v}, keys)
    except NET_ERRORS:
        friends, owned = None, {}  # Steam didn't answer: keep the friends you had
    mine = extras.steam_profiles(spare, [str(a["steamId"]) for a in lib.get("steamAccounts") or []])
    for a in lib.get("steamAccounts") or []:
        pic = (mine.get(str(a["steamId"])) or {}).get("avatar")
        a["avatar"] = (inline(pic) if inline else pic) if pic else None
    wanted = {int(g["steamAppId"]) for g in games if g.get("steamAppId")} | {int(a) for a in store_appids}
    for fr in friends or []:
        if inline and fr["avatar"]:
            fr["avatar"] = inline(fr["avatar"])
        fr["owns"] = sum(1 for a in owned.get(fr["id"], ()) if a in wanted)
    owners = {}
    for i, fr in enumerate(friends or []):
        for a in owned.get(fr["id"], ()):
            if a in wanted:
                owners.setdefault(str(a), []).append(i)
    if friends is not None:
        lib["friends"], lib["friendOwners"] = friends, owners


def add_news(lib):
    """Each Steam game's latest developer announcements."""
    appids = sorted({g["steamAppId"] for g in lib["games"] if g.get("steamAppId")})
    lib["news"] = {str(a): n for a, n in extras.steam_news(appids).items()}


def add_achievement_counts(lib, settings=None):
    """{steamid: [unlocked, total]} per played Steam game."""
    settings = settings if settings is not None else config.load_settings()
    _, keys = accounts_and_keys(settings)
    shim = [{"launcher": g["launcher"], "playtimeMinutes": g.get("playtimeMinutes"), "owners": g.get("owners") or [],
             "artAppId": g.get("steamAppId")} for g in lib["games"]]
    extras.achievements(shim, keys)
    for g, s in zip(lib["games"], shim):
        # Steam not answering for a game keeps the counts it had, but only for accounts still listed with a key
        # (a removed account's counts go).
        kept = {sid: v for sid, v in (g.get("achievements") or {}).items() if keys.get(sid)}
        kept.update(s.get("achievements") or {})
        g["achievements"] = kept or None


def enrich(lib, settings=None, store_appids=(), progress=None):
    """Everything slower than a scan. Changes `lib` in place."""
    settings = settings if settings is not None else config.load_settings()
    steps = [
        ("Counting achievements", lambda: add_achievement_counts(lib, settings)),
        ("Measuring installed games", lambda: add_sizes(lib)),
        ("Friends, DLC sales and profile pictures",
         lambda: add_extras(lib, settings, inline=None, store_appids=store_appids, progress=progress)),
        ("Loading news", lambda: add_news(lib)),
    ]
    for label, step in steps:
        _log(progress, label)
        try:
            step()
        except Exception as e:  # noqa: BLE001 - one failed extra never loses the others
            lib.setdefault("errors", {})[label] = f"{type(e).__name__}: {e}"


# ---------- per-game details, loaded when a game page asks ----------

NEW_CDN = "https://shared.akamai.steamstatic.com/community_assets/images/apps/"
OLD_CDN = "https://steamcdn-a.akamaihd.net/steamcommunity/public/images/apps/"


def achievements_for(appid, owners, settings=None):
    """One game's achievements in the page's format, with icon addresses instead of sprite sheets."""
    settings = settings if settings is not None else config.load_settings()
    _, keys = accounts_and_keys(settings)
    key = next((keys[s] for s in owners if keys.get(s)), next(iter(k for k in keys.values() if k), ""))
    if not key:
        return None
    v = extras.achievement_list(key, appid, [s for s in owners if s] or list(keys))
    if not v:
        return None
    fix = lambda u: (u or "").replace(OLD_CDN, NEW_CDN) or None  # noqa: E731 - newer icons only live there
    return {
        # [name, description, hidden, % of players, sheet, slot, icon, grey icon]
        "list": [[a["name"], a["desc"], int(a["hidden"]), a["pct"], -1, -1, fix(a["icon"]), fix(a["icongray"])]
                 for a in v["list"]],
        "who": v["who"],
    }


def media_for(appid):
    return extras.steam_media([int(appid)]).get(int(appid))
