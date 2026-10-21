"""Find installed games across the PC game launchers on this Windows machine.

Each finder returns a list of game dicts with the same shape (see _game). Nothing here
needs a login: everything is read from the registry and the launchers' local files.
"""
import glob
import json
import os
import re
import string
import winreg

LAUNCHERS = {
    "steam": "Steam",
    "epic": "Epic Games",
    "gog": "GOG Galaxy",
    "ubisoft": "Ubisoft Connect",
    "ea": "EA app",
    "battlenet": "Battle.net",
    "rockstar": "Rockstar Games",
    "xbox": "Xbox",
    "manual": "Added by you",
}

HKCU, HKLM = winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE
UNINSTALL_KEYS = [
    (HKLM, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (HKLM, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (HKCU, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
]

# Steam entries that are runtimes or tools rather than games.
STEAM_SKIP_IDS = {"228980", "1070560", "1391110", "1628350", "1493710"}
STEAM_SKIP_NAME = re.compile(r"redistributable|steamworks|proton|steam linux runtime|dedicated server|\bsdk\b"
                             r"|staging branch| - unstable$| RU$", re.I)
# Installed through Steam but not games: listed, hidden until the user un-hides them.
STEAM_TOOL_IDS = {"431960", "250820"}  # Wallpaper Engine, SteamVR

_BNET_LOCALE = re.compile(r"_(enus|engb|dede|frfr|eses|esmx|itit|ptbr|ruru|kokr|zhtw|zhcn|plpl|jajp)$")


def bnet_product(uid):
    """A Battle.net install id without its language: "wow_enus" -> "wow"."""
    return _BNET_LOCALE.sub("", (uid or "").lower())


# Battle.net uninstall --uid values mapped to the product codes `Battle.net.exe --exec="launch X"` takes.
BNET_CODES = {
    "wow": "WoW", "wow_classic": "WoWC", "odin": "ODIN", "d3": "D3", "diablo3": "D3",
    "fenris": "Fen", "osi": "OSI", "anbs": "ANBS", "pro": "Pro", "prometheus": "Pro",
    "hs_beta": "WTCG", "heroes": "Hero", "s1": "S1", "s2": "S2", "w3": "W3",
    "viper": "VIPR", "zeus": "ZEUS", "lazarus": "LAZR", "fore": "FORE", "auks": "AUKS",
    "wlby": "WLBY", "rtro": "RTRO",
}


# ---------- small helpers ----------

def _reg_values(root, path):
    try:
        with winreg.OpenKey(root, path) as key:
            values, i = {}, 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    return values
                values[name] = value
                i += 1
    except OSError:
        return None


def _reg_subkeys(root, path):
    try:
        with winreg.OpenKey(root, path) as key:
            names, i = [], 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, i))
                except OSError:
                    return names
                i += 1
    except OSError:
        return []


def _uninstall_entries():
    for root, path in UNINSTALL_KEYS:
        for sub in _reg_subkeys(root, path):
            values = _reg_values(root, path + "\\" + sub)
            if values and values.get("DisplayName"):
                yield sub, values


def _exe_from_icon(icon):
    """DisplayIcon looks like '"C:\\Game\\game.exe",0'; return the bare path."""
    if not icon:
        return None
    path = icon.split(",")[0].strip().strip('"')
    return path if path.lower().endswith(".exe") and os.path.isfile(path) else None


def _clean_name(name):
    return re.sub(r"\s+", " ", re.sub(r"[™®©]", "", name or "")).strip()


def _game(launcher, key, name, launch, install_dir=None, art_appid=None,
          playtime=None, last_played=None, local_art=None, hidden_by_default=False):
    return {
        "id": f"{launcher}:{key}",
        "name": _clean_name(name),
        "launcher": launcher,
        "installDir": install_dir,
        "launch": launch,
        "artAppId": art_appid,
        "playtimeMinutes": playtime,
        "lastPlayed": last_played,
        "localArt": local_art,
        "hiddenByDefault": hidden_by_default,
        "installed": True,
        "owners": [],  # SteamIDs of the accounts that own it (Steam games only)
    }


_VDF_TOKEN = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])')


