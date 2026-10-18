"""The Store's prices for the desktop app: the same data as web/store.json, fetched live.

IsThereAnyDeal throttles hard, so a full refresh takes a few minutes; the app keeps the last one
in its cache and refreshes it in the background when it's older than STALE seconds.
"""
import time
import urllib.error

from . import art, config, extras, keys, steam_owned, stores

STALE = 6 * 3600          # with your own key
STALE_SHARED = 12 * 3600  # with the key Junction comes with, which everyone shares
SEARCH_TTL = 3600
_searches = {}


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
    wanted = list(dict.fromkeys(data["frontDeals"][:40] + data["catalog"][:20] + [w["id"] for w in data["wishlist"]]))
    for itad_id in wanted:
        try:
            line = stores.best_price_line(itad.history(itad_id))
        except (urllib.error.URLError, ValueError):
            continue
        if len(line) > 1:
            data["games"][itad_id]["history"] = line

    # Screenshots, straight from Steam, for the same games and the top sellers.
    steam_of = {data["games"][i]["steamAppId"]: i for i in wanted + data["topSellers"] if data["games"][i].get("steamAppId")}
    try:
        for appid, m in extras.steam_media(list(steam_of), shots=3).items():
            if m["screenshots"]:
                data["games"][steam_of[appid]]["screenshots"] = m["screenshots"]
    except (urllib.error.URLError, ValueError):
        pass
    # Games without an IsThereAnyDeal picture get their Steam header.
    no_image = {g["steamAppId"]: g for g in data["games"].values() if not g["image"] and g["steamAppId"]}
    try:
        for appid, urls in art.steam_art(list(no_image)).items():
            no_image[appid]["image"] = urls.get("header")
    except (urllib.error.URLError, ValueError):
        pass
    return data


def search(query, settings=None):
    """Every game IsThereAnyDeal knows by this name, with its best first-party price."""
    settings = settings if settings is not None else config.load_settings()
    key = keys.prices_key(settings)
    if not key or not query.strip():
        return []
    q = " ".join(query.lower().split())
    hit = _searches.get(q)
    if hit and time.time() - hit[0] < SEARCH_TTL:
        return hit[1]
    itad = stores.Itad(key)
    found = itad.search(query.strip(), results=20) or []
    ids = [g["id"] for g in found]
    prices = itad.prices(ids) if ids else {}
    out = []
    for g in found:
        deals = (prices.get(g["id"]) or {}).get("deals") or []
        best = min(deals, key=lambda d: d["price"]["amount"], default=None)
        out.append({"id": g["id"], "title": g["title"], "image": ((g.get("assets") or {}).get("banner400")),
                    "price": best and best["price"]["amount"], "shop": best and best["shop"]["name"], "url": best and best["url"]})
    _searches[q] = (time.time(), out)
    if len(_searches) > 200:
        _searches.pop(next(iter(_searches)))
    return out
