"""Signing in to EA, so Junction can show the play time the EA app counts.

The sign-in is EA's own page in a small Junction window. EA only lets the EA app sign in this way, so
Junction signs in as the EA app, like the GOG Galaxy EA plugin does: with the EA app's public client id
and the "pc_sign" EA asks for (see pcsign.py). When the sign-in is done, EA sends the window to the EA
app's own page with a one-time code; Junction catches that, swaps the code for a refresh token and keeps
that (encrypted, see signins.py), never the password. EA doesn't offer this as an official API, so it
can stop working.
"""
import base64
import datetime
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from . import pcsign, signins
from .art import normalize

CLIENT = ("JUNO_PC_CLIENT", "4mRLtYMb6vq9qglomWEaT4ChxsXWcyqbQpuBNfMPOYOiDmYYQmjuaBsF2Zp0RyVeWkfqhE9TuGgAw7te")  # the EA app's
AUTH_URL = "https://accounts.ea.com/connect/auth"
TOKEN_URL = "https://accounts.ea.com/connect/token"
API_URL = "https://service-aggregation-layer.juno.ea.com/graphql"
REDIRECT = "qrc:///html/login_successful.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Origin/10.6.0.00000 EAApp/13.680.0.6193 Chrome/109.0.5414.120 Safari/537.36"
SIGN_IN_WAIT = 15 * 60   # seconds the sign-in window stays open
OWNED_QUERY = ("query{me{ownedGameProducts(storefronts:[EA] locale:\"DEFAULT\" paging:{limit:9999,next:null} "
               "productFound:true ownershipMethod:[PURCHASE,REDEMPTION,ENTITLEMENT_GRANT] downloadableOnly:false "
               "entitlementEnabled:true platforms:[PC]){items{product{name gameSlug baseItem(availabilities:[VISIBLE]){title}}}}}}")


class SignInError(Exception):
    pass


def _post_token(fields):
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(
        {"client_id": CLIENT[0], "client_secret": CLIENT[1], **fields}).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code >= 500:  # EA having a bad moment, not a refused sign-in: try again later
            raise
        try:
            body = json.load(e)
            msg = body.get("error_description") or body.get("error")
        except ValueError:
            msg = None
        raise SignInError(msg or f"EA said no (HTTP {e.code}).") from None


def _name_from(token):
    """The EA account's display name, from inside the access token (a JWT)."""
    try:
        part = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["nexus"]
        return data.get("psif", [{}])[0].get("dis") or None
    except (IndexError, KeyError, ValueError, TypeError, AttributeError):
        return None


def _keep(tok, name=None):
    signins.put("ea", {"refresh": tok["refresh_token"], "name": name or _name_from(tok["access_token"]) or "EA account"})