def parse_vdf(text):
    """Parse Valve's KeyValues text format (libraryfolders.vdf, *.acf) into nested dicts."""
    stack, key = [{}], None
    for m in _VDF_TOKEN.finditer(text):
        word, brace = m.group(1), m.group(2)
        if brace == "{":
            child = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = word.replace("\\\\", "\\")
        else:
            stack[-1][key] = word.replace("\\\\", "\\")
            key = None
    return stack[0]


def _ci(d, key):
    """Case-insensitive dict lookup; Steam's files mix 'apps' and 'Apps'."""
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return None


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


# ---------- Steam ----------

def _steam_root():
    path = (_reg_values(HKCU, r"Software\Valve\Steam") or {}).get("SteamPath")
    if not path:
        path = (_reg_values(HKLM, r"SOFTWARE\WOW6432Node\Valve\Steam") or {}).get("InstallPath")
    return os.path.normpath(path) if path and os.path.isdir(path) else None


STEAMID64_BASE = 76561197960265728


def _my_steam_users():
    """The userdata folder names (account ids) of the Steam accounts that count: the ones listed in Junction, or,
    when none are listed yet, every account on this PC except the ones removed in Junction."""
    try:
        from . import config
        s = config.load_settings()
    except Exception:  # noqa: BLE001
        return None, set()
    accs = s.get("steamAccounts")
    if accs is None and s.get("steamId"):
        accs = [{"steamId": s["steamId"]}]
    removed = {str(h.get("steamId")) for h in s.get("hiddenSteamAccounts") or []}
    to_id = lambda sid: str(int(sid) - STEAMID64_BASE)  # noqa: E731
    mine = {to_id(a["steamId"]) for a in accs or [] if str(a.get("steamId") or "").strip().isdigit()
            and str(a["steamId"]).strip() not in removed}
    return (mine or None), {to_id(r) for r in removed if r.isdigit()}


def _steam_stats(root):
    """Playtime (minutes) and last played time per app, summed over your Steam accounts on this PC.
    Other people's accounts on the same PC, and accounts removed in Junction, don't count."""
    stats = {}
    only, removed = _my_steam_users()
    for cfg in glob.glob(os.path.join(root, "userdata", "*", "config", "localconfig.vdf")):
        user = os.path.basename(os.path.dirname(os.path.dirname(cfg)))
        if (only is not None and user not in only) or user in removed:
            continue
        try:
            data = parse_vdf(_read(cfg))
        except OSError:
            continue
        node = _ci(data, "UserLocalConfigStore")
        for part in ("Software", "Valve", "Steam", "apps"):
            node = _ci(node, part)
        for appid, info in (node or {}).items():
            if not isinstance(info, dict):
                continue
            s = stats.setdefault(appid, {"playtime": 0, "last_played": 0})
            s["playtime"] += int(info.get("Playtime") or 0)
            s["last_played"] = max(s["last_played"], int(info.get("LastPlayed") or 0))
    return stats


def find_steam():
    root = _steam_root()
    if not root:
        return []
    libraries = [root]
    folders_file = os.path.join(root, "steamapps", "libraryfolders.vdf")
    if os.path.isfile(folders_file):
        for entry in (_ci(parse_vdf(_read(folders_file)), "libraryfolders") or {}).values():
            path = _ci(entry, "path")
            if path and os.path.normpath(path).lower() not in (l.lower() for l in libraries):
                libraries.append(os.path.normpath(path))

    stats = _steam_stats(root)
    games, seen = [], set()
    for lib in libraries:
        for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            state = _ci(parse_vdf(_read(acf)), "AppState") or {}
            appid, name = state.get("appid"), state.get("name") or ""
            if not appid or appid in seen or appid in STEAM_SKIP_IDS or STEAM_SKIP_NAME.search(name):
                continue
            if not int(state.get("StateFlags") or 0) & 4:  # 4 = fully installed
                continue
            seen.add(appid)
            s = stats.get(appid, {})
            games.append(_game(
                "steam", appid, name,
                launch={"type": "uri", "target": f"steam://rungameid/{appid}"},
                install_dir=os.path.join(lib, "steamapps", "common", state.get("installdir", "")),
                art_appid=int(appid),
                playtime=s.get("playtime") or None,
                last_played=s.get("last_played") or None,
                hidden_by_default=appid in STEAM_TOOL_IDS,
            ))
    return games


