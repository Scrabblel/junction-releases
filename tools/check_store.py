"""Builds the Store the way a brand-new Junction does, before a release goes out. tools/build.py runs it;
it also runs on its own:

    python tools/check_store.py

A new person who has just been through the welcome: the prices key built into Junction, and a Steam account
from this PC without a key of its own. Their Steam wishlist is public (both 0.9.2 and 0.9.3 broke the Store for exactly this person). The games are this PC's, from
a real scan. It goes through the app itself (Api.get_store, then the price history a game page asks for), so it
catches what a test of one function misses. Takes about half a minute and some 40 IsThereAnyDeal calls.

Exits with an error, and says why, when the Store wouldn't load for that person.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# A public wishlist: Cyberpunk 2077, Elden Ring, The Witcher 3, Stardew Valley, Hades II, and one id Steam doesn't know.
WISHLIST = [1091500, 1245620, 292030, 413150, 1145350, 999999990]


def fail(msg):
    sys.exit(f"Store check failed: {msg}")


def main():
    home = tempfile.mkdtemp(prefix="junction-storecheck-")
    os.environ["JUNCTION_HOME"] = home  # settings and cache of a new person, not yours
    # The Steam app id lookups the scan does are the same for everyone; reusing yours saves about a minute.
    yours = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Junction", "cache")
    os.makedirs(os.path.join(home, "cache"))
    for name in ("appids.json", "logos.json"):
        if os.path.isfile(os.path.join(yours, name)):
            shutil.copy(os.path.join(yours, name), os.path.join(home, "cache", name))
    sys.path.insert(0, ROOT)
    try:
        from junction import app, config, keys, steam_switch, stores
        try:
            steam_id = next(iter(steam_switch.remembered_accounts()), None)
        except OSError:
            steam_id = None
        config.save_settings({"setupDone": True, "steamAccounts": [
            {"name": "New person", "steamId": steam_id or "76561190000000001", "apiKey": ""}]})
        if not keys.builtin_prices_key():
            fail("this build has no built-in prices key, so a new person's Store would be empty.")
        stores.steam_wishlist = lambda steamid, key="": list(WISHLIST)

        t = time.time()
        api = app.Api()
        api._progress = lambda msg: None
        lib = api._scan()  # a real scan of this PC, as on a first start
        if not lib or not lib.get("games"):
            fail("the scan found no games, so there's nothing to check the Store against.")
        store = api.get_store()
        if not store:
            fail("get_store() gave nothing back.")
        games = store.get("games") or {}
        problems = []
        for part in ("frontDeals", "catalog", "topSellers"):
            if not store.get(part):
                problems.append(f"{part} is empty")
            elif any(i not in games for i in store[part]):
                problems.append(f"{part} lists games that aren't in games")
        wish = store.get("wishlist") or []
        if any(not isinstance(w, dict) or w.get("id") not in games for w in wish):
            problems.append("the wishlist has entries that aren't Store games")
        if len(wish) < 4:
            problems.append(f"only {len(wish)} of the {len(WISHLIST) - 1} wishlist games came through")
        if not any(g.get("history") for g in games.values()):
            problems.append("no game has a price history")
        json.dumps(store)  # the page gets it as JSON
        if problems:
            fail("; ".join(problems) + ".")
        # The price history a game page asks for when it opens: a wishlist game, which the build skips.
        later = next((w["id"] for w in wish if not games[w["id"]].get("history")), None)
        if later and api.get_price_history(later) is None:
            fail("a game page couldn't load its price history.")
        print(f"Store check: a new person's Store loads ({len(games)} games, {len(wish)} on the wishlist, "
              f"{time.time() - t:.0f} s)", flush=True)
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        fail("it crashed:\n" + traceback.format_exc())
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
