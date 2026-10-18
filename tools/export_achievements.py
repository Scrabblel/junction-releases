"""Every achievement of the games in web/library.json: names, descriptions, rarity, who unlocked
what, and the icons packed into a few sprite sheets (an artifact holds at most 511 files).

Writes web/achievements.json and web/ach/sheet{n}.jpg. Run after tools/export_artifact.py.

    python tools/export_achievements.py
"""
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from junction import extras, steam_owned  # noqa: E402

WEB = os.path.join(ROOT, "web")
SHEET_DIR = os.path.join(WEB, "ach")
ICON = 48            # px per icon in the sheet
COLS = 40            # icons per sheet row
PER_SHEET = COLS * 40


OLD_CDN = "https://steamcdn-a.akamaihd.net/steamcommunity/public/images/apps/"
NEW_CDN = "https://shared.akamai.steamstatic.com/community_assets/images/apps/"


def fetch_icon(urls):
    """The first of `urls` that downloads, as an ICON-sized image; each is tried twice.

    Newer icons only exist on Steam's newer image server, so that copy of each URL is tried too.
    """
    urls = [u for u in urls if u]
    urls = urls + [u.replace(OLD_CDN, NEW_CDN) for u in urls if u.startswith(OLD_CDN)]
    for url in urls * 2:
        try:
            with extras.urllib.request.urlopen(extras.urllib.request.Request(url, headers=extras.UA), timeout=20) as r:
                return Image.open(io.BytesIO(r.read())).convert("RGB").resize((ICON, ICON), Image.LANCZOS)
        except Exception:
            continue
    return None


def main():
    with open(os.path.join(ROOT, "local_settings.json"), encoding="utf-8") as f:
        settings = json.load(f)
    accs = steam_owned.steam_accounts(settings)
    key = next((a["apiKey"].strip() for a in accs if (a.get("apiKey") or "").strip()), "")
    with open(os.path.join(WEB, "library.json"), encoding="utf-8") as f:
        lib = json.load(f)
    jobs = {}
    for g in lib["games"]:
        if g.get("achievements") and g.get("steamAppId"):
            jobs.setdefault(str(g["steamAppId"]), set()).update(g["achievements"])
    print(f"{len(jobs)} games with achievements")
    with ThreadPoolExecutor(6) as pool:
        lists = dict(zip(jobs, pool.map(lambda a: extras.achievement_list(key, a, sorted(jobs[a])), jobs)))
    lists = {a: v for a, v in lists.items() if v}

    icons = [(a, i, (item["icon"], item["icongray"])) for a, v in lists.items() for i, item in enumerate(v["list"])]
    print(f"{len(icons)} achievements, fetching icons")
    with ThreadPoolExecutor(16) as pool:
        images = list(pool.map(lambda x: fetch_icon(x[2]), icons))
    print(f"{sum(i is None for i in images)} icons could not be downloaded")

    # Pack the icons game by game, so one game's icons usually sit on one sheet.
    os.makedirs(SHEET_DIR, exist_ok=True)
    for n in os.listdir(SHEET_DIR):
        os.remove(os.path.join(SHEET_DIR, n))
    sheets, slot, out = [], 0, {}
    pos = {}
    for (appid, i, _), img in zip(icons, images):
        if img is None:
            continue
        if not sheets or slot == PER_SHEET:
            sheets.append(Image.new("RGB", (COLS * ICON, (PER_SHEET // COLS) * ICON), (20, 24, 30)))
            slot = 0
        sheets[-1].paste(img, ((slot % COLS) * ICON, (slot // COLS) * ICON))
        pos[(appid, i)] = (len(sheets) - 1, slot)
        slot += 1
    for n, sheet in enumerate(sheets):
        rows = -(-(slot if n == len(sheets) - 1 else PER_SHEET) // COLS)
        sheet.crop((0, 0, COLS * ICON, rows * ICON)).save(os.path.join(SHEET_DIR, f"sheet{n}.jpg"), quality=72, optimize=True)

    for appid, v in lists.items():
        out[appid] = {
            # [name, description, hidden, % of players, sheet, slot]
            "list": [[a["name"], a["desc"], int(a["hidden"]), a["pct"], *pos.get((appid, i), (-1, -1))]
                     for i, a in enumerate(v["list"])],
            "who": v["who"],
        }
    with open(os.path.join(WEB, "achievements.json"), "w", encoding="utf-8") as f:
        json.dump({"icon": ICON, "cols": COLS, "games": out}, f, ensure_ascii=False, separators=(",", ":"))
    size = sum(os.path.getsize(os.path.join(SHEET_DIR, n)) for n in os.listdir(SHEET_DIR))
    print(f"{len(out)} games, {len(sheets)} sheets, {size / 1e6:.1f} MB, "
          f"achievements.json {os.path.getsize(os.path.join(WEB, 'achievements.json')) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
