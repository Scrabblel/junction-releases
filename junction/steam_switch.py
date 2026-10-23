"""Switch the Steam app to another remembered account, then start or install a game.

This is the approach account-switcher tools use: close Steam, mark the target account as the
one to sign in to automatically (registry AutoLoginUser + loginusers.vdf), and start Steam
again through the game's steam:// link. Steam reuses that account's saved sign-in, so Junction
never sees or types a password. If Steam has forgotten the sign-in, it shows its own login window.
"""
import os
import re
import subprocess
import time
import winreg

from .launchers import _ci, _read, _steam_root, parse_vdf

STEAMID64_BASE = 76561197960265728
STEAM_KEY = r"Software\Valve\Steam"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def _loginusers(root):
    return os.path.join(root, "config", "loginusers.vdf")


def remembered_accounts():
    """{steamid: {"account": login name, "persona": display name}} for accounts Steam remembers."""
    root = _steam_root()
    if not root or not os.path.isfile(_loginusers(root)):
        return {}
    users = _ci(parse_vdf(_read(_loginusers(root))), "users") or {}
    return {sid: {"account": u.get("AccountName"), "persona": u.get("PersonaName")}
            for sid, u in users.items() if isinstance(u, dict)}


def signed_in_steamid():
    """SteamID of the account Steam is signed in to (or will sign in to next), or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STEAM_KEY + r"\ActiveProcess") as key:
            active = winreg.QueryValueEx(key, "ActiveUser")[0]
        if active:
            return str(STEAMID64_BASE + int(active))
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STEAM_KEY) as key:
            auto = winreg.QueryValueEx(key, "AutoLoginUser")[0]
    except OSError:
        return None
    return next((sid for sid, u in remembered_accounts().items()
                 if (u["account"] or "").lower() == (auto or "").lower()), None)


def steam_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"],
                         capture_output=True, text=True, creationflags=NO_WINDOW).stdout
    return "steam.exe" in out.lower()


def _mark_auto_login(text, steamid):
    """loginusers.vdf with only `steamid` marked as most recent and allowed to sign in automatically."""
    def fix(m):
        head, body, tail = m.group(1), m.group(3), m.group(4)
        on = "1" if m.group(2) == steamid else "0"
        for key in ("MostRecent", "AllowAutoLogin"):
            pattern = rf'("{key}"\s+")\d(")'
            if re.search(pattern, body, re.I):
                body = re.sub(pattern, rf"\g<1>{on}\g<2>", body, flags=re.I)
            else:
                body = body.rstrip() + f'\n\t\t"{key}"\t\t"{on}"\n\t'
        return head + body + tail
    return re.sub(r'("(\d{17})"\s*\{)([^{}]*)(\})', fix, text)


def switch_and_open(steamid, uri, timeout=30):
    """Open `uri` (steam://rungameid/… or steam://install/…) as account `steamid`.

    Returns True if Steam had to be restarted on another account, False if it was already on it.
    Raises ValueError with a message for the user when switching isn't possible.
    """
    steamid = str(steamid)
    if signed_in_steamid() == steamid:
        os.startfile(uri)
        return False

    root = _steam_root()
    account = remembered_accounts().get(steamid, {}).get("account")
    if not root or not account:
        raise ValueError("Steam doesn't remember this account. Sign in to it once in Steam "
                         "with \"Remember me\" ticked, then try again.")

    if steam_running():
        subprocess.Popen([os.path.join(root, "steam.exe"), "-shutdown"], creationflags=NO_WINDOW)
        deadline = time.time() + timeout
        while steam_running():
            if time.time() > deadline:
                raise ValueError("Steam didn't close in time. Close it yourself and press PLAY again.")
            time.sleep(0.5)

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STEAM_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "AutoLoginUser", 0, winreg.REG_SZ, account)
        winreg.SetValueEx(key, "RememberPassword", 0, winreg.REG_DWORD, 1)
    path = _loginusers(root)
    text = _read(path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(_mark_auto_login(text, steamid))

    os.startfile(uri)  # starts Steam on the new account, then the game
    return True
