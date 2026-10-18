"""The API keys people enter in the app (the menu under their profile picture).

Each key is checked with the service it belongs to before it's saved, and it's saved only in the
person's own settings file. The page never gets a key back: it only learns whether one is saved
and its last four characters.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import config, steam_owned, steam_switch, stores
from .art import UA

STEAM_KEY = re.compile(r"^[0-9A-Fa-f]{32}$")
OFFLINE = "Junction couldn't reach {} to check the key. Check your internet connection and try again."


def _tail(key):
    key = (key or "").strip()
    return f"…{key[-4:]}" if key else None


def _accounts(settings):
    """The settings' Steam accounts as a list, converting the old single-account format."""
    if "steamAccounts" not in settings and settings.get("steamId"):
        settings["steamAccounts"] = [{"name": "Steam", "steamId": str(settings.pop("steamId")),
                                      "apiKey": settings.pop("steamApiKey", "")}]
    return settings.setdefault("steamAccounts", [])


# ---------- the IsThereAnyDeal key Junction comes with ----------
# tools/build.py writes it into junction/_builtin.py (never in git), lightly masked so it isn't plain text in
# the files. That isn't secrecy: anyone with the installer can recover it. It's IsThereAnyDeal's "app key" for
# Junction, which their terms allow apps to carry; people can still use their own key instead.
_MASK = b"junction-prices"


def mask(key):
    import base64
    raw = key.encode()
    return base64.b64encode(bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(raw[::-1]))).decode()


def _unmask(text):
    import base64
    try:
        raw = base64.b64decode(text)
        return bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(raw)).decode()[::-1]
    except (ValueError, UnicodeDecodeError):
        return None


def builtin_prices_key():
    try:
        from ._builtin import ITAD
    except ImportError:
        return None
    return _unmask(ITAD) or None


def prices_key(settings=None):
    """Your own IsThereAnyDeal key if you added one, otherwise the one Junction comes with ("" if neither)."""
    s = settings if settings is not None else config.load_settings()
    return (s.get("itadApiKey") or "").strip() or builtin_prices_key() or ""


def overview():
    """What the settings dialogs show: every Steam account (in settings or signed in on this PC)."""
    s = config.load_settings()
    try:
        on_pc = steam_switch.remembered_accounts()
        signed_in = steam_switch.signed_in_steamid()
    except OSError:
        on_pc, signed_in = {}, None
    hidden = {str(h.get("steamId")): h.get("name") for h in s.get("hiddenSteamAccounts") or []}
    accounts, seen = [], set(hidden)
    for a in steam_owned.steam_accounts(s):
        sid = str(a["steamId"]).strip()
        seen.add(sid)
        accounts.append({"steamId": sid, "name": a.get("name") or (on_pc.get(sid) or {}).get("persona") or sid,
                         "key": _tail(a.get("apiKey")), "onPc": sid in on_pc})
    for sid, u in on_pc.items():
        if sid not in seen:
            accounts.append({"steamId": sid, "name": u.get("persona") or u.get("account") or sid, "key": None, "onPc": True})
    return {"accounts": accounts, "signedIn": signed_in,
            "removed": [{"steamId": sid, "name": name or (on_pc.get(sid) or {}).get("persona") or sid} for sid, name in hidden.items()],
            "prices": _tail(s.get("itadApiKey")), "pricesBuiltIn": bool(builtin_prices_key()),
            "art": _tail(s.get("steamGridDbKey"))}


def _check_steam(key, steamid):
    url = ("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?"
           + urllib.parse.urlencode({"key": key, "steamids": steamid}))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            players = (json.loads(r.read()).get("response") or {}).get("players") or []
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"error": "Steam turned this key down. Check it was copied completely."}
        return {"error": f"Steam answered with an error ({e.code}). Try again in a minute."}
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {"error": OFFLINE.format("Steam")}
    return {"ok": True, "name": players[0].get("personaname") if players else None}


