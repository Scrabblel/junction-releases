"""Signing in to Epic, so Junction can show the play time the Epic Games Launcher counts.

The sign-in is Epic's own page in a small Junction window. When it's done, Epic's page shows a one-time
code; Junction swaps it for a refresh token and keeps that (encrypted, see signins.py), never the
password. This is the same way Legendary, Heroic and Playnite sign in: as the Epic Games Launcher, whose
public client id they all use. Epic doesn't offer this as an official API, so it can stop working.
"""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import signins

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
        try:
            msg = json.load(e).get("errorMessage")
        except ValueError:
            msg = None
        raise SignInError(msg or f"Epic said no (HTTP {e.code}).") from None


def _keep(tok):
    signins.put("epic", {"refresh": tok["refresh_token"], "account": tok["account_id"],
                         "name": tok.get("displayName") or "Epic account", "refreshExpires": tok.get("refresh_expires_at")})


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
        signins.put("epic", dict(saved, expired=True))
        raise
    tok.setdefault("displayName", saved.get("name"))
    _keep(tok)
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