def login_url():
    """(EA's sign-in page, the PKCE verifier that goes with it)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": CLIENT[0], "display": "junoClient/login", "redirect_uri": REDIRECT,
        "locale": "en_US", "pc_sign": pcsign.make(), "code_challenge": challenge, "code_challenge_method": "S256"})
    return f"{AUTH_URL}?{query}", verifier


def code_from_redirect(location):
    """The one-time code from the EA app page EA sends the window to, or None."""
    if not (location or "").startswith("qrc:"):
        return None
    return (urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code") or [None])[0]


def finish_sign_in(code, verifier):
    tok = _post_token({"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                       "redirect_uri": REDIRECT, "token_format": "JWS"})
    if not tok.get("access_token") or not tok.get("refresh_token"):
        raise SignInError("EA didn't finish the sign-in. Try again.")
    name = _name_from(tok["access_token"]) or "EA account"
    _keep(tok, name)
    return name


def sign_in(open_window):
    """Show EA's sign-in page in a Junction window and wait for the person to sign in there.
    `open_window(title, url)` returns a pywebview window. Returns the display name; raises SignInError."""
    url, verifier = login_url()
    win = open_window("Sign in to EA", url)
    closed, codes = [], []

    def seen(response):
        # EA ends the sign-in by sending the window to the EA app's own page (qrc:), which a browser can't open:
        # the code is in that redirect.
        headers = {k.lower(): v for k, v in (getattr(response, "headers", None) or {}).items()}
        code = code_from_redirect(headers.get("location")) or code_from_redirect(getattr(response, "url", ""))
        if code:
            codes.append(code)

    def going(_sender, args):
        # EA's last page sends the window on with a script, which only the browser's own "navigation starting"
        # events see. The EA app's page can't open here anyway, so that navigation is stopped.
        code = code_from_redirect(str(args.Uri))
        if code:
            codes.append(code)
            try:
                args.Cancel = True
            except Exception:  # noqa: BLE001
                pass

    hooked = set()

    def hook():
        """Listen to the WebView2 control behind the window, once it's there (pywebview doesn't pass these on)."""
        try:
            control = win.native.browser.webview
            if "control" not in hooked:
                control.NavigationStarting += going
                hooked.add("control")
            if "frames" not in hooked and control.CoreWebView2 is not None:
                control.CoreWebView2.FrameNavigationStarting += going
                hooked.add("frames")
        except Exception:  # noqa: BLE001 - not created yet; try again on the next round
            pass

    win.events.closed += lambda: closed.append(1)
    win.events.response_received += seen
    win.events.request_sent += seen
    t0 = time.time()
    try:
        while not closed and not codes and time.time() - t0 < SIGN_IN_WAIT:
            time.sleep(0.3)
            if len(hooked) < 2:
                hook()
            try:
                code = code_from_redirect(win.get_current_url())
            except Exception:  # noqa: BLE001 - still loading, or just closed
                code = None
            if code:
                codes.append(code)
        if codes:
            return finish_sign_in(codes[0], verifier)
        raise SignInError("The sign-in window was closed before you signed in.")
    finally:
        if not closed:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass


def _access():
    """An access token from the saved sign-in. Raises SignInError (and marks it) when the sign-in has run out."""
    saved = signins.get("ea")
    if not saved or saved.get("expired"):
        raise SignInError("Not signed in to EA.")
    try:
        tok = _post_token({"grant_type": "refresh_token", "refresh_token": saved["refresh"]})
    except SignInError:
        signins.put("ea", dict(saved, expired=True))
        raise
    if tok.get("refresh_token") and tok["refresh_token"] != saved["refresh"]:
        _keep(tok, saved.get("name"))
    return tok["access_token"]


def _query(token, query):
    req = urllib.request.Request(f"{API_URL}?query={urllib.parse.quote(query)}", headers={
        "Authorization": f"Bearer {token}", "x-client-id": "EAX-JUNO-CLIENT", "referer": "https://pc.ea.com/", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if body.get("errors") and not body.get("data"):
        raise ValueError(f"EA: {body['errors'][0].get('message')}")
    return body.get("data") or {}


def _when(text):
    try:
        return int(datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except (AttributeError, ValueError):
        return None


def playtime():
    """{normalized game name: (minutes played, last played unix time)} as the EA app counts it; {} when not signed in.
    EA's games are matched by name, since Junction knows the installed ones by their uninstall entry."""
    if not signins.get("ea"):
        return {}
    token = _access()
    items = (((_query(token, OWNED_QUERY).get("me") or {}).get("ownedGameProducts") or {}).get("items")) or []
    names = {}
    for it in items:
        p = it.get("product") or {}
        if p.get("gameSlug"):
            names.setdefault(p["gameSlug"], set()).update(
                n for n in (p.get("name"), (p.get("baseItem") or {}).get("title")) if n)
    out = {}
    slugs = sorted(names)
    for i in range(0, len(slugs), 50):
        part = slugs[i:i + 50]
        q = f"query{{me{{recentGames(gameSlugs:{json.dumps(part)}){{items{{gameSlug lastSessionEndDate totalPlayTimeSeconds}}}}}}}}"
        for r in ((_query(token, q).get("me") or {}).get("recentGames") or {}).get("items") or []:
            seconds = int(r.get("totalPlayTimeSeconds") or 0)
            if not seconds:
                continue
            for n in names.get(r.get("gameSlug"), ()):
                out[normalize(n)] = (round(seconds / 60), _when(r.get("lastSessionEndDate")))
    return out


def sign_out():
    signins.put("ea", None)
