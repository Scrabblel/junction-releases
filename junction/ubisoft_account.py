"""Signing in to Ubisoft, so Junction can show the play time Ubisoft counts, up to date.

Ubisoft Connect keeps a copy of each game's stats on the PC, but it only refreshes that copy when you open the
game's stats in Ubisoft Connect, so it can be a year old. Signed in, Junction asks Ubisoft for the same
"Time Played" directly.

The sign-in is Ubisoft's own page in a small Junction window, the one Ubisoft Connect's website uses. When it's
done, the page keeps a "remember me" ticket; Junction takes that and keeps it (encrypted, see signins.py), never
the password, and swaps it for a short-lived ticket when it needs one. This is how the GOG Galaxy Ubisoft plugin
signs in. Ubisoft doesn't offer this as an official API, so it can stop working.
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.request

from . import owned_local, signins

APP_ID = "b8fde481-327d-4031-85ce-7c10a202a700"     # Ubisoft Connect's website, as the GOG Galaxy plugin uses it
GENOME_ID = "fbd6791c-a6c6-4206-a75e-77234080b87b"
LOGIN_URL = (f"https://connect.ubisoft.com/login?appId={APP_ID}&genomeId={GENOME_ID}&lang=en-US"
             "&nextUrl=https:%2F%2Fconnect.ubisoft.com%2Fready")
READY_URL = "https://connect.ubisoft.com/ready"
SESSIONS_URL = "https://public-ubiservices.ubi.com/v3/profiles/sessions"
STATS_URL = "https://public-ubiservices.ubi.com/v1/profiles/{}/statscard?spaceId={}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
SIGN_IN_WAIT = 15 * 60   # seconds the sign-in window stays open
# What Ubisoft's page keeps once you're signed in (read from the sign-in window only).
READ_STORAGE = "JSON.stringify([localStorage.getItem('PRODloginData'), localStorage.getItem('PRODrememberMe')])"
# Ubisoft hands out a new "remember me" ticket each time one is used, and the old one stops working: so one use at a
# time, and the new one is always kept.
_lock = threading.Lock()
log = logging.getLogger("junction")
FORGET_STORAGE = ("['PRODloginData', 'PRODrememberMe', 'PRODlastProfile'].forEach(k => localStorage.removeItem(k));"
                  " sessionStorage.clear(); 1")


class SignInError(Exception):
    pass


def _request(method, url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Ubi-AppId": APP_ID, "User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json",
        "Origin": "https://connect.ubisoft.com", "Referer": "https://connect.ubisoft.com/", **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _session(remember):
    """A fresh ticket from the "remember me" ticket: {ticket, sessionId, userId, rememberMeTicket?, ...}."""
    try:
        return _request("POST", SESSIONS_URL, {"Authorization": f"rm_v1 t={remember}"}, {"rememberMe": True})
    except urllib.error.HTTPError as e:
        # Only 400/401 means Ubisoft refused the sign-in. Anything else (a rate limit, a proxy, a timeout, a bad
        # moment on their side) is "try again later" and must never make someone sign in again.
        if e.code not in (400, 401):
            raise
        raise SignInError(f"Ubisoft said no (HTTP {e.code}).") from None


def login_from_storage(text):
    """{remember, user, name, done} from what Ubisoft's page keeps while you sign in, or None (nothing yet).
    `done`: the sign-in is finished (also a code sent by email, if Ubisoft asked for one) with "Remember me"."""
    try:
        login, remember = [json.loads(p) if p else None for p in json.loads(text or "null") or [None, None]]
    except (ValueError, TypeError):
        return None
    login = login if isinstance(login, dict) else {}
    remember = remember if isinstance(remember, dict) else {}
    if not login.get("ticket") or not login.get("userId"):
        return None
    return {"remember": remember.get("rememberMeTicket") or login.get("rememberMeTicket"), "user": login["userId"],
            "name": login.get("nameOnPlatform") or "Ubisoft account", "done": bool(remember.get("rememberMeTicket"))}


def _keep(remember, user, name):
    signins.put("ubisoft", {"refresh": remember, "user": user, "name": name})


def sign_in(open_window):
    """Show Ubisoft's sign-in page in a Junction window and wait for the person to sign in there.
    `open_window(title, url)` returns a pywebview window. Returns the display name; raises SignInError."""
    win = open_window("Sign in to Ubisoft", LOGIN_URL)
    closed = []
    win.events.closed += lambda: closed.append(1)
    t0 = time.time()
    forgot, last, rereads = False, None, 0

    def finish(login):
        if not login["remember"]:
            raise SignInError("Tick “Remember me” on Ubisoft’s sign-in page, so Junction can stay signed in.")
        with _lock:
            s = _session(login["remember"])  # check it works; it also gives the ticket to keep from now on
            _keep(s.get("rememberMeTicket") or login["remember"], login["user"], login["name"])
        return login["name"]

    try:
        while not closed and time.time() - t0 < SIGN_IN_WAIT:
            time.sleep(0.3)
            try:
                url = win.get_current_url() or ""
            except Exception:  # noqa: BLE001 - the window is still loading or just closed
                continue
            if url.startswith("https://connect.ubisoft.com/") and not url.startswith(READY_URL):
                try:
                    if not forgot:
                        # A sign-in from before (Ubisoft's page remembers one) would be taken as is: so that Ubisoft
                        # asks who you are, it's forgotten once, and the sign-in page is shown fresh.
                        win.evaluate_js(FORGET_STORAGE)
                        forgot = True
                        win.load_url(LOGIN_URL)
                        continue
                    login = login_from_storage(win.evaluate_js(READ_STORAGE))
                except Exception:  # noqa: BLE001 - the page is still loading
                    continue
                last = login or last
                if login and login["done"]:
                    return finish(login)
            elif forgot and url.startswith(READY_URL):
                # Signed in, Ubisoft sends the window to a page that doesn't exist (any more), where its saved
                # sign-in can't be read. Use what was read on the way, or read it on the sign-in page.
                if last:
                    return finish(last)
                if rereads < 3:
                    rereads += 1
                    win.load_url(LOGIN_URL)
        raise SignInError("The sign-in window was closed before you signed in.")
    finally:
        if not closed:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass


def _access():
    """(ticket, session id, user id) from the saved sign-in. Raises SignInError (and marks it) when it has run out."""
    with _lock:
        saved = signins.get("ubisoft")
        if not saved or saved.get("expired"):
            raise SignInError("Not signed in to Ubisoft.")
        try:
            s = _session(saved["refresh"])
        except SignInError:
            signins.put_if_same("ubisoft", saved["refresh"], dict(saved, expired=True))
            raise
        if s.get("rememberMeTicket") and s["rememberMeTicket"] != saved["refresh"]:
            # Only if you didn't sign out (or in again) while this was under way.
            signins.put_if_same("ubisoft", saved["refresh"], dict(saved, refresh=s["rememberMeTicket"]))
    return s["ticket"], s.get("sessionId"), s.get("userId") or saved["user"]


def playtime(product_ids):
    """{Ubisoft product id: (minutes played, last played unix time)} as Ubisoft counts it now, for these games;
    {} when not signed in. A game's editions share one "space", and Ubisoft keeps its stats per space.
    One game that can't be read doesn't lose the others; the log says what happened, for the problem report."""
    if not signins.get("ubisoft"):
        return {}
    wanted = {str(p) for p in product_ids}
    known = owned_local.ubisoft_spaces()
    spaces = {space: [p for p in pids if p in wanted] for space, pids in known.items()}
    spaces = {space: pids for space, pids in spaces.items() if pids}
    if not spaces:
        log.warning("Ubisoft play time: none of the %d Ubisoft games are in Ubisoft Connect's list on this PC "
                    "(%s)", len(wanted), f"{len(known)} games in it" if known else owned_local.ubisoft_where(empty="its game list has no games in it"))
        return {}
    ticket, session, user = _access()
    headers = {"Authorization": f"Ubi_v1 t={ticket}", "Ubi-SessionId": session or "",
               "Ubi-RequestedPlatformType": "uplay", "Ubi-LocaleCode": "en-GB"}
    out, none, failed = {}, 0, {}
    for space, pids in spaces.items():
        try:
            cards = _request("GET", STATS_URL.format(user, space), headers).get("Statscards") or []
        except urllib.error.HTTPError as e:
            if e.code in (404, 412):  # no stats for this game (never played, or it keeps none)
                none += 1
                continue
            failed[f"HTTP {e.code}"] = failed.get(f"HTTP {e.code}", 0) + 1
            if e.code in (401, 403) and not out:
                break  # the ticket isn't accepted: the other games won't be either
            continue
        except (OSError, ValueError, AttributeError) as e:  # a timeout, or an answer that isn't the usual JSON
            failed[type(e).__name__] = failed.get(type(e).__name__, 0) + 1
            continue
        got = owned_local.ubisoft_time(cards)
        if got:
            for pid in pids:
                out[pid] = (round(got[0] / 60), got[1])
        else:
            none += 1
    log.info("Ubisoft play time: %d of %d games%s%s", len(out), len(spaces), f", {none} without play time" if none else "",
             f", failed: {', '.join(f'{k} ×{n}' for k, n in failed.items())}" if failed else "")
    if failed and not out:
        raise OSError(f"Ubisoft didn't give any play time ({', '.join(failed)})")
    return out


def sign_out():
    signins.put("ubisoft", None)
