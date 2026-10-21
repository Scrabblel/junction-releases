"""Prices from the launchers' own stores, in euros, plus free games and Steam wishlists.

Prices come from IsThereAnyDeal (https://docs.isthereanydeal.com/), limited to the first-party
stores in FIRST_PARTY. It needs a free API key from https://isthereanydeal.com/apps/my/ saved as
"itadApiKey" in local_settings.json, and it rate-limits hard, so this makes only a few dozen calls.
Genres, release dates and reviews come from Steam's store API instead, 50 games per call.

build_store() returns the shape web/index.html's Store and Wishlist pages read:
    {fetchedAt, currency, shops: {id: name}, games: {itadId: game}, catalog: [itadId],
     frontDeals: [itadId], topSellers: [itadId], free: [...], wishlist: [{id, from: [steamId]}],
     wishlistAccounts}
where game = {id, title, image, steamAppId, rank, tags, released, score, scoreCount, earlyAccess,
              historyLow: {price, shop}, owned: [{launcher, label}],
              deals: [{shop, price, regular, cut, url, storeLow}] cheapest first}
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .art import UA, get_json, normalize, steam_art, steam_items
from .config import NET_ERRORS
from .launchers import LAUNCHERS

API = "https://api.isthereanydeal.com"
COUNTRY = "NL"
STEAM_SHOP = 61
# The launchers' own stores (ITAD shop id: name shown in Junction). No resellers.
FIRST_PARTY = {61: "Steam", 16: "Epic Games", 35: "GOG", 62: "Ubisoft Store",
               52: "EA app", 4: "Battle.net", 48: "Microsoft Store"}
SHOPS_PARAM = ",".join(map(str, FIRST_PARTY))
EPIC_FREE = ("https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
             f"?locale=en-US&country={COUNTRY}&allowCountries={COUNTRY}")


def _chunks(items, size):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _ts(value):
    """ISO 8601 timestamp to epoch seconds (None stays None)."""
    if not value:
        return None
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


class Itad:
    # ITAD documents 1000 requests per 5 minutes but throttles new keys far sooner; pace calls out.
    MIN_INTERVAL = 0.5
    COOL_DOWN = 120   # seconds to stop asking after IsThereAnyDeal keeps turning requests away
    _quiet_until = 0.0

    def __init__(self, key, log=None):
        self.key = key
        self.log = log or (lambda msg: None)
        self._lock = threading.Lock()
        self._next = 0.0

    def _pace(self):
        with self._lock:
            wait = self._next - time.monotonic()
            self._next = max(self._next, time.monotonic()) + self.MIN_INTERVAL
        if wait > 0:
            time.sleep(wait)

    def _request(self, path, params=None, body=None):
        params = dict(params or {}, key=self.key)
        url = f"{API}{path}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode() if body is not None else None
        headers = dict(UA, **({"Content-Type": "application/json"} if data else {}))
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        if time.monotonic() < Itad._quiet_until:
            raise urllib.error.URLError("IsThereAnyDeal asked Junction to wait; prices come back in a few minutes")
        for attempt in range(3):
            self._pace()
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:  # rate limited: wait as long as ITAD asks
                    wait = int(e.headers.get("Retry-After") or 5)
                    self.log(f"IsThereAnyDeal asked us to slow down; waiting {wait} s")
                    time.sleep(wait)
                    continue
                if e.code == 429:
                    Itad._quiet_until = time.monotonic() + max(self.COOL_DOWN, int(e.headers.get("Retry-After") or 0))
                if e.code in (401, 403):
                    raise ValueError("IsThereAnyDeal turned down the API key. Check it was copied completely.") from None
                raise

    def deals(self, count, on_sale_only=True):
        """Popular games with their best first-party price, one entry per game, most popular first.

        on_sale_only=False gives the whole catalogue ("nondeals"), not just games on sale.
        "rank" is ITAD's popularity order (undocumented; without it the list is mostly obscure
        add-ons). Only full games are kept: no DLC, soundtracks or bundles ("package").
        """
        found, offset = [], 0
        while len(found) < count:
            params = {"country": COUNTRY, "limit": 200, "offset": offset, "sort": "rank", "shops": SHOPS_PARAM}
            if not on_sale_only:
                params["nondeals"] = "true"
            page = self._request("/deals/v2", params)
            found += [d for d in page.get("list", []) if d.get("type") == "game"]
            if not page.get("hasMore"):
                break
            offset = page["nextOffset"]
        return found[:count]

    def prices(self, ids):
        out = {}
        for chunk in _chunks(ids, 200):
            for p in self._request("/games/prices/v3", {"country": COUNTRY, "shops": SHOPS_PARAM}, chunk):
                out[p["id"]] = p
        return out

    def ids_for_steam(self, appids):
        """{steam appid: ITAD id}"""
        out = {}
        for chunk in _chunks([f"app/{a}" for a in appids], 200):
            for shop_id, itad_id in self._request(f"/lookup/id/shop/{STEAM_SHOP}/v1", body=chunk).items():
                if itad_id:
                    out[int(shop_id.split("/")[1])] = itad_id
        return out

    def steam_for_ids(self, itad_ids):
        """{ITAD id: steam appid} for games sold on Steam."""
        out = {}
        for chunk in _chunks(itad_ids, 200):
            for itad_id, shop_ids in self._request(f"/lookup/shop/{STEAM_SHOP}/id/v1", body=chunk).items():
                apps = [s for s in shop_ids or [] if s.startswith("app/")]
                if apps:
                    out[itad_id] = int(apps[0].split("/")[1])
        return out

    def ids_for_titles(self, titles):
        out = {}
        for chunk in _chunks(titles, 200):
            out.update({t: i for t, i in self._request("/lookup/id/title/v1", body=chunk).items() if i})
        return out

    def history(self, itad_id, days=365):
        """Price changes at the first-party stores over the last `days`, newest first."""
        since = datetime.fromtimestamp(time.time() - days * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._request("/games/history/v2", {"id": itad_id, "country": COUNTRY, "shops": SHOPS_PARAM,
                                                    "since": since})

    def search(self, title, results=30):
        return self._request("/games/search/v1", {"title": title, "results": results})

    def giveaways(self):
        return self._request("/giveaways/v1", {"limit": 50})


# ---------- Steam store details: genres, release dates, reviews ----------

_tag_names = {}


def steam_details(appids, tag_count=8):
    """{appid: {name, tags, released, score, scoreCount, earlyAccess}} from Steam's store API."""
    if not _tag_names:
        tags = get_json("https://api.steampowered.com/IStoreService/GetTagList/v1/?language=english")
        _tag_names.update({t["tagid"]: t["name"] for t in tags["response"]["tags"]})
    out = {}
    request = {"include_tag_count": tag_count, "include_release": True, "include_reviews": True}
    for item in steam_items(appids, request):
        reviews = (item.get("reviews") or {}).get("summary_filtered") or {}
        released = (item.get("release") or {}).get("steam_release_date")
        out[item["appid"]] = {
            "name": item.get("name") or "",
            "tags": [_tag_names[t] for t in item.get("tagids") or [] if t in _tag_names],
            "released": datetime.fromtimestamp(released, timezone.utc).date().isoformat() if released else None,
            "score": reviews.get("percent_positive"), "scoreCount": reviews.get("review_count"),
            "earlyAccess": bool(item.get("is_early_access")),
            "isGame": item.get("type") == 0,  # not hardware, software or a demo
        }
    return out


