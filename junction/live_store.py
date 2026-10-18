"""The Store's prices for the desktop app: the same data as web/store.json, fetched live.

IsThereAnyDeal throttles hard, so a full refresh takes a few minutes; the app keeps the last one
in its cache and refreshes it in the background when it's older than STALE seconds.
"""
import urllib.error

from . import art, config, extras, steam_owned, stores

STALE = 6 * 3600


def build(games, accounts, settings=None, progress=None):
    """Prices for the Store and Wishlist. `games` are the raw records from library.scan().

    Returns None when there's no IsThereAnyDeal key.
    """
    settings = settings if settings is not None else config.load_settings()
    key = (settings.get("itadApiKey") or "").strip()
    if not key:
        return None
    keys = {str(a["steamId"]): a.get("apiKey", "") for a in steam_owned.steam_accounts(settings)}
    account_info = [dict(a, apiKey=keys.get(a["steamId"], "")) for a in accounts]
    itad = stores.Itad(key, log=progress)
    data = stores.build_store(itad, games, account_info)

    # A year of prices for the games people look at most; any other game loads its history when opened.
    wanted = list(dict.fromkeys(data["frontDeals"][:40] + data["catalog"][:20] + data["wishlist"]))
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
    key = (settings.get("itadApiKey") or "").strip()
    if not key or not query.strip():
        return []
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
    return out
