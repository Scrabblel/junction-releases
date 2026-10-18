"""Snapshot this PC's game library for the artifact prototype.

The artifact runs in a browser sandbox: it can't read the registry and can't load images
from Steam's servers. So this script detects the games, downloads and shrinks their art,
and writes web/library.json + web/art/*, which are published alongside web/index.html.
Games that aren't installed only get a small cover, embedded in library.json as a data URI,
because an artifact holds at most 511 files.

    python tools/export_artifact.py
"""
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from junction import art, extras, launchers, owned_local, steam_owned, steam_switch, stores  # noqa: E402

WEB = os.path.join(ROOT, "web")
ART_DIR = os.path.join(WEB, "art")
SIZES = {"cover": (300, 450), "hero": (1280, 414), "logo": (560, 220), "header": (460, 215), "shot": (960, 540)}
INLINE_SIZES = {"cover": (200, 300), "header": (300, 140)}


def fetch(url):
    url = urllib.parse.quote(url, safe=":/?&=%#+@,;~")  # some Epic image names contain spaces
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=art.UA), timeout=20) as r:
            return r.read()
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def save_image(data, kind, out_name):
    img = Image.open(io.BytesIO(data))
    img.thumbnail(SIZES[kind], Image.LANCZOS)
    path = os.path.join(ART_DIR, out_name)
    if out_name.endswith(".png"):
        img.save(path, "PNG", optimize=True)
    else:
        img.convert("RGB").save(path, "JPEG", quality=80, optimize=True, progressive=True)
    return "art/" + out_name


