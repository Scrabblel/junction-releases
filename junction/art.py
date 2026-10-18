"""Cover art: every game uses Steam's library art, found by Steam app id.

Steam games already have an app id. For the other launchers we look the title up in
Steam's store search and only accept a match whose name is the same once trademark
signs, punctuation and edition labels are stripped, so a sequel never gets borrowed art.
"""
import json
import re
import urllib.parse
import urllib.request

UA = {"User-Agent": "Junction/0.1 (personal game launcher)"}
CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{file}"
ART_FILES = {"cover": "library_600x900.jpg", "hero": "library_hero.jpg", "logo": "logo.png", "header": "header.jpg"}

_EDITION = re.compile(
    r"\b(game of the year|goty|definitive|complete|deluxe|ultimate|standard|gold|anniversary|remastered)( edition)?\b")


def normalize(name):
    n = re.sub(r"[™®©]", "", name or "").lower().replace("&", " and ")
    n = re.sub(r"['’]", "", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    n = _EDITION.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def get_json(url, timeout=15):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read())


def _search(term):
    url = "https://store.steampowered.com/api/storesearch/?" + urllib.parse.urlencode(
        {"term": term, "l": "english", "cc": "NL"})
    return [i for i in get_json(url).get("items", []) if i.get("type") == "app"]


def find_steam_appid(name):
    """Steam app id whose store name matches `name`, or None."""
    target = normalize(name)
    if not target:
        return None
    # Steam's search finds nothing for long edition names, so retry with the plain title.
    for term in dict.fromkeys([name, target]):
        items = _search(term)
        for item in items:
            if normalize(item["name"]) == target:
                return item["id"]
        # "Ghost Recon Breakpoint" is listed as "Tom Clancy's Ghost Recon Breakpoint".
        if len(target.split()) >= 3:
            for item in items:
                if normalize(item["name"]).endswith(" " + target):
                    return item["id"]
    return None


ASSET_BASE = "https://shared.akamai.steamstatic.com/store_item_assets/"
ASSET_KEYS = {"cover": "library_capsule", "hero": "library_hero", "header": "header"}


def steam_items(appids, data_request=None):
    """Store details for app ids (name, type, and whatever data_request asks for). No key needed."""
    appids = [int(a) for a in appids]
    items = []
    for i in range(0, len(appids), 50):
        query = {"ids": [{"appid": a} for a in appids[i:i + 50]],
                 "context": {"language": "english", "country_code": "NL"},
                 "data_request": data_request or {}}
        url = ("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/?input_json="
               + urllib.parse.quote(json.dumps(query)))
        items += [it for it in get_json(url).get("response", {}).get("store_items", []) if it.get("success") == 1]
    return items


def steam_art(appids):
    """Art URLs per app id. Newer games only have hashed URLs, which this store API hands out."""
    found = {}
    for item in steam_items(appids, {"include_assets": True}):
        assets = item.get("assets") or {}
        fmt = assets.get("asset_url_format")
        if not fmt:
            continue
        urls = {kind: ASSET_BASE + fmt.replace("${FILENAME}", assets[key])
                for kind, key in ASSET_KEYS.items() if assets.get(key)}
        # The store API has no logo; older games still serve one at the classic path.
        urls["logo"] = art_url(item["appid"], "logo")
        found[item["appid"]] = urls
    return found


def art_url(appid, kind):
    return CDN.format(appid=appid, file=ART_FILES[kind])