def steam_top_sellers():
    """App ids of Steam's current top sellers in the Netherlands."""
    data = get_json(f"https://store.steampowered.com/api/featuredcategories?cc={COUNTRY.lower()}&l=english")
    return list(dict.fromkeys(i["id"] for i in data.get("top_sellers", {}).get("items", []) if i.get("id")))


def steam_wishlist(steamid, key=""):
    """App ids on an account's wishlist. Empty when the wishlist is private (or empty)."""
    params = {"steamid": steamid, **({"key": key} if key else {})}
    url = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/?" + urllib.parse.urlencode(params)
    return [i["appid"] for i in get_json(url).get("response", {}).get("items", [])]


# ---------- free games ----------

def epic_free():
    """Games Epic is giving away right now."""
    now = time.time()
    out = []
    for el in get_json(EPIC_FREE)["data"]["Catalog"]["searchStore"]["elements"]:
        offers = ((el.get("promotions") or {}).get("promotionalOffers") or [{}])[0].get("promotionalOffers") or []
        live = [o for o in offers if o["discountSetting"]["discountPercentage"] == 0
                and _ts(o["startDate"]) <= now <= _ts(o["endDate"])]
        if not live:
            continue
        images = {k["type"]: k["url"] for k in el.get("keyImages", [])}
        slug = ((el.get("catalogNs") or {}).get("mappings") or [{}])[0].get("pageSlug") \
            or (el.get("productSlug") or "").split("/")[0]
        out.append({"title": el["title"], "store": "Epic Games",
                    "image": images.get("OfferImageWide") or images.get("DieselStoreFrontWide") or images.get("Thumbnail"),
                    "until": _ts(live[0]["endDate"]),
                    "url": f"https://store.epicgames.com/p/{slug}" if slug else "https://store.epicgames.com/free-games"})
    return out


