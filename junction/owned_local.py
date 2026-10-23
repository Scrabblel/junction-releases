"""Games owned on GOG, Epic and Ubisoft that aren't installed, read from the launchers' own files.

No login and no API key: each launcher keeps a copy of your library on disk.
- GOG Galaxy: its SQLite database (storage/galaxy-2.0.db).
- Epic Games Launcher: its catalogue cache (Data/Catalog/catcache.bin, base64 JSON).
- Ubisoft Connect: its ownership cache (cache/ownership/<user>, signed protobuf), with names
  from cache/configuration/configurations. The ownership file also holds CD keys; only
  product IDs and add-on flags are read from it, nothing else.

These copies are only as fresh as the last time each launcher was opened and signed in.

Play time comes from the same kind of files: GOG Galaxy's database, Ubisoft Connect's stats cache
(cache/game_stats) and, for last played only, Battle.net's settings file.
"""
import base64
import contextlib
import datetime
import glob
import json
import os
import re
import shutil
import sqlite3
import struct
import tempfile

from .art import normalize
from .launchers import HKLM, _game, _reg_values

# Epic library entries that are tools or extras rather than games.
EPIC_SKIP_NAME = re.compile(r"\b(editor|resource archiver|content|soundtrack|art ?book|wallpaper|map pack)\b|beta$", re.I)
# Where Ubisoft Connect loads the pictures named in its configuration cache.
UBISOFT_ASSETS = "https://ubistatic3-a.akamaihd.net/orbit/uplay_launcher_3_0/assets/"


# ---------- GOG Galaxy ----------

def _galaxy_db():
    return os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "GOG.com", "Galaxy", "storage", "galaxy-2.0.db")


def _copy_db(path, copy):
    """Copy an SQLite database Galaxy has open, with its write-ahead log: the newest play time can be in there."""
    shutil.copyfile(path, copy)
    for extra in ("-wal", "-shm"):
        if os.path.isfile(path + extra):
            try:
                shutil.copyfile(path + extra, copy + extra)
            except OSError:
                pass


@contextlib.contextmanager
def _galaxy():
    """Galaxy's database, or None without Galaxy. Galaxy keeps it open while it runs, so this reads a copy."""
    path = _galaxy_db()
    if not os.path.isfile(path):
        yield None
        return
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "galaxy.db")
        _copy_db(path, copy)
        db = sqlite3.connect(copy)
        try:
            yield db
        finally:
            db.close()


def gog_owned():
    """[(product id, title, cover url)] for GOG games in this PC's Galaxy library, DLC and hidden duplicates left out."""
    with _galaxy() as db:
        if db is None:
            return []
        rows = db.execute("""
            select lr.releaseKey, gp.value from LibraryReleases lr
            join GamePieces gp on gp.releaseKey = lr.releaseKey
            join GamePieceTypes t on t.id = gp.gamePieceTypeId and t.type = 'title'
            left join ReleaseProperties rp on rp.releaseKey = lr.releaseKey
            where lr.releaseKey like 'gog\\_%' escape '\\'
              and coalesce(rp.isDlc, 0) = 0 and coalesce(rp.isVisibleInLibrary, 1) = 1
            group by lr.releaseKey""").fetchall()
        covers = dict(db.execute("""
            select gp.releaseKey, gp.value from GamePieces gp
            join GamePieceTypes t on t.id = gp.gamePieceTypeId and t.type = 'originalImages'""").fetchall())
    games = []
    for key, value in rows:
        try:
            title = json.loads(value).get("title")
        except ValueError:
            continue
        try:
            cover = json.loads(covers.get(key) or "{}").get("verticalCover")
        except ValueError:
            cover = None
        if title:
            games.append((key.split("_", 1)[1], title, cover))
    return games


# ---------- Epic Games ----------

