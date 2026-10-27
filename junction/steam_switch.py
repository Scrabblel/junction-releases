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


def active_steamid():
    """SteamID of the account the running Steam is signed in to now, or None: also while Steam shows its
    "Who's playing?" account picker or is still signing in (Steam keeps ActiveUser at 0 until it's signed in)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STEAM_KEY + r"\ActiveProcess") as key:
            active = winreg.QueryValueEx(key, "ActiveUser")[0]
        if active:
            return str(STEAMID64_BASE + int(active))
    except (OSError, TypeError, ValueError):
        pass
    return None


def running_appid():
    """The Steam game running now, by app id (Steam keeps it in RunningAppID), or None. Also for games started
    outside Junction."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STEAM_KEY) as key:
            appid = winreg.QueryValueEx(key, "RunningAppID")[0]
        return int(appid) or None
    except (OSError, TypeError, ValueError):
        return None


def signed_in_steamid():
    """SteamID of the account Steam is signed in to (or will sign in to next), or None."""
    active = active_steamid()
    if active:
        return active
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


def _signed_in_soon(wait):
    """The account the running Steam is signed in to, waiting up to `wait` seconds while it's signed in to none: a
    Steam that has just started takes a moment to sign in. Still none after that: it's at the account picker."""
    deadline = time.time() + wait
    while True:
        active = active_steamid()
        if active or time.time() >= deadline:
            return active
        time.sleep(0.5)


# Steam's own "show the account picker every time Steam starts". While it's on, Steam waits at "Who's playing?" for a
# click, whatever account it's told to sign in to.
PICKER = re.compile(rb'("AlwaysShowUserChooser"\s+")1(")', re.I)


def _config(root):
    return os.path.join(root, "config", "config.vdf")


def picker_always_shown(root=None):
    """Whether Steam is set to show its account picker every time it starts."""
    root = root or _steam_root()
    try:
        with open(_config(root), "rb") as f:
            return bool(PICKER.search(f.read()))
    except (OSError, TypeError):
        return False


def _stop_showing_picker(root):
    """Turn Steam's always-show-the-picker off. Only while Steam is closed: it saves config.vdf as it closes."""
    path = _config(root)
    with open(path, "rb") as f:
        text = f.read()
    fixed = PICKER.sub(rb"\g<1>0\g<2>", text)
    if fixed != text:
        with open(path + ".junction", "wb") as f:
            f.write(fixed)
        os.replace(path + ".junction", path)


def switch_and_open(steamid, uri, skip_picker=False, timeout=30, picker_wait=6):
    """Open `uri` (steam://rungameid/… or steam://install/…) as account `steamid`.

    Returns "same" when Steam was already on that account (or signs in to it by itself), "switched" when Junction
    restarted it on that account, and "picker" when Steam waits at "Who's playing?" for the person to pick it.
    Raises ValueError with a message for the user when switching isn't possible.

    A Steam at its account picker is signed in to no one, so the game would wait there: Junction restarts it on this
    account, as for a switch, and a Steam that isn't running is always told to sign in to this account by itself.
    That can't get past Steam's own "show the account picker every time" unless the person let Junction turn it off
    (skip_picker, the skipSteamPicker setting); otherwise Junction leaves it on and says to pick the account.
    """
    steamid = str(steamid)
    root = _steam_root()
    running = steam_running()
    waits = picker_always_shown(root) and not skip_picker  # Steam stops at its picker, whatever Junction does
    if running:
        # A Steam that has just started may still be signing in, unless it's set to wait at the picker anyway.
        active = active_steamid() if waits else _signed_in_soon(picker_wait)
        if active == steamid:
            os.startfile(uri)
            return "same"
        if active is None and waits:  # at the picker: a restart would only bring it back there
            os.startfile(uri)  # brings Steam's window up
            return "picker"
    was_next = not running and signed_in_steamid() == steamid

    account = remembered_accounts().get(steamid, {}).get("account")
    if not root or not account:
        if was_next:  # Steam signs in to it by itself; nothing for Junction to set
            os.startfile(uri)
            return "picker" if waits else "same"
        raise ValueError("Steam doesn't remember this account. Sign in to it once in Steam "
                         "with \"Remember me\" ticked, then try again.")

    if running:
        subprocess.Popen([os.path.join(root, "steam.exe"), "-shutdown"], creationflags=NO_WINDOW)
        deadline = time.time() + timeout
        while steam_running():
            if time.time() > deadline:
                raise ValueError("Steam didn't close in time. Close it yourself and press PLAY again.")
            time.sleep(0.5)

    if skip_picker and picker_always_shown(root):
        try:
            _stop_showing_picker(root)
        except OSError:
            waits = True

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STEAM_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "AutoLoginUser", 0, winreg.REG_SZ, account)
        winreg.SetValueEx(key, "RememberPassword", 0, winreg.REG_DWORD, 1)
    path = _loginusers(root)
    text = _read(path)
    marked = _mark_auto_login(text, steamid)
    if marked != text:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(marked)

    os.startfile(uri)  # starts Steam on this account, then the game
    return "picker" if waits else "same" if was_next else "switched"