def steam_downloads():
    """Games Steam is downloading or updating right now: [{id, name, done, total}] in bytes.

    Steam writes progress into each game's appmanifest while it downloads (BytesDownloaded of
    BytesToDownload), so this is as fresh as Steam's last write, usually a few seconds.
    """
    root = _steam_root()
    if not root:
        return []
    libraries = [root]
    folders_file = os.path.join(root, "steamapps", "libraryfolders.vdf")
    if os.path.isfile(folders_file):
        for entry in (_ci(parse_vdf(_read(folders_file)), "libraryfolders") or {}).values():
            path = _ci(entry, "path")
            if path:
                libraries.append(os.path.normpath(path))
    out = []
    for lib in dict.fromkeys(l.lower() for l in libraries):
        for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            state = _ci(parse_vdf(_read(acf)), "AppState") or {}
            total, done = int(state.get("BytesToDownload") or 0), int(state.get("BytesDownloaded") or 0)
            flags = int(state.get("StateFlags") or 0)
            if total and done < total and flags & (2 | 1024):  # 2 = update required, 1024 = update running
                out.append({"id": f"steam:{state.get('appid')}", "name": _clean_name(state.get("name")),
                            "done": done, "total": total, "paused": not flags & 1024})
    return out


# ---------- Epic Games ----------

def find_epic():
    base = (_reg_values(HKLM, r"SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher") or {}).get("AppDataPath")
    base = base or r"C:\ProgramData\Epic\EpicGamesLauncher\Data"
    games = []
    for item in glob.glob(os.path.join(base, "Manifests", "*.item")):
        try:
            m = json.loads(_read(item))
        except (OSError, ValueError):
            continue
        app = m.get("AppName")
        if m.get("bIsIncompleteInstall") or "games" not in (m.get("AppCategories") or []):
            continue
        if m.get("MainGameAppName") and m["MainGameAppName"] != app:  # DLC
            continue
        loc = m.get("InstallLocation")
        if not loc or not os.path.isdir(loc):
            continue
        target = (f"com.epicgames.launcher://apps/{m.get('CatalogNamespace')}%3A"
                  f"{m.get('CatalogItemId')}%3A{app}?action=launch&silent=true")
        games.append(_game("epic", app, m.get("DisplayName") or app,
                           launch={"type": "uri", "target": target}, install_dir=loc))
    return games


# ---------- GOG Galaxy ----------

def find_gog():
    base = r"SOFTWARE\WOW6432Node\GOG.com"
    client_dir = (_reg_values(HKLM, base + r"\GalaxyClient\paths") or {}).get("client")
    client_exe = (_reg_values(HKLM, base + r"\GalaxyClient") or {}).get("clientExecutable") or "GalaxyClient.exe"
    galaxy = os.path.join(client_dir, client_exe) if client_dir else None
    if galaxy and not os.path.isfile(galaxy):
        galaxy = None

    games = []
    for gid in _reg_subkeys(HKLM, base + r"\Games"):
        v = _reg_values(HKLM, base + "\\Games\\" + gid) or {}
        path = v.get("path")
        if v.get("dependsOn") or not path or not os.path.isdir(path):  # dependsOn = DLC
            continue
        if galaxy:  # through Galaxy, so it tracks playtime and syncs saves
            launch = {"type": "cmd", "target": f'"{galaxy}" /command=runGame /gameId={gid} /path="{path}"'}
        elif v.get("exe"):
            launch = {"type": "exe", "target": v["exe"], "cwd": v.get("workingDir") or path}
        else:
            continue
        games.append(_game("gog", gid, v.get("gameName") or os.path.basename(path), launch=launch, install_dir=path))
    return games


