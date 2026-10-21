"""Launcher sign-ins (Epic, EA, Ubisoft) that Junction keeps so it can read play time from them.

A sign-in is as good as the person's password for that store, so it's never in settings.json: it's
kept in signins.dat next to it, encrypted with Windows' own per-user protection (DPAPI). Only the
same Windows user on the same PC can read it back. The page never gets a token, only a name.
"""
import ctypes
import json
import os
import threading
from ctypes import wintypes

from . import config

FILE = os.path.join(config.SETTINGS_DIR, "signins.dat")
_ENTROPY = b"junction-signins"
_lock = threading.RLock()


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _crypt(name, data):
    """CryptProtectData or CryptUnprotectData: both take (in, description, entropy, reserved, prompt, flags, out)."""
    src, _keep = _blob(data)
    ent, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    if not getattr(ctypes.windll.crypt32, name)(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1,  # 1 = no UI
                                               ctypes.byref(out)):
        raise OSError("Windows couldn't protect or read the sign-in data")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(data: bytes) -> bytes:
    return _crypt("CryptProtectData", data)


def unprotect(data: bytes) -> bytes:
    return _crypt("CryptUnprotectData", data)


def load():
    """{store: {...}} for every saved sign-in; {} when there are none or the file can't be read."""
    with _lock:
        try:
            with open(FILE, "rb") as f:
                data = json.loads(unprotect(f.read()))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def get(store):
    return load().get(store)


def put(store, data):
    """Save (or with data=None, forget) one store's sign-in."""
    with _lock:
        all_ = load()
        if data is None:
            all_.pop(store, None)
        else:
            all_[store] = data
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        if not all_:
            try:
                os.remove(FILE)
            except OSError:
                pass
            return
        with open(FILE + ".tmp", "wb") as f:
            f.write(protect(json.dumps(all_).encode()))
        config.replace_file(FILE + ".tmp", FILE)


def put_if_same(store, refresh, data):
    """Save `data` only if the saved sign-in still has this refresh token. A refresh that was under way while you
    signed out (or signed in again) must not bring the old sign-in back."""
    with _lock:
        if (load().get(store) or {}).get("refresh") != refresh:
            return False
        put(store, data)
        return True


def overview():
    """What the page may know: which stores are signed in, as whom, and whether the sign-in has run out."""
    return {k: {"name": v.get("name") or k.title(), "expired": bool(v.get("expired"))} for k, v in load().items()}