def epic_owned():
    """[(appName, namespace, catalogItemId, title, cover url)] for games in the Epic launcher's catalogue cache."""
    base = (_reg_values(HKLM, r"SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher") or {}).get("AppDataPath")
    path = os.path.join(base or r"C:\ProgramData\Epic\EpicGamesLauncher\Data", "Catalog", "catcache.bin")
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as f:
        items = json.loads(base64.b64decode(f.read()))
    games = []
    for i in items:
        if not any(c.get("path") == "games" for c in i.get("categories") or []):
            continue
        if (i.get("mainGameItem") or {}).get("id"):  # DLC
            continue
        app = next((r.get("appId") for r in i.get("releaseInfo") or [] if r.get("appId")), None)
        # The cache stores ™ and ® as "?" ("LEGO? Batman?"); a "?" straight after a word is one of those.
        title = re.sub(r"(?<=\w)\?(?=[\s:]|$)", "", i.get("title") or "")
        cover = next((k.get("url") for k in i.get("keyImages") or [] if k.get("type") == "DieselGameBoxTall"), None)
        if app and title and not EPIC_SKIP_NAME.search(title):
            games.append((app, i["namespace"], i["id"], title, cover))
    return games


# ---------- Ubisoft Connect ----------

def _varint(b, i):
    result = shift = 0
    while True:
        c = b[i]
        i += 1
        result |= (c & 0x7F) << shift
        shift += 7
        if c < 0x80:
            return result, i