def itad_free(itad):
    """Full games given away by the launchers' own stores."""
    out = []
    for g in itad.giveaways():
        shop_id = (g.get("shop") or {}).get("id")
        games = [x for x in g.get("games") or [] if x.get("type") == "game"]
        if shop_id not in FIRST_PARTY or not games:
            continue
        assets = games[0].get("assets") or {}
        out.append({"title": games[0]["title"], "store": FIRST_PARTY[shop_id],
                    "image": assets.get("banner400") or assets.get("banner300"),
                    "until": _ts(g.get("expiry")), "url": g["url"]})
    return out


def best_price_line(changes, now=None, today=None):
    """Turn ITAD price changes into the cheapest first-party price over time.

    Returns [[unix time, price], ...], oldest first, one point per change in the best price,
    ending with today's price. Each store's price holds until its next change.
    """
    current, line = {}, []
    for c in sorted(changes, key=lambda c: c["timestamp"]):
        current[c["shop"]["id"]] = c["deal"]["price"]["amount"]
        best = min(current.values())
        ts = int(datetime.fromisoformat(c["timestamp"]).timestamp())
        if line and line[-1][1] == best:
            continue
        if line and ts - line[-1][0] < 3600:  # several stores changing at once: keep the result
            line[-1] = [line[-1][0], best]
        else:
            line.append([ts, best])
    if line:
        line.append([int(now or time.time()), line[-1][1]])
        # A store that stopped selling the game keeps its last price above; end at today's real best price.
        if today is not None and line[-1][1] != today:
            line.append([line[-1][0], today])
    return line


# ---------- putting it together ----------

def _owned_label(g, account_names):
    if g["launcher"] == "steam" and g["owners"] and len(account_names) > 1:
        return f'Steam ({", ".join(account_names.get(o, o) for o in g["owners"])})'
    return LAUNCHERS.get(g["launcher"], g["launcher"])


