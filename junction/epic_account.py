"""Signing in to Epic, so Junction can show the play time the Epic Games Launcher counts, and your achievements.

The sign-in is Epic's own page in a small Junction window. When it's done, Epic's page shows a one-time
code; Junction swaps it for a refresh token and keeps that (encrypted, see signins.py), never the
password. This is the same way Legendary, Heroic and Playnite sign in: as the Epic Games Launcher, whose
public client id they all use. Epic doesn't offer this as an official API, so it can stop working.
"""
import base64
import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import config, signins

CLIENT = ("34a02cf8f4414e29b15921876da36f9a", "daafbccc737745039dffe53d94fc76cf")  # the Epic Games Launcher's
TOKEN_URL = "https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/token"
PLAYTIME_URL = "https://library-service.live.use1a.on.epicgames.com/library/api/public/playtime/account/{}/all"
CODE_URL = f"https://www.epicgames.com/id/api/redirect?clientId={CLIENT[0]}&responseType=code"
LOGIN_URL = "https://www.epicgames.com/id/login?redirectUrl=" + urllib.parse.quote(CODE_URL, safe="")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EpicGamesLauncher"
SIGN_IN_WAIT = 15 * 60   # seconds the sign-in window stays open


class SignInError(Exception):
    pass


def _post_token(fields):
    basic = base64.b64encode(f"{CLIENT[0]}:{CLIENT[1]}".encode()).decode()
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode({**fields, "token_type": "eg1"}).encode(),
                                 headers={"Authorization": f"basic {basic}", "User-Agent": UA,
                                          "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # Only 400/401 means Epic refused the sign-in. Anything else (a rate limit, a proxy, a timeout, a bad
        # moment on their side) is "try again later" and must never make someone sign in again.
        if e.code not in (400, 401):
            raise
        try:
            msg = json.load(e).get("errorMessage")
        except ValueError:
            msg = None
        raise SignInError(msg or f"Epic said no (HTTP {e.code}).") from None


def _signin_of(tok):
    return {"refresh": tok["refresh_token"], "account": tok["account_id"],
            "name": tok.get("displayName") or "Epic account", "refreshExpires": tok.get("refresh_expires_at")}


def _keep(tok):
    signins.put("epic", _signin_of(tok))


def code_from_page(text):
    """The one-time code from the JSON Epic's redirect page shows, or None (not signed in yet)."""
    try:
        return json.loads(text).get("authorizationCode") or None
    except (ValueError, AttributeError):
        return None


def finish_sign_in(code):
    """Swap the one-time code for a refresh token and keep it. Returns the account's display name."""
    tok = _post_token({"grant_type": "authorization_code", "code": code})
    _keep(tok)
    return tok.get("displayName") or "Epic account"


def sign_in(open_window):
    """Show Epic's sign-in page in a Junction window and wait for the person to sign in there.
    `open_window(title, url)` returns a pywebview window. Returns the display name; raises SignInError."""
    win = open_window("Sign in to Epic Games", LOGIN_URL)
    closed = []
    win.events.closed += lambda: closed.append(1)
    t0 = reloaded = time.time()
    try:
        while not closed and time.time() - t0 < SIGN_IN_WAIT:
            time.sleep(0.5)
            try:
                url = win.get_current_url() or ""
            except Exception:  # noqa: BLE001 - the window is still loading or just closed
                continue
            if url.startswith("https://www.epicgames.com/id/api/redirect"):
                code = code_from_page(win.evaluate_js("document.body.innerText") or "")
                if code:
                    return finish_sign_in(code)
                if time.time() - reloaded > 5:  # the code page showed before the sign-in finished: show the sign-in again
                    reloaded = time.time()
                    win.load_url(LOGIN_URL)
        raise SignInError("The sign-in window was closed before you signed in.")
    finally:
        if not closed:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass


def _access():
    """(access token, account id), refreshing the saved sign-in. Epic hands out a new refresh token each time,
    so it's saved again. Raises SignInError when the sign-in has run out; it's then marked so the page can say so."""
    saved = signins.get("epic")
    if not saved or saved.get("expired"):
        raise SignInError("Not signed in to Epic.")
    try:
        tok = _post_token({"grant_type": "refresh_token", "refresh_token": saved["refresh"]})
    except SignInError:
        signins.put_if_same("epic", saved["refresh"], dict(saved, expired=True))
        raise
    tok.setdefault("displayName", saved.get("name"))
    # Only if you didn't sign out (or in again) while this was under way.
    signins.put_if_same("epic", saved["refresh"], _signin_of(tok))
    return tok["access_token"], tok["account_id"]


def playtime():
    """{Epic app name: (minutes played, None)} as the Epic Games Launcher counts it; {} when not signed in."""
    if not signins.get("epic"):
        return {}
    token, account = _access()
    req = urllib.request.Request(PLAYTIME_URL.format(account), headers={"Authorization": f"bearer {token}", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        items = json.load(r)
    out = {}
    for it in items if isinstance(items, list) else []:
        seconds = it.get("totalTime") or 0
        if it.get("artifactId") and seconds > 0:
            out[it["artifactId"]] = (round(seconds / 60), None)
    return out


def sign_out():
    signins.put("epic", None)


# ---------- achievements ----------
# The same requests as the Epic store's own achievement pages (and Playnite's SuccessStory). A game's list is public;
# what you unlocked needs the sign-in.

GRAPHQL_URL = "https://launcher.store.epicgames.com/graphql"
ACHIEVEMENTS_QUERY = """query Achievement($sandboxId: String!, $locale: String!) { Achievement {
  productAchievementsRecordBySandbox(sandboxId: $sandboxId, locale: $locale) { achievements { achievement {
    name hidden unlockedDisplayName unlockedDescription unlockedIconLink lockedIconLink rarity { percent } } } } } }"""
PLAYER_QUERY = """query PlayerAchievement($epicAccountId: String!, $sandboxId: String!) { PlayerAchievement {
  playerAchievementGameRecordsBySandbox(epicAccountId: $epicAccountId, sandboxId: $sandboxId) { records {
    playerAchievements { playerAchievement { achievementName unlocked unlockDate } } } } } }"""
TOTALS_TTL = 7 * 86400  # seconds a game's number of achievements is remembered


def _graphql(query, variables, token=None):
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if token:
        headers["Authorization"] = f"bearer {token}"
    req = urllib.request.Request(GRAPHQL_URL, data=json.dumps({"query": query, "variables": variables}).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("data") or {}


def _schema(namespace):
    """Every achievement of the game in this catalogue namespace; [] when it has none."""
    rec = (_graphql(ACHIEVEMENTS_QUERY, {"sandboxId": namespace, "locale": "en-US"}).get("Achievement") or {}
           ).get("productAchievementsRecordBySandbox") or {}
    return [a["achievement"] for a in rec.get("achievements") or [] if (a.get("achievement") or {}).get("name")]


def _unlocked(token, account, namespace):
    """{achievement name: unlock unix time} for what you unlocked in this game."""
    def ask():
        return ((_graphql(PLAYER_QUERY, {"epicAccountId": account, "sandboxId": namespace}, token).get("PlayerAchievement") or {}
                 ).get("playerAchievementGameRecordsBySandbox") or {}).get("records")
    records = ask()
    if records is None:  # sometimes Epic has nothing the first time it's asked about a game, and does a moment later
        time.sleep(1)
        records = ask()
    out = {}
    for rec in records or []:
        for item in rec.get("playerAchievements") or []:
            a = item.get("playerAchievement") or {}
            if a.get("unlocked") and a.get("achievementName"):
                try:
                    out[a["achievementName"]] = int(datetime.datetime.fromisoformat(a.get("unlockDate") or "").timestamp())
                except ValueError:
                    out[a["achievementName"]] = 1  # unlocked, date unknown
    return out


def achievement_counts(namespaces):
    """{namespace: [unlocked, total], or None when Epic didn't answer for that game} for these games; games without
    achievements are left out. {} when not signed in."""
    if not signins.get("epic"):
        return {}
    token, account = _access()
    totals = config.read_cache("epic_achievements.json", {})  # {namespace: [number of achievements, when asked]}

    def one(ns):
        try:
            if time.time() - (totals.get(ns) or [0, 0])[1] > TOTALS_TTL:
                totals[ns] = [len(_schema(ns)), time.time()]
            if not totals[ns][0]:
                return ns, False
            return ns, [len(_unlocked(token, account, ns)), totals[ns][0]]
        except (*config.NET_ERRORS, KeyError, TypeError):
            return ns, None
    with ThreadPoolExecutor(4) as pool:
        out = {ns: v for ns, v in pool.map(one, namespaces) if v is not False}
    config.write_cache("epic_achievements.json", totals)
    return out


def achievement_list(namespace):
    """One game's achievements in the page's format, under "epic" as whose they are; None without any."""
    items = _schema(namespace)
    if not items:
        return None
    got = _unlocked(*_access(), namespace) if signins.get("epic") else {}
    return {
        # [name, description, hidden, % of players, sheet, slot, icon, grey icon]
        "list": [[a.get("unlockedDisplayName") or a["name"], a.get("unlockedDescription") or "", int(bool(a.get("hidden"))),
                  (a.get("rarity") or {}).get("percent"), -1, -1, a.get("unlockedIconLink"), a.get("lockedIconLink")]
                 for a in items],
        "who": {"epic": [got.get(a["name"], 0) for a in items]} if signins.get("epic") else {},
    }
