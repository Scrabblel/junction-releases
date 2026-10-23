"""Add the extras to web/library.json that don't need the art export to run again:

- Steam friends: who they are, who's in a game right now, and which of your games they own.
- DLC on sale on Steam for the Steam games you own and play.
- GOG Galaxy's play time for GOG games.
- How much disk space each installed game takes.
- Your own Steam accounts' profile pictures.

    python tools/export_extras.py        (also run at the end of tools/export_artifact.py)
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from junction import library  # noqa: E402

WEB = os.path.join(ROOT, "web")


def main():
    path = os.path.join(WEB, "library.json")
    with open(path, encoding="utf-8") as f:
        lib = json.load(f)
    library.add_sizes(lib, max_age=0)
    print(f'Sizes for {sum(1 for g in lib["games"] if g.get("sizeBytes"))} installed games')
    try:
        with open(os.path.join(WEB, "store.json"), encoding="utf-8") as f:
            store_appids = [sg["steamAppId"] for sg in json.load(f)["games"].values() if sg.get("steamAppId")]
    except (OSError, ValueError):
        store_appids = []
    # The artifact can't load pictures from Steam, so avatars are embedded.
    library.add_launcher_playtime(lib["games"])
    library.add_extras(lib, inline=library.web_uri, store_appids=store_appids, progress=print)
    print(f'DLC on sale for {len(lib["dlcSales"])} games; {len(lib["friends"])} Steam friends; '
          f'profile pictures for {sum(1 for a in lib["steamAccounts"] if a.get("avatar"))} accounts')
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
