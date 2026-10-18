"""Extra game details for the game page: screenshots, a short trailer clip, achievements, missing covers.

- Screenshots and trailers come from Steam's store API (no key).
- Achievements come from the Steam Web API, so they need a Steam key; any key reads any account
  whose game details are public.
- Covers for games Steam doesn't sell come from SteamGridDB, a free community art library, when
  local_settings.json has a "steamGridDbKey" (made at steamgriddb.com, Preferences > API).
"""
import html
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .art import ASSET_BASE, UA, get_json, steam_items

TRAILER_BASE = "https://video.akamai.steamstatic.com/store_trailers/"


def steam_media(appids, shots=4):
    """{appid: {"screenshots": [url, ...], "clip": mp4 url or None, "trailerName": str}}"""
    out = {}
    for item in steam_items(appids, {"include_screenshots": True, "include_trailers": True}):
        ss = (item.get("screenshots") or {}).get("all_ages_screenshots") or []
        ss = sorted(ss, key=lambda s: s.get("ordinal", 0))[:shots]
        trailer = next(iter((item.get("trailers") or {}).get("highlights") or []), None) or {}
        clip = next((m["filename"] for m in trailer.get("microtrailer") or [] if m.get("type") == "video/mp4"), None)
        out[item["appid"]] = {
            "screenshots": [ASSET_BASE + s["filename"] for s in ss if s.get("filename")],
            "clip": TRAILER_BASE + clip if clip else None,
            "trailerName": trailer.get("trailer_name"),
        }
    return out


def _achievements(key, steamid, appid):
    url = ("https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v1/?"
           + urllib.parse.urlencode({"key": key, "steamid": steamid, "appid": appid}))
    try:
        stats = get_json(url, timeout=20).get("playerstats") or {}
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None  # 400/403: the game has no achievements, or this account doesn't own it
    items = stats.get("achievements") or []
    return [sum(1 for a in items if a.get("achieved")), len(items)] if items else None


def achievements(games, keys):
    """Fill game["achievements"] = {steamid: [done, total]} for played Steam games.

    `keys` maps SteamID to the API key to use for that account.
    """
    jobs = [(g, sid) for g in games if g["launcher"] == "steam" and g.get("playtimeMinutes")
            for sid in g["owners"] if keys.get(sid)]
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda j: _achievements(keys[j[1]], j[1], j[0]["artAppId"]), jobs))
    for (g, sid), r in zip(jobs, results):
        if r:
            g.setdefault("achievements", {})[sid] = r
    return sum(1 for g in games if g.get("achievements"))


def steamgriddb_cover(key, name):
    """A 600x900 cover for a game by name from SteamGridDB, or None."""
    headers = dict(UA, Authorization=f"Bearer {key}")

    def call(path):
        req = urllib.request.Request("https://www.steamgriddb.com/api/v2" + path, headers=headers)
        import json
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    try:
        found = call("/search/autocomplete/" + urllib.parse.quote(name)).get("data") or []
        if not found:
            return None
        grids = call(f"/grids/game/{found[0]['id']}?dimensions=600x900&types=static").get("data") or []
        return grids[0]["url"] if grids else None
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
        return None


def achievement_list(key, appid, steamids):
    """Every achievement of one game, plus who unlocked what and when.

    {"list": [{"api", "name", "desc", "hidden", "icon", "pct"}, ...],
     "who": {steamid: [unlock time or 0, ...]}}  (in list order), or None.
    """
    def call(url):
        try:
            return get_json(url, timeout=20)
        except (urllib.error.URLError, TimeoutError, ValueError):
            return {}
    base = "https://api.steampowered.com/ISteamUserStats/"
    schema = call(base + "GetSchemaForGame/v2/?" + urllib.parse.urlencode({"key": key, "appid": appid, "l": "english"}))
    items = ((schema.get("game") or {}).get("availableGameStats") or {}).get("achievements") or []
    if not items:
        return None
    pcts = call(base + "GetGlobalAchievementPercentagesForApp/v2/?" + urllib.parse.urlencode({"gameid": appid}))
    pct = {a["name"]: float(a.get("percent") or 0) for a in (pcts.get("achievementpercentages") or {}).get("achievements") or []}
    who = {}
    for sid in steamids:
        stats = call(base + "GetPlayerAchievements/v1/?" + urllib.parse.urlencode({"key": key, "steamid": sid, "appid": appid}))
        got = {a["apiname"]: (a.get("unlocktime") or 1) if a.get("achieved") else 0
               for a in (stats.get("playerstats") or {}).get("achievements") or []}
        if got:
            who[sid] = [got.get(a["name"], 0) for a in items]
    return {
        "list": [{"api": a["name"], "name": a.get("displayName") or a["name"], "desc": a.get("description") or "",
                  "hidden": bool(a.get("hidden")), "icon": a.get("icon"), "icongray": a.get("icongray"), "pct": round(pct.get(a["name"], 0), 1)}
                 for a in items],
        "who": who,
    }


def steam_dlc_sales(appids):
    """{appid: [{"id", "name", "price", "regular", "cut"}]} for DLC of these games that's discounted on Steam now."""
    def one(appid):
        try:
            d = get_json(f"https://store.steampowered.com/api/dlcforapp/?appid={appid}&cc=nl&l=english", timeout=20)
        except (urllib.error.URLError, TimeoutError, ValueError):
            return appid, []
        out = []
        for x in d.get("dlc") or []:
            p = x.get("price_overview") or {}
            if p.get("discount_percent"):
                out.append({"id": x["id"], "name": x.get("name") or "", "price": p["final"] / 100,
                            "regular": p["initial"] / 100, "cut": p["discount_percent"]})
        return appid, sorted(out, key=lambda x: -x["cut"])
    with ThreadPoolExecutor(4) as pool:
        return {a: dlc for a, dlc in pool.map(one, appids) if dlc}


