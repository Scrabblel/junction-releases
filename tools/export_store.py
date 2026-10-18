"""Snapshot store prices for the artifact prototype: writes web/store.json.

The artifact can't call IsThereAnyDeal or load images from other sites, so this fetches the
prices, matches them against your library, and embeds small copies of the pictures.

    python tools/export_store.py
"""
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from junction import art, extras, launchers, owned_local, steam_owned, stores  # noqa: E402


def data_uri(url, width=320):
    """Small JPEG copy of an image as a data: URI, or None."""
    if not url:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=art.UA), timeout=20) as r:
            img = Image.open(io.BytesIO(r.read()))
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    img.thumbnail((width, width), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=72, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main():
    settings = steam_owned.load_settings()
    key = (settings.get("itadApiKey") or "").strip()
    if not key:
        sys.exit("No IsThereAnyDeal key yet: add it as \"itadApiKey\" in local_settings.json.")

    library = launchers.find_all()["games"]
    accounts = steam_owned.add_accounts(library, settings)
    owned_local.add_owned(library)
    account_info = [dict(a, apiKey=next((x.get("apiKey", "") for x in steam_owned.steam_accounts(settings)
                                         if str(x["steamId"]) == a["steamId"]), "")) for a in accounts]
    itad = stores.Itad(key, log=lambda msg: print(msg, flush=True))
    data = stores.build_store(itad, library, account_info)
    # A year of prices for the games people look at most. ITAD throttles hard, so only these;
    # the app loads the history of any other game when its page opens.
    wanted = list(dict.fromkeys(data["frontDeals"][:40] + data["catalog"][:20] + [w["id"] for w in data["wishlist"]]))
    print(f"Fetching price history for {len(wanted)} games", flush=True)
    for itad_id in wanted:
        try:
            line = stores.best_price_line(itad.history(itad_id))
        except (urllib.error.URLError, ValueError):
            continue
        if len(line) > 1:
            data["games"][itad_id]["history"] = line
    # Screenshots for the same games, saved as files next to the page (web/shots/).
    shots_dir = os.path.join(ROOT, "web", "shots")
    os.makedirs(shots_dir, exist_ok=True)
    for old in os.listdir(shots_dir):
        os.remove(os.path.join(shots_dir, old))
    steam_of = {data["games"][i]["steamAppId"]: i for i in wanted + data["topSellers"] if data["games"][i].get("steamAppId")}
    print(f"Fetching screenshots for {len(steam_of)} games", flush=True)

    def save_shots(appid, urls):
        paths = []
        for n, url in enumerate(urls):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=art.UA), timeout=20) as r:
                    img = Image.open(io.BytesIO(r.read()))
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
            img.thumbnail((800, 450), Image.LANCZOS)
            img.convert("RGB").save(os.path.join(shots_dir, f"{appid}_{n}.jpg"), "JPEG", quality=76, optimize=True, progressive=True)
            paths.append(f"shots/{appid}_{n}.jpg")
        return appid, paths

    media = extras.steam_media(list(steam_of), shots=3)
    with ThreadPoolExecutor(6) as pool:
        for appid, paths in pool.map(lambda kv: save_shots(kv[0], kv[1]["screenshots"]), media.items()):
            if paths:
                data["games"][steam_of[appid]]["screenshots"] = paths
    print("Prices fetched; downloading pictures", flush=True)

    # Owned-only games have no ITAD picture: use their Steam header instead.
    no_image = {g["steamAppId"]: g for g in data["games"].values() if not g["image"] and g["steamAppId"]}
    for appid, urls in art.steam_art(list(no_image)).items():
        no_image[appid]["image"] = urls.get("header")

    targets = list(data["games"].values()) + data["free"]
    with ThreadPoolExecutor(8) as pool:
        images = list(pool.map(lambda x: data_uri(x.get("image"), 460 if x in data["free"] else 240), targets))
    for x, img in zip(targets, images):
        x["image"] = img

    path = os.path.join(ROOT, "web", "store.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    owned_deals = sum(1 for i in data["frontDeals"] if data["games"][i]["owned"])
    print(f'{len(data["catalog"])} games to browse, {len(data["topSellers"])} Steam top sellers, ')
    print(f'{len(data["shops"])} shops, {len(data["frontDeals"])} deals ({owned_deals} you own), '
          f'{len(data["games"])} games priced, {len(data["free"])} free, {len(data["wishlist"])} wishlisted '
          f'from {data["wishlistAccounts"]} accounts, {os.path.getsize(path) / 1e6:.1f} MB')


if __name__ == "__main__":
    main()