def inline_image(data, kind):
    img = Image.open(io.BytesIO(data))
    img.thumbnail(INLINE_SIZES[kind], Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=72, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def download_art(g, urls):
    """Download and shrink one game's art. Returns {kind: published path}."""
    appid = g["artAppId"]
    kinds = ("cover", "hero", "logo", "header") if g["installed"] else ("cover", "header")
    images = {}
    if not g["installed"] and g.get("coverUrl"):  # the launcher's own cover is always the right game
        data = fetch(g["coverUrl"])
        try:
            if data:
                images["cover"] = inline_image(data, "cover")
        except OSError:
            pass
    for kind in kinds:
        if kind in images:
            continue
        url = urls.get(appid, {}).get(kind)
        # Uninstalled games get the wide header too: it's the banner on their game page.
        if not url or (kind == "header" and "cover" in images and g["installed"]):
            continue
        data = fetch(url)
        if data:
            try:
                if g["installed"]:
                    images[kind] = save_image(data, kind, f"{appid}_{kind}.{'png' if kind == 'logo' else 'jpg'}")
                else:
                    images[kind] = inline_image(data, kind)
            except OSError:
                pass
    if g["localArt"]:
        with open(g["localArt"], "rb") as f:
            name = g["id"].replace(":", "_").replace(".", "_") + "_icon.png"
            images["icon"] = save_image(f.read(), "logo", name)
    return images


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    for old in os.listdir(ART_DIR):
        os.remove(os.path.join(ART_DIR, old))

    result = launchers.find_all()
    games = result["games"]
    accounts = steam_owned.add_accounts(games)
    for a in accounts:
        print(f'Steam account {a["name"]}: {a["games"]} games ({a["source"]}){" - " + a["error"] if a.get("error") else ""}')
    owned = owned_local.add_owned(games)
    for launcher, s in owned.items():
        print(f'{launchers.LAUNCHERS[launcher]}: {s["games"]} owned, {s["added"]} not installed'
              f'{" - " + s["error"] if s.get("error") else ""}')

    for g in games:
        if g["artAppId"] is None:
            try:
                g["artAppId"] = art.find_steam_appid(g["name"])
            except (urllib.error.URLError, ValueError):
                pass
            time.sleep(0.4)  # be polite to Steam's store search
    urls = art.steam_art([g["artAppId"] for g in games if g["artAppId"]])
    # Genres, release dates and review scores for the library's filters and game pages.
    try:
        details = stores.steam_details({g["artAppId"] for g in games if g["artAppId"]})
    except urllib.error.URLError:
        details = {}

    with ThreadPoolExecutor(8) as pool:
        all_images = list(pool.map(lambda g: download_art(g, urls), games))

    settings = steam_owned.load_settings()
    # Covers for games Steam doesn't sell, from SteamGridDB (needs a free key in local_settings.json).
    grid_key = (settings.get("steamGridDbKey") or "").strip()
    if grid_key:
        for g, images in zip(games, all_images):
            if images.get("cover") or images.get("header"):
                continue
            url = extras.steamgriddb_cover(grid_key, g["name"])
            data = fetch(url) if url else None
            if data:
                name = g["id"].replace(":", "_").replace(".", "_") + "_cover.jpg"
                images["cover"] = save_image(data, "cover", name) if g["installed"] else inline_image(data, "cover")

    # Screenshots and a short trailer clip for installed games. (The app loads them for every game.)
    installed_ids = {g["artAppId"] for g in games if g["installed"] and g["artAppId"]}
    media = {}
    for appid, m in extras.steam_media(installed_ids).items():
        shots = []
        for i, url in enumerate(m["screenshots"]):
            data = fetch(url)
            if data:
                shots.append(save_image(data, "shot", f"{appid}_ss{i}.jpg"))
        clip = None
        data = fetch(m["clip"]) if m["clip"] else None
        if data and len(data) < 8e6:
            with open(os.path.join(ART_DIR, f"{appid}_clip.mp4"), "wb") as f:
                f.write(data)
            clip = f"art/{appid}_clip.mp4"
        media[appid] = {"screenshots": shots, "clip": clip, "trailerName": m["trailerName"]}

    # Achievements on every Steam account, for played games.
    accs = steam_owned.steam_accounts(settings)
    spare = next((a["apiKey"].strip() for a in accs if (a.get("apiKey") or "").strip()), "")
    keys = {str(a["steamId"]): (a.get("apiKey") or "").strip() or spare for a in accs}
    print(f"Achievements for {extras.achievements(games, keys)} games")

    out_games = []
    for g, images in zip(games, all_images):
        out_games.append({
            "id": g["id"], "name": g["name"], "launcher": g["launcher"], "installed": g["installed"],
            "installDir": g["installDir"], "launch": g["launch"]["target"],
            "steamAppId": g["artAppId"], "playtimeMinutes": g["playtimeMinutes"],
            "lastPlayed": g["lastPlayed"], "hiddenByDefault": g["hiddenByDefault"],
            "owners": g["owners"], "art": images,
            "media": media.get(g["artAppId"]) if g["installed"] else None, "achievements": g.get("achievements"),
            **{k: details.get(g["artAppId"], {}).get(k) for k in ("tags", "released", "score", "scoreCount")},
        })
        if g["installed"]:
            print(f'{g["launcher"]:<10} {g["name"]:<50} steam={g["artAppId"]} art={",".join(images) or "-"}')

    out = {"scannedAt": int(time.time()), "launchers": launchers.LAUNCHERS,
           "steamAccounts": accounts, "ownedSources": owned, "steamSignedIn": steam_switch.signed_in_steamid(),
           "games": out_games, "errors": result["errors"]}
    with open(os.path.join(WEB, "library.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    files = os.listdir(ART_DIR)
    size = sum(os.path.getsize(os.path.join(ART_DIR, n)) for n in files)
    no_art = [g["name"] for g, i in zip(games, all_images) if not i]
    print(f"\n{len(out_games)} games ({sum(g['installed'] for g in games)} installed), "
          f"{len(files)} images, {size / 1e6:.1f} MB")
    if no_art:
        print("No art:", ", ".join(no_art))

    # Friends, DLC sales, GOG play time and install sizes; then every achievement's name, rarity and icon.
    import runpy
    runpy.run_path(os.path.join(ROOT, "tools", "export_extras.py"), run_name="__main__")
    runpy.run_path(os.path.join(ROOT, "tools", "export_news.py"), run_name="__main__")
    runpy.run_path(os.path.join(ROOT, "tools", "export_achievements.py"), run_name="__main__")


if __name__ == "__main__":
    main()