# ---------- Ubisoft Connect ----------

def find_ubisoft():
    names = {}
    for sub, v in _uninstall_entries():
        m = re.match(r"Uplay Install (\d+)$", sub)
        if m:
            names[m.group(1)] = v["DisplayName"]

    base = r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs"
    games = []
    for uid in _reg_subkeys(HKLM, base):
        install = (_reg_values(HKLM, base + "\\" + uid) or {}).get("InstallDir")
        if not install or not os.path.isdir(install):
            continue
        install = os.path.normpath(install)
        games.append(_game("ubisoft", uid, names.get(uid) or os.path.basename(install),
                           launch={"type": "uri", "target": f"uplay://launch/{uid}/0"}, install_dir=install))
    return games


# ---------- EA app ----------

def find_ea():
    games = []
    for sub, v in _uninstall_entries():
        if "eainstaller" not in (v.get("UninstallString") or "").lower():
            continue
        exe = _exe_from_icon(v.get("DisplayIcon"))
        if not exe:
            continue
        # EA games hand off to the EA app themselves when started directly.
        games.append(_game("ea", sub.strip("{}"), v["DisplayName"],
                           launch={"type": "exe", "target": exe, "cwd": os.path.dirname(exe)},
                           install_dir=(v.get("InstallLocation") or os.path.dirname(exe)).rstrip("\\")))
    return games


# ---------- Battle.net ----------

def find_battlenet():
    entries = [(sub, v) for sub, v in _uninstall_entries()
               if "blizzard uninstaller" in (v.get("UninstallString") or "").lower()]
    bnet = None
    for sub, v in entries:
        if "--uid=battle.net" in v["UninstallString"].lower():
            candidate = os.path.join(v.get("InstallLocation") or "", "Battle.net.exe")
            bnet = candidate if os.path.isfile(candidate) else None
    bnet = bnet or next((p for p in (r"C:\Program Files (x86)\Battle.net\Battle.net.exe",) if os.path.isfile(p)), None)

    games = []
    for sub, v in entries:
        m = re.search(r"--uid=(\S+)", v["UninstallString"])
        uid = m.group(1).strip('"').lower() if m else None
        if not uid or uid == "battle.net":
            continue
        exe = _exe_from_icon(v.get("DisplayIcon"))
        code = BNET_CODES.get(uid) or BNET_CODES.get(bnet_product(uid))
        if bnet and code:
            launch = {"type": "cmd", "target": f'"{bnet}" --exec="launch {code}"'}
        elif exe:
            launch = {"type": "exe", "target": exe, "cwd": os.path.dirname(exe)}
        else:
            continue
        games.append(_game("battlenet", uid, v["DisplayName"], launch=launch, install_dir=v.get("InstallLocation")))
    return games


# ---------- Rockstar Games Launcher ----------

def find_rockstar():
    """Games installed from Rockstar's own store. Only those leave an `uninstall.exe -uninstall=<title>` entry;
    Rockstar games bought on Steam or Epic are found there instead, so nothing is listed twice."""
    folder = (_reg_values(HKLM, r"SOFTWARE\WOW6432Node\Rockstar Games\Launcher") or {}).get("InstallFolder")
    patcher = os.path.join(folder, "LauncherPatcher.exe") if folder else None
    games = []
    for sub, v in _uninstall_entries():
        m = re.search(r"rockstar games\\launcher\\uninstall\.exe\"?\s.*?-uninstall=(\w+)", v.get("UninstallString") or "", re.I)
        if not m or m.group(1).lower() == "launcher":
            continue
        title = m.group(1).lower()
        exe = _exe_from_icon(v.get("DisplayIcon"))
        install = (v.get("InstallLocation") or "").strip().strip('"').rstrip("\\") or (os.path.dirname(exe) if exe else None)
        if patcher and os.path.isfile(patcher):  # what Rockstar's own desktop shortcuts run
            launch = {"type": "cmd", "target": f'"{patcher}" -minmodeApp={title}'}
        elif exe:  # Rockstar games hand off to the launcher themselves when started directly
            launch = {"type": "exe", "target": exe, "cwd": os.path.dirname(exe)}
        else:
            continue
        games.append(_game("rockstar", title, v["DisplayName"], launch=launch, install_dir=install))
    return games


