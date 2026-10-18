"""Games on a Steam account that aren't installed on this PC.

Best source: the Steam Web API, which lists every game the account owns. It needs an API key
(https://steamcommunity.com/dev/apikey) and the account's SteamID in local_settings.json.
Without a key we fall back to what this PC remembers: the games that account has installed or
played here before, read from its localconfig.vdf. That list is incomplete, and can include
games played through Family Sharing that the account doesn't own.
"""
import json
import os
import urllib.error
import urllib.parse

from .art import get_json, steam_items
from .launchers import (STEAM_SKIP_IDS, STEAM_SKIP_NAME, STEAM_TOOL_IDS, _ci, _game, _read,
                        _steam_root, parse_vdf)

# Store item types worth listing: 0 = game, 14 = separately launched parts such as
# "Call of Duty: Black Ops II - Multiplayer". Skipped: 1 demo, 6 software, 12 beta.
GAME_TYPES = {0, 14}
STEAMID64_BASE = 76561197960265728

def load_settings():
    from .config import load_settings as load  # per-user settings, or local_settings.json while developing
    return load()


def owned_games(key, steamid):
    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?" + urllib.parse.urlencode({
        "key": key, "steamid": steamid, "include_appinfo": 1, "include_played_free_games": 1, "format": "json"})
    try:
        response = get_json(url, timeout=30).get("response", {})
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ValueError("Steam turned down the API key. Check it was copied completely.") from None
        raise
    if "games" not in response:
        # Steam answers with an empty response when the profile's game details are private.
        raise ValueError("Steam returned no games. Make the key on this same account, "
                         "or set the profile's Game details to Public.")
    # The API also lists test branches and region copies ("Rust - Staging Branch").
    # Drop what the store calls something other than a game; keep delisted games it no longer knows.
    types = {item["appid"]: item.get("type") for item in steam_items([g["appid"] for g in response["games"]])}
    return [g for g in response["games"] if types.get(g["appid"], 0) in GAME_TYPES]


def remembered_games(steamid):
    """Games this PC's Steam remembers for the account, in the same shape as owned_games()."""
    root = _steam_root()
    if not root:
        return []
    cfg = os.path.join(root, "userdata", str(int(steamid) - STEAMID64_BASE), "config", "localconfig.vdf")
    if not os.path.isfile(cfg):
        return []
    node = _ci(parse_vdf(_read(cfg)), "UserLocalConfigStore")
    for part in ("Software", "Valve", "Steam", "apps"):
        node = _ci(node, part)
    seen = {int(a): v for a, v in (node or {}).items() if a.isdigit() and isinstance(v, dict)}
    games = []
    for item in steam_items(list(seen)):
        if item.get("type") not in GAME_TYPES:
            continue
        info = seen[item["appid"]]
        games.append({"appid": item["appid"], "name": item.get("name") or "",
                      "playtime_forever": int(info.get("Playtime") or 0),
                      "rtime_last_played": int(info.get("LastPlayed") or 0)})
    return games


def steam_accounts(settings=None):
    """The Steam accounts listed in local_settings.json, in the user's order."""
    settings = settings if settings is not None else load_settings()
    accounts = settings.get("steamAccounts")
    if accounts is None and settings.get("steamId"):  # first, single-account format
        accounts = [{"name": "Steam", "steamId": settings["steamId"], "apiKey": settings.get("steamApiKey", "")}]
    return [a for a in accounts or [] if str(a.get("steamId") or "").strip()]


def add_accounts(games, settings=None):
    """Merge every account's Steam games into `games` (from launchers.find_all).

    A game owned by several accounts appears once, with all of them in its "owners".
    Returns one summary per account: name, steamId, source ("api" or "pc"), games, and error if any.
    """
    steam = {g["artAppId"]: g for g in games if g["launcher"] == "steam"}
    totals = {}  # appid -> [minutes played on all accounts, last played]
    summaries = []
    accounts = steam_accounts(settings)
    # Any key can read any profile whose game details are public, so an account without
    # a key of its own borrows another account's.
    spare_key = next((k for k in ((a.get("apiKey") or "").strip() for a in accounts) if k), "")
    for acc in accounts:
        steamid, key = str(acc["steamId"]).strip(), (acc.get("apiKey") or "").strip() or spare_key
        summary = {"name": acc.get("name") or steamid, "steamId": steamid, "source": "api" if key else "pc", "games": 0}
        summaries.append(summary)
        try:
            owned = owned_games(key, steamid) if key else remembered_games(steamid)
        except (ValueError, urllib.error.URLError) as e:
            summary["error"], summary["source"] = str(e), "pc"
            owned = remembered_games(steamid)

        for o in owned:
            appid, name = o["appid"], o.get("name") or ""
            if str(appid) in STEAM_SKIP_IDS or STEAM_SKIP_NAME.search(name):
                continue
            summary["games"] += 1
            g = steam.get(appid)
            if g is None:
                g = _game("steam", appid, name,
                          launch={"type": "uri", "target": f"steam://install/{appid}"},
                          art_appid=appid, hidden_by_default=str(appid) in STEAM_TOOL_IDS)
                g["installed"] = False
                games.append(g)
                steam[appid] = g
            if steamid not in g["owners"]:
                g["owners"].append(steamid)
            t = totals.setdefault(appid, [0, 0])
            t[0] += o.get("playtime_forever") or 0
            t[1] = max(t[1], o.get("rtime_last_played") or 0)

    for appid, (minutes, last) in totals.items():
        g = steam[appid]
        g["playtimeMinutes"] = max(g["playtimeMinutes"] or 0, minutes) or None
        g["lastPlayed"] = max(g["lastPlayed"] or 0, last) or None
    games.sort(key=lambda g: g["name"].lower())
    return summaries