def _protobuf(b):
    """Decode one protobuf message into [(field, wire type, value)] without a schema."""
    i, out = 0, []
    while i < len(b):
        key, i = _varint(b, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(b, i)
        elif wire == 2:
            n, i = _varint(b, i)
            value, i = b[i:i + n], i + n
        elif wire == 5:
            value, i = b[i:i + 4], i + 4
        elif wire == 1:
            value, i = b[i:i + 8], i + 8
        else:
            raise ValueError(f"unknown protobuf wire type {wire}")
        out.append((field, wire, value))
    if i != len(b):
        raise ValueError("truncated protobuf")
    return out


def _ubisoft_dir():
    path = (_reg_values(HKLM, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher") or {}).get("InstallDir")
    path = path or r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher"
    return path if os.path.isdir(path) else None


def _ubisoft_names(base):
    """{product id: (display name, bought on another platform, cover url)} from the launcher's configuration cache."""
    path = os.path.join(base, "cache", "configuration", "configurations")
    if not os.path.isfile(path):
        return {}
    with open(path, "rb") as f:
        records = _protobuf(f.read())
    names = {}
    for _, wire, value in records:
        if wire != 2:
            continue
        rec = {field: v for field, _, v in _protobuf(value)}
        if 1 not in rec or 3 not in rec:
            continue
        y = rec[3].decode("utf-8", "replace").replace("\r\n", "\n")
        root = re.search(r"^root:\n((?:[ \t].*\n?|\n)*)", y, re.M)
        if not root:
            continue
        block = root.group(1)
        top = dict(re.findall(r"^  (\w+): *(.*)$", block, re.M))  # only the 2-space keys of root
        name = (top.get("display_name") or top.get("name") or "").strip().strip("\"'")
        loc = re.search(r"^localizations:\n  default:\n((?:    .*\n?)*)", y, re.M)

        def localized(value):
            value = (value or "").strip().strip("\"'")
            m = loc and value and re.search(r"^    " + re.escape(value) + r": *(.+)$", loc.group(1), re.M)
            return m.group(1).strip().strip("\"'") if m else value

        name, thumb = localized(name), localized(top.get("thumb_image"))
        cover = UBISOFT_ASSETS + thumb if re.fullmatch(r"\w+\.(jpg|png)", thumb) else None
        if name and not re.fullmatch(r"l\d+", name):
            names[rec[1]] = (name, "third_party_platform" in top, cover)
    return names


def ubisoft_owned():
    """[(product id, name, cover url)] for games (not add-ons) in Ubisoft Connect's ownership cache."""
    base = _ubisoft_dir()
    if not base:
        return []
    names = _ubisoft_names(base)
    games, seen = [], set()
    for path in glob.glob(os.path.join(base, "cache", "ownership", "*")):
        with open(path, "rb") as f:
            data = f.read()
        # 4-byte version, 256-byte signature, 4-byte length, then the protobuf.
        if len(data) < 264 or struct.unpack_from("<I", data, 260)[0] != len(data) - 264:
            continue
        for field, wire, value in _protobuf(data[264:]):
            if field != 1 or wire != 2:
                continue
            ints = {}
            for f2, w2, v2 in _protobuf(value):
                if w2 == 0:
                    ints.setdefault(f2, v2)
            pid = ints.get(1)
            if pid is None or ints.get(6, 0) != 0 or pid in seen:  # field 6 set = add-on
                continue
            name, elsewhere, cover = names.get(pid, (None, False, None))
            if name and not elsewhere:  # bought on Steam/Epic: those launchers already list it
                seen.add(pid)
                games.append((str(pid), name, cover))
    return games


# ---------- merge ----------

def _add(games, launcher, key, name, install_uri, cover_url):
    g = _game(launcher, key, name, launch={"type": "uri", "target": install_uri})
    g["installed"] = False
    g["coverUrl"] = cover_url  # the launcher's own cover, used before Steam's
    games.append(g)
    return g


def add_owned(games):
    """Add the uninstalled games owned on GOG, Epic and Ubisoft to `games` (from launchers.find_all).

    Returns {launcher: {"games": owned count, "added": not-installed count, "error"?: str}}.
    """
    sources = {
        "gog": (gog_owned, lambda gid, title, cover: (gid, title, f"goggalaxy://openGameView/{gid}", cover)),
        "epic": (epic_owned, lambda app, ns, cid, title, cover: (
            app, title, f"com.epicgames.launcher://apps/{ns}%3A{cid}%3A{app}?action=install", cover)),
        "ubisoft": (ubisoft_owned, lambda pid, name, cover: (pid, name, f"uplay://install/{pid}", cover)),
    }
    summary = {}
    for launcher, (reader, shape) in sources.items():
        s = summary[launcher] = {"games": 0, "added": 0}
        try:
            owned = reader()
        except Exception as e:  # noqa: BLE001 - a changed file format must not break the scan
            s["error"] = f"{type(e).__name__}: {e}"
            continue
        have = {g["id"] for g in games if g["launcher"] == launcher}
        have_names = {normalize(g["name"]) for g in games if g["launcher"] == launcher}
        for row in owned:
            key, name, uri, cover = shape(*row)
            s["games"] += 1
            if f"{launcher}:{key}" in have or normalize(name) in have_names:
                continue
            _add(games, launcher, key, name, uri, cover)
            have_names.add(normalize(name))
            s["added"] += 1
    games.sort(key=lambda g: g["name"].lower())
    return summary


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for label, rows in (("GOG", gog_owned()), ("Epic", epic_owned()), ("Ubisoft", ubisoft_owned())):
        print(f"{label}: {len(rows)}")
        for r in rows:
            print("   ", r[-2], "|", (r[-1] or "no cover")[:70])


UBI_TOTALS = ("timeplayed", "playtime", "totalplaytime", "totaltimeplayed")


def _ubisoft_spaces(base):
    """{space id: [product ids]}: a game's editions share one space, and its stats are kept per space."""
    path = os.path.join(base, "cache", "configuration", "configurations")
    if not os.path.isfile(path):
        return {}
    with open(path, "rb") as f:
        records = _protobuf(f.read())
    spaces = {}
    for _, wire, value in records:
        if wire != 2:
            continue
        rec = {field: v for field, _, v in _protobuf(value)}
        if 1 not in rec or 3 not in rec:
            continue
        for space in set(re.findall(r"^\s*space_id: *['\"]?([0-9a-f-]{36})", rec[3].decode("utf-8", "replace"), re.M)):
            spaces.setdefault(space, []).append(str(rec[1]))
    return spaces


def ubisoft_spaces():
    """{space id: [product ids]} for the games Ubisoft Connect knows on this PC; {} without Ubisoft Connect."""
    base = _ubisoft_dir()
    return _ubisoft_spaces(base) if base else {}


def ubisoft_time(cards):
    return _ubisoft_time(cards)


def _ubisoft_time(cards):
    """(seconds played, last changed unix time) from one game's stat cards, or None. Games name it differently:
    one total ("TimePlayed", "Playtime"), or only parts such as solo, co-op and PvP play time, which are added up."""
    timed = [c for c in cards if c.get("unit") == "Seconds" and "play" in (c.get("statName") or "").lower()]
    total = [c for c in timed if c["statName"].lower() in UBI_TOTALS or c.get("displayName") == "Time Played"]
    use = total[:1] or [c for c in timed if c["statName"].lower().endswith("playtime")]
    try:
        seconds = sum(float(c.get("value") or 0) for c in use)
    except ValueError:
        return None
    if not use or seconds <= 0:
        return None
    when = None
    for c in use:
        try:
            t = int(datetime.datetime.fromisoformat(c["lastModified"].replace("Z", "+00:00")).timestamp())
            when = max(when or 0, t)
        except (KeyError, ValueError, AttributeError):
            pass
    return seconds, when


def ubisoft_playtime():
    """{Ubisoft product id: (minutes played, last played unix time)} from Ubisoft Connect's stats cache.

    Ubisoft Connect saves a game's stats when you look at them in the launcher, so not every game is
    there, and the numbers are as fresh as that last look. Only cache/game_stats is read (never ownership).
    """
    base = _ubisoft_dir()
    if not base:
        return {}
    spaces = None
    out = {}
    for path in glob.glob(os.path.join(base, "cache", "game_stats", "*")):
        with open(path, "rb") as f:
            data = f.read()
        for _, wire, value in _protobuf(data):
            if wire != 2:
                continue
            rec = {field: v for field, w, v in _protobuf(value) if w == 2}
            try:
                space = rec[1].decode()
                got = _ubisoft_time(json.loads(rec[2])["Statscards"])
            except (KeyError, ValueError, TypeError):
                continue
            if not got:
                continue
            spaces = spaces if spaces is not None else _ubisoft_spaces(base)
            for pid in spaces.get(space, ()):
                out[pid] = (round(got[0] / 60), got[1])
    return out


def battlenet_last_played():
    """{Battle.net game uid: last played unix time} from the Battle.net app's settings. It keeps no play time."""
    path = os.path.join(os.environ.get("APPDATA", ""), "Battle.net", "Battle.net.config")
    try:
        with open(path, encoding="utf-8") as f:
            games = json.load(f).get("Games") or {}
    except (OSError, ValueError):
        return {}
    out = {}
    for uid, v in games.items():
        try:
            when = int((v or {}).get("LastPlayed") or 0)
        except (ValueError, TypeError, AttributeError):
            continue
        if when > 0:
            out[uid.lower()] = when
    return out


def gog_playtime():
    """{GOG product id: (minutes played, last played unix time)} from GOG Galaxy's own tracking."""
    with _galaxy() as db:
        if db is None:
            return {}
        minutes = dict(db.execute("select releaseKey, sum(minutesInGame) from GameTimes group by releaseKey").fetchall())
        last = dict(db.execute("select gameReleaseKey, max(lastPlayedDate) from LastPlayedDates group by gameReleaseKey").fetchall())
    return {key[4:]: (int(minutes.get(key) or 0), _galaxy_time(last.get(key)))
            for key in set(minutes) | set(last) if key.startswith("gog_")}


def _galaxy_time(text):
    """Galaxy's "2021-02-04 20:05:47" (UTC) as a unix time, or None."""
    try:
        return int(datetime.datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


# Galaxy keeps the names and descriptions in several languages: English where it has it, like Steam's.
_GOG_ACHIEVEMENTS = """
    select a.gameReleaseKey, coalesce(en.name, other.name, a.apikey), coalesce(en.description, other.description, ''),
           case when a.raritySlug is null then null else a.rarity end, a.imageUnlockedUrl, a.imageLockedUrl,
           (select max(u.unlockTime) from UserAchievements u
            where u.gameReleaseKey = a.gameReleaseKey and u.apikey = a.apikey and u.isUnlocked = 1)
    from Achievements a
    left join LocalizedAchievements en on en.gameReleaseKey = a.gameReleaseKey and en.apikey = a.apikey
         and en.languageId = (select id from Languages where code = 'en-US')
    left join LocalizedAchievements other on other.rowid = (
         select min(o.rowid) from LocalizedAchievements o where o.gameReleaseKey = a.gameReleaseKey and o.apikey = a.apikey)
    where a.gameReleaseKey {} order by a.rowid"""


def gog_achievements(product=None):
    """{GOG product id: [(name, description, % of players or None, icon, grey icon, unlock time or 0)]} from Galaxy's
    copy of your achievements, as fresh as the last time Galaxy ran. `product`: only that game."""
    with _galaxy() as db:
        if db is None:
            return {}
        if product is None:
            rows = db.execute(_GOG_ACHIEVEMENTS.format("like 'gog\\_%' escape '\\'")).fetchall()
        else:
            rows = db.execute(_GOG_ACHIEVEMENTS.format("= ?"), ("gog_" + str(product),)).fetchall()
    out = {}
    for key, name, desc, pct, icon, gray, unlocked in rows:
        out.setdefault(key[4:], []).append((name, desc, pct, icon, gray, (_galaxy_time(unlocked) or 1) if unlocked else 0))
    return out