# ---------- Xbox / Microsoft Store (PC Game Pass) ----------

def _local(tag):
    return tag.split("}")[-1]


def find_xbox():
    import xml.etree.ElementTree as ET

    packages = _reg_subkeys(HKCU, r"Software\Classes\Local Settings\Software\Microsoft\Windows"
                                  r"\CurrentVersion\AppModel\Repository\Packages")
    games = []
    for drive in string.ascii_uppercase:
        root = f"{drive}:\\XboxGames"
        if not os.path.isdir(root):
            continue
        for folder in os.listdir(root):
            content = os.path.join(root, folder, "Content")
            cfg = os.path.join(content, "MicrosoftGame.config")
            if not os.path.isfile(cfg):
                continue
            try:
                tree = ET.parse(cfg).getroot()
            except ET.ParseError:
                continue
            nodes = {}
            for el in tree.iter():
                nodes.setdefault(_local(el.tag), el)
            exe, identity, visuals = nodes.get("Executable"), nodes.get("Identity"), nodes.get("ShellVisuals")
            if exe is None or identity is None:  # expansions have no executable of their own
                continue
            ident = identity.get("Name")
            full = next((p for p in packages if p.startswith(ident + "_")), None)
            if not full:
                continue
            parts = full.split("_")
            family = f"{parts[0]}_{parts[-1]}"

            app_id = exe.get("Id") or "Game"
            manifest = os.path.join(content, "appxmanifest.xml")
            if os.path.isfile(manifest):
                m = re.search(r'<Application\s[^>]*\bId="([^"]+)"', _read(manifest))
                app_id = m.group(1) if m else app_id

            name = exe.get("OverrideDisplayName") or (visuals.get("DefaultDisplayName") if visuals is not None else None) or folder
            logo = None
            if visuals is not None:
                for attr in ("Square480x480Logo", "Square150x150Logo", "StoreLogo"):
                    rel = visuals.get(attr)
                    if rel and os.path.isfile(os.path.join(content, rel)):
                        logo = os.path.join(content, rel)
                        break
            games.append(_game("xbox", ident, name,
                               launch={"type": "cmd", "target": f'explorer.exe "shell:AppsFolder\\{family}!{app_id}"'},
                               install_dir=content, local_art=logo))
    return games


FINDERS = {
    "steam": find_steam, "epic": find_epic, "gog": find_gog, "ubisoft": find_ubisoft,
    "ea": find_ea, "battlenet": find_battlenet, "rockstar": find_rockstar, "xbox": find_xbox,
}


# The launchers' own apps, by their name in Windows' installed programs (uninstalling removes the entry).
CLIENT_NAMES = {"epic": "epic games launcher", "ea": "ea app", "ubisoft": "ubisoft connect"}


def clients_on_pc():
    """Which of these launchers' own apps are installed here, e.g. ["ea", "epic"]."""
    names = {(v.get("DisplayName") or "").strip().lower() for _, v in _uninstall_entries()}
    return sorted(k for k, n in CLIENT_NAMES.items() if n in names)


def find_all():
    """Run every finder. One broken launcher never stops the others."""
    games, errors = [], {}
    for launcher, finder in FINDERS.items():
        try:
            games.extend(finder())
        except Exception as e:  # noqa: BLE001 - report and keep going
            errors[launcher] = f"{type(e).__name__}: {e}"
    games.sort(key=lambda g: g["name"].lower())
    return {"games": games, "errors": errors}


if __name__ == "__main__":
    result = find_all()
    for g in result["games"]:
        print(f'{g["launcher"]:<10} {g["name"]:<45} {g["launch"]["target"][:90]}')
    print(f'\n{len(result["games"])} games', result["errors"] or "")
