"""The Store's prices for the desktop app: the same data as web/store.json, fetched live.

IsThereAnyDeal throttles hard, so a full refresh takes half a minute or more; the app keeps the last one
in its cache and refreshes it in the background when it's older than stale() seconds.
"""
import time

from . import art, config, extras, keys, steam_owned, stores
from .config import NET_ERRORS

STALE = 6 * 3600          # with your own key
STALE_SHARED = 12 * 3600  # with the key Junction comes with, which everyone shares
SEARCH_TTL = 3600
_searches = {}
# Price history is one IsThereAnyDeal call per game, so the build fetches it only for the games the Store
# shows first; any other game's comes when its page is opened (history()).
HISTORY_FRONT, HISTORY_CATALOG = 16, 8
HISTORY_TTL = 6 * 3600
_histories = {}


def stale(settings=None):
    """How old the Store's prices may get before Junction fetches them again."""
    s = settings if settings is not None else config.load_settings()
    return STALE if (s.get("itadApiKey") or "").strip() else STALE_SHARED


def build(games, accounts, settings=None, progress=None):
    """Prices for the Store and Wishlist. `games` are the raw records from library.scan().

    Returns None when there's no IsThereAnyDeal key (not your own, and none built in).
    """
    settings = settings if settings is not None else config.load_settings()
    key = keys.prices_key(settings)
    if not key:
        return None
    steam_keys = {str(a["steamId"]): a.get("apiKey", "") for a in steam_owned.steam_accounts(settings)}
    account_info = [dict(a, apiKey=steam_keys.get(a["steamId"], "")) for a in accounts]
    itad = stores.Itad(key, log=progress)
    data = stores.build_store(itad, games, account_info)

    # A year of prices for the games people look at most; any other game loads its history when opened.
    # Not the wishlists: they can hold hundreds of games, and IsThereAnyDeal throttles long before that.
    wanted = list(dict.fromkeys(data["frontDeals"][:HISTORY_FRONT] + data["catalog"][:HISTORY_CATALOG]))
    for itad_id in wanted:
        try:
            deals = data["games"][itad_id].get("deals") or []
            line = stores.best_price_line(itad.history(itad_id), today=deals[0]["price"] if deals else None)
        except (*NET_ERRORS, ValueError):
            continue
        if len(line) > 1:
            data["games"][itad_id]["history"] = line

    # Screenshots, straight from Steam, for the same games and the top sellers.
    shown = wanted + [w["id"] for w in data["wishlist"]] + data["topSellers"]
    steam_of = {data["games"][i]["steamAppId"]: i for i in shown if data["games"][i].get("steamAppId")}
    try:
        for appid, m in extras.steam_media(list(steam_of), shots=3).items():
            if m["screenshots"]:
                data["games"][steam_of[appid]]["screenshots"] = m["screenshots"]
    except (*NET_ERRORS, ValueError):
        pass
    # Games without an IsThereAnyDeal picture get their Steam header.
    no_image = {g["steamAppId"]: g for g in data["games"].values() if not g["image"] and g["steamAppId"]}
    try:
        for appid, urls in art.steam_art(list(no_image)).items():
            no_image[appid]["image"] = urls.get("header")
    except (*NET_ERRORS, ValueError):
        pass
    return data


def history(itad_id, settings=None):
    """One game's best price over the last year, [[unix time, price], ...]; None without a prices key."""
    settings = settings if settings is not None else config.load_settings()
    at = (config.country(settings), itad_id)  # a price history is in one country's currency
    hit = _histories.get(at)
    if hit and time.time() - hit[0] < HISTORY_TTL:
        return hit[1]
    key = keys.prices_key(settings)
    if not key:
        return None
    line = stores.best_price_line(stores.Itad(key, country=at[0]).history(itad_id))
    _histories[at] = (time.time(), line)
    if len(_histories) > 500:
        _histories.pop(next(iter(_histories)))
    return line


def search(query, settings=None):
    """Every game IsThereAnyDeal knows by this name (up to 20), shaped like the Store's own games, so a search
    can find games outside the Store's list: their prices at every store, genres, reviews and lowest price ever.
    Raises when IsThereAnyDeal can't be reached; [] without a prices key."""
    settings = settings if settings is not None else config.load_settings()
    key = keys.prices_key(settings)
    if not key or not query.strip():
        return []
    q = (config.country(settings), " ".join(query.lower().split()))  # prices are in one country's currency
    hit = _searches.get(q)
    if hit and time.time() - hit[0] < SEARCH_TTL:
        return hit[1]
    itad = stores.Itad(key, country=q[0])
    # Full games only, like the Store (no DLC, soundtracks or bundles), and no adult-only games.
    found = [g for g in itad.search(query.strip(), results=20) or [] if g.get("type") in (None, "game") and not g.get("mature")]
    out = _store_games(itad, found)
    _searches[q] = (time.time(), out)
    if len(_searches) > 200:
        _searches.pop(next(iter(_searches)))
    return out


def _store_games(itad, found):
    """ITAD search results as Store games (see stores.py): prices, plus Steam's genres and reviews when it sells them."""
    ids = [g["id"] for g in found]
    if not ids:
        return []
    prices = itad.prices(ids)
    try:
        steam_of = itad.steam_for_ids(ids)
    except NET_ERRORS:
        steam_of = {}
    try:
        details = stores.steam_details(set(steam_of.values())) if steam_of else {}
    except (*NET_ERRORS, KeyError):
        details = {}
    out = []
    for g in found:
        appid = steam_of.get(g["id"])
        det = details.get(appid, {})
        assets = g.get("assets") or {}
        deals, low = stores.deals_from(prices.get(g["id"]))
        out.append({"id": g["id"], "title": g.get("title") or det.get("name", ""),
                    "image": assets.get("banner400") or assets.get("banner300"), "steamAppId": appid,
                    "tags": det.get("tags", []), "released": det.get("released"), "score": det.get("score"),
                    "scoreCount": det.get("scoreCount"), "earlyAccess": det.get("earlyAccess", False),
                    "deals": deals, "historyLow": low, "owned": [], "live": True})
    no_image = {g["steamAppId"]: g for g in out if not g["image"] and g["steamAppId"]}
    try:
        for appid, urls in art.steam_art(list(no_image)).items():
            no_image[appid]["image"] = urls.get("header")
    except NET_ERRORS:
        pass
    return out


def prices_for(ids, settings=None):
    """{ITAD id: {deals, historyLow}} for games outside the Store's list (a wishlist game that left it)."""
    key = keys.prices_key(settings if settings is not None else config.load_settings())
    ids = list(dict.fromkeys(ids))[:200]
    if not key or not ids:
        return {}
    out = {}
    for itad_id, price in stores.Itad(key).prices(ids).items():
        deals, low = stores.deals_from(price)
        out[itad_id] = {"deals": deals, "historyLow": low}
    return out