def steam_friends(keys, own_ids):
    """Friends of all your Steam accounts (your own accounts left out) and the games each one owns.

    Returns ([{"id", "name", "avatar", "state", "playing", "lastOnline"}], {friend id: {appid, ...}}).
    `keys` maps your SteamIDs to an API key. Friends with a private friend list or game list are skipped.
    """
    base = "https://api.steampowered.com/"
    ids = set()
    for sid, key in keys.items():
        try:
            fl = get_json(base + "ISteamUser/GetFriendList/v1/?" + urllib.parse.urlencode(
                {"key": key, "steamid": sid, "relationship": "friend"}))
        except (urllib.error.URLError, TimeoutError, ValueError):
            continue  # this account's friend list is private
        ids.update(f["steamid"] for f in fl.get("friendslist", {}).get("friends", []))
    ids -= set(own_ids)
    key = next(iter(keys.values()), "")
    if not ids or not key:
        return [], {}
    people = []
    ids = sorted(ids)
    for i in range(0, len(ids), 100):
        r = get_json(base + "ISteamUser/GetPlayerSummaries/v2/?" + urllib.parse.urlencode(
            {"key": key, "steamids": ",".join(ids[i:i + 100])}))
        people += r.get("response", {}).get("players", [])
    friends = [{
        "id": p["steamid"], "name": p.get("personaname") or "Friend", "avatar": p.get("avatarmedium") or p.get("avatar"),
        # Steam persona states: 0 offline, 1 online, 2 busy, 3 away, 4 snooze, 5 looking to trade, 6 looking to play
        "state": int(p.get("personastate") or 0),
        "playing": {"appid": int(p["gameid"]), "name": p.get("gameextrainfo") or ""} if p.get("gameid") else None,
        "lastOnline": p.get("lastlogoff"),
    } for p in people]

    def owned(fid):
        try:
            r = get_json(base + "IPlayerService/GetOwnedGames/v1/?" + urllib.parse.urlencode(
                {"key": key, "steamid": fid, "include_played_free_games": 1}), timeout=20)
        except (urllib.error.URLError, TimeoutError, ValueError):
            return fid, set()
        return fid, {g["appid"] for g in r.get("response", {}).get("games", [])}
    with ThreadPoolExecutor(6) as pool:
        games = dict(pool.map(owned, [f["id"] for f in friends]))
    return friends, games


def _plain(text):
    """Steam announcements are BBCode or HTML; keep only the readable text, one line per paragraph joined up."""
    text = re.sub(r"\[(img|previewyoutube|video)\b[^\]]*\].*?\[/\1\]|\[img\b[^\]]*\]|\{STEAM_CLAN_IMAGE\}\S*", " ", text, flags=re.S | re.I)
    text = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", text, flags=re.S | re.I)
    text = text.replace("\\[", "[").replace("\\]", "]")
    text = re.sub(r"\[/?(p|h\d|list|olist|\*|hr|table|tr)(?=[\s\]=])[^\]]*\]|<br\s*/?>|</?(p|li|h\d|div)\b[^>]*>|\n", "\n", text, flags=re.I)
    text = re.sub(r"\[/?[a-z0-9*]+(?=[\s\]=])[^\]]*\]|<[^>]+>|https?://\S+", " ", text, flags=re.I)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in html.unescape(text).split("\n")]
    out = ""
    for ln in filter(None, lines):
        out += ln if not out else (" " if out[-1] in ".!?:,;…" else ". ") + ln
    return out


PATCH_WORDS = re.compile(r"\b(patch|hotfix|update|changelog|release notes)\b", re.I)


def steam_news(appids, count=4):
    """{appid: [{"title", "date", "url", "text", "patch"}]}: the developer's own announcements on Steam, newest first."""
    def one(appid):
        q = urllib.parse.urlencode({"appid": appid, "count": count, "format": "json",
                                    "feeds": "steam_community_announcements"})
        try:
            d = get_json(f"https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?{q}", timeout=20)
        except (urllib.error.URLError, TimeoutError, ValueError):
            return appid, []
        out = []
        for x in (d.get("appnews") or {}).get("newsitems") or []:
            text = _plain(x.get("contents") or "")
            if len(text) > 320:
                text = text[:320].rsplit(" ", 1)[0] + "…"
            title = html.unescape(x.get("title") or "").strip()
            patch = "patchnotes" in (x.get("tags") or []) or PATCH_WORDS.search(title) is not None
            out.append({"title": title, "date": x.get("date"), "url": x.get("url"), "text": text, "patch": patch})
        return appid, out
    with ThreadPoolExecutor(6) as pool:
        return {a: n for a, n in pool.map(one, appids) if n}


def steam_profiles(key, steamids):
    """{steamid: {"name", "avatar"}} for these Steam accounts; "avatar" is the 184px profile picture URL."""
    if not key or not steamids:
        return {}
    try:
        r = get_json("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?" + urllib.parse.urlencode(
            {"key": key, "steamids": ",".join(steamids)}), timeout=20)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {}
    return {p["steamid"]: {"name": p.get("personaname"), "avatar": p.get("avatarfull") or p.get("avatarmedium")}
            for p in r.get("response", {}).get("players", [])}