def set_steam_key(steamid, key):
    steamid, key = str(steamid).strip(), (key or "").strip()
    if not steamid.isdigit():
        return {"error": "That isn't a Steam account."}
    name = None
    if key:
        if not STEAM_KEY.match(key):
            return {"error": "A Steam API key is 32 letters and numbers. Check it was copied completely."}
        checked = _check_steam(key, steamid)
        if not checked.get("ok"):
            return checked
        name = checked.get("name")
    s = config.load_settings()
    accounts = _accounts(s)
    acc = next((a for a in accounts if str(a.get("steamId")).strip() == steamid), None)
    if acc is None:
        if not key:
            return {"ok": True}
        persona = (steam_switch.remembered_accounts().get(steamid) or {}).get("persona")
        acc = {"name": name or persona or steamid, "steamId": steamid}
        accounts.append(acc)
    acc["apiKey"] = key
    config.save_settings(s)
    return {"ok": True, "name": acc.get("name")}


def remove_account(steamid):
    """Take a Steam account out of Junction: its key is deleted and its games that aren't installed
    leave the library. Steam itself isn't changed, and the account can be added back."""
    steamid = str(steamid).strip()
    s = config.load_settings()
    accounts = _accounts(s)
    acc = next((a for a in accounts if str(a.get("steamId")).strip() == steamid), None)
    name = (acc or {}).get("name") or (steam_switch.remembered_accounts().get(steamid) or {}).get("persona") or steamid
    s["steamAccounts"] = [a for a in accounts if a is not acc]
    hidden = s.setdefault("hiddenSteamAccounts", [])
    if not any(str(h.get("steamId")) == steamid for h in hidden):
        hidden.append({"steamId": steamid, "name": name})
    config.save_settings(s)
    return {"ok": True, "name": name}


def restore_account(steamid):
    """Add a removed Steam account back (without a key; it borrows another account's until it has one)."""
    steamid = str(steamid).strip()
    s = config.load_settings()
    hidden = s.get("hiddenSteamAccounts") or []
    entry = next((h for h in hidden if str(h.get("steamId")) == steamid), None)
    if entry is None:
        return {"error": "That account isn't removed."}
    s["hiddenSteamAccounts"] = [h for h in hidden if h is not entry]
    if not s["hiddenSteamAccounts"]:
        s.pop("hiddenSteamAccounts")
    accounts = _accounts(s)
    if not any(str(a.get("steamId")).strip() == steamid for a in accounts):
        accounts.append({"name": entry.get("name") or steamid, "steamId": steamid, "apiKey": ""})
    config.save_settings(s)
    return {"ok": True, "name": entry.get("name")}


def _check_prices(key):
    try:
        stores.Itad(key).search("portal", results=1)
    except ValueError as e:
        return {"error": str(e)}
    except urllib.error.HTTPError as e:
        return {"error": f"IsThereAnyDeal answered with an error ({e.code}). Try again in a minute."}
    except (urllib.error.URLError, TimeoutError):
        return {"error": OFFLINE.format("IsThereAnyDeal")}
    return {"ok": True}


def _check_art(key):
    req = urllib.request.Request("https://www.steamgriddb.com/api/v2/search/autocomplete/portal",
                                 headers=dict(UA, Authorization=f"Bearer {key}"))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"error": "SteamGridDB turned this key down. Check it was copied completely."}
        return {"error": f"SteamGridDB answered with an error ({e.code}). Try again in a minute."}
    except (urllib.error.URLError, TimeoutError):
        return {"error": OFFLINE.format("SteamGridDB")}
    return {"ok": True}


def _set_simple(field, key, check):
    key = (key or "").strip()
    if key and not re.fullmatch(r"[\w-]{8,128}", key):
        return {"error": "That doesn't look like a key. Check it was copied completely, without spaces."}
    if key:
        checked = check(key)
        if not checked.get("ok"):
            return checked
    s = config.load_settings()
    if key:
        s[field] = key
    else:
        s.pop(field, None)
    config.save_settings(s)
    return {"ok": True}


def set_prices_key(key):
    return _set_simple("itadApiKey", key, _check_prices)


def set_art_key(key):
    return _set_simple("steamGridDbKey", key, _check_art)