def build_store(itad, library_games, accounts, deal_count=150, catalog_count=600):
    """Everything the Store and Wishlist pages need. `library_games` and `accounts` come from
    launchers.find_all() + steam_owned.add_accounts(), so deals can be matched to what you own."""
    log = itad.log
    account_names = {a["steamId"]: a["name"] for a in accounts}

    # What you own, as ITAD ids. Steam app ids are exact; everything else goes by title.
    log("Matching your library")
    owned, by_appid = {}, {}
    for g in library_games:
        if g.get("artAppId"):
            by_appid.setdefault(int(g["artAppId"]), []).append(g)
    for appid, itad_id in itad.ids_for_steam(by_appid).items():
        for g in by_appid[appid]:
            owned.setdefault(itad_id, []).append(g)
    leftover = [g for g in library_games if not g.get("artAppId")]
    for title, itad_id in itad.ids_for_titles([g["name"] for g in leftover]).items():
        owned.setdefault(itad_id, []).extend(g for g in leftover if g["name"] == title)

    # Wishlists of every Steam account.
    wished, wishlist_accounts, steam_of = {}, 0, {}
    for a in accounts:
        try:
            appids = steam_wishlist(a["steamId"], a.get("apiKey", ""))
        except NET_ERRORS:
            appids = []
        wishlist_accounts += bool(appids)
        for appid, itad_id in (itad.ids_for_steam(appids) if appids else {}).items():
            wished.setdefault(itad_id, []).append(a["steamId"])
            steam_of[itad_id] = appid

    log("Fetching the catalogue and deals")
    games = {}

    def add(d):
        assets = d.get("assets") or {}
        games.setdefault(d["id"], {"id": d["id"], "title": d["title"],
                                   "image": assets.get("banner400") or assets.get("banner300")})
        return d["id"]

    catalog = [add(d) for d in itad.deals(catalog_count, on_sale_only=False)]
    front = [add(d) for d in itad.deals(deal_count)]
    try:
        sellers = itad.ids_for_steam(steam_top_sellers())
    except NET_ERRORS:
        sellers = {}
    steam_of.update({i: a for a, i in sellers.items()})
    top_sellers = list(dict.fromkeys(sellers.values()))
    browsable = list(dict.fromkeys(catalog + front + top_sellers + list(wished)))
    for itad_id, gs in owned.items():
        if gs[0].get("artAppId"):
            steam_of.setdefault(itad_id, int(gs[0]["artAppId"]))

    # Steam app ids for everything, then genres, release dates and reviews from Steam.
    log("Looking up genres, release dates and reviews on Steam")
    steam_of.update(itad.steam_for_ids([i for i in browsable if i not in steam_of]))
    details = steam_details({steam_of[i] for i in browsable if i in steam_of})
    # Steam's top sellers include hardware like the Steam Deck; keep games only.
    top_sellers = [i for i in top_sellers if details.get(steam_of.get(i), {}).get("isGame")]
    browsable = list(dict.fromkeys(catalog + front + top_sellers + list(wished)))
    pictures = steam_art([steam_of[i] for i in browsable if i in steam_of and i not in games])
    rank = {itad_id: n for n, itad_id in enumerate(catalog, 1)}
    for itad_id in browsable:
        appid = steam_of.get(itad_id)
        det = details.get(appid, {})
        g = games.setdefault(itad_id, {"id": itad_id, "title": det.get("name", ""),
                                       "image": pictures.get(appid, {}).get("header")})
        g.update(steamAppId=appid, rank=rank.get(itad_id), tags=det.get("tags", []), released=det.get("released"),
                 score=det.get("score"), scoreCount=det.get("scoreCount"), earlyAccess=det.get("earlyAccess", False))
    # Owned games outside the catalogue, so a search can tell you "you already own this".
    for itad_id, gs in owned.items():
        if itad_id not in games:
            games[itad_id] = {"id": itad_id, "title": gs[0]["name"], "image": None, "steamAppId": gs[0].get("artAppId")}

    log(f"Fetching prices for {len(games)} games")
    prices = itad.prices(list(games))
    for itad_id, g in games.items():
        offers = [x for x in prices.get(itad_id, {}).get("deals", []) if x["shop"]["id"] in FIRST_PARTY]
        deals = sorted(({"shop": x["shop"]["id"], "price": x["price"]["amount"], "regular": x["regular"]["amount"],
                         "cut": x.get("cut") or 0, "url": x["url"],
                         "storeLow": (x.get("storeLow") or {}).get("amount")} for x in offers),
                       key=lambda x: (x["price"], -x["cut"]))
        lows = [d for d in deals if d["storeLow"] is not None]
        low = min(lows, key=lambda d: d["storeLow"]) if lows else None
        g.update(deals=deals, historyLow={"price": low["storeLow"], "shop": low["shop"]} if low else None,
                 owned=[{"launcher": o["launcher"], "label": _owned_label(o, account_names)} for o in owned.get(itad_id, [])])

    free = []
    for source in (epic_free, lambda: itad_free(itad)):
        try:
            free += source()
        except (*NET_ERRORS, KeyError, ValueError):
            pass
    seen, unique_free = set(), []
    for f in free:
        if normalize(f["title"]) not in seen:
            seen.add(normalize(f["title"]))
            unique_free.append(f)

    return {"fetchedAt": int(time.time()), "currency": "EUR", "country": COUNTRY,
            "shops": {str(k): v for k, v in FIRST_PARTY.items()}, "games": games, "frontDeals": front,
            "catalog": browsable, "topSellers": top_sellers, "free": unique_free,
            "wishlist": [{"id": i, "from": f} for i, f in wished.items()], "wishlistAccounts": wishlist_accounts}
