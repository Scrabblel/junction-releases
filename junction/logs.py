"""Junction's log file, and the problem report a friend can send when something goes wrong.

The log is %LOCALAPPDATA%\\Junction\\logs\\junction.log (two files of half a megabyte at most).
Everything the app prints, including errors, ends up there; a double-clicked app has no console.
The report never contains API keys: saved keys and anything that looks like one are blanked out.
"""
import io
import logging
import logging.handlers
import os
import platform
import re
import sys
import time

from . import config, keys

LOG_DIR = os.path.join(os.path.dirname(config.CACHE_DIR), "logs")
LOG_FILE = os.path.join(LOG_DIR, "junction.log")
REPORT_FILE = os.path.join(LOG_DIR, "Junction problem report.txt")
REPORT_LINES = 200

log = logging.getLogger("junction")


class _ToLog(io.TextIOBase):
    """print() and tracebacks go to the log (and to the console too, when there is one)."""

    def __init__(self, level, console):
        self.level, self.console, self.buf = level, console, ""

    def write(self, s):
        if self.console:
            try:
                self.console.write(s)
            except Exception:  # noqa: BLE001
                pass
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                log.log(self.level, line.rstrip())
        return len(s)

    def flush(self):
        if self.console:
            try:
                self.console.flush()
            except Exception:  # noqa: BLE001
                pass


def setup():
    os.makedirs(LOG_DIR, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    sys.stdout = _ToLog(logging.INFO, sys.__stdout__)
    sys.stderr = _ToLog(logging.ERROR, sys.__stderr__)
    log.info("Junction %s started (%s)", config.VERSION, "installed" if getattr(sys, "frozen", False) else "from source")


# ---------- the problem report ----------

_KEYLIKE = [
    re.compile(r"\b[0-9A-Fa-f]{32}\b"),                                    # Steam Web API keys
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),  # IsThereAnyDeal keys
    re.compile(r"\b[A-Z0-9]{4,5}(?:-[A-Z0-9]{4,5}){2,4}\b"),               # product keys of any kind
    re.compile(r"(?i)\b(set-cookie|cookie)(:\s*)[^\r\n]+"),                   # a whole Cookie header
    re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*"),                          # JWTs (sign-in tokens)
    # "Bearer <token>", "Authorization: Basic <x>": the word and the value after it
    re.compile(r"(?i)\b(bearer|basic)(\s+)[^\s\"'&,]+"),
    re.compile(r"(?i)\b(ubi_v1|rm_v1)(\s+t=)[^\s\"'&,]+"),                  # Ubisoft's tickets
    # key=..., token: ..., ?code=..., "authorizationCode": "...", cookie=..., password=... (keep this one last)
    re.compile(r"(?i)(key|token|ticket|authorization|code|cookie|password|passwd|secret|session|verifier)([\"'=: ]+)[^\s\"'&,]+"),
]


def redact(text, settings=None):
    settings = settings if settings is not None else config.load_settings()
    secrets = [a.get("apiKey") for a in settings.get("steamAccounts") or []]
    secrets += [settings.get("itadApiKey"), settings.get("steamGridDbKey"), keys.builtin_prices_key()]
    for s in filter(None, secrets):
        text = text.replace(s, "[key]")
    for rx in _KEYLIKE:
        text = rx.sub(r"\1\2[hidden]" if rx.groups >= 2 else "[hidden]", text)
    home = os.path.expanduser("~")
    if len(home) > 3:
        # Also as error messages print it: C:\\Users\\name (doubled) or C:/Users/name. The Windows user name is
        # often someone's real name.
        parts = [re.escape(p) for p in re.split(r"[\\/]+", home) if p]
        text = re.sub(r"[\\/]{1,2}".join(parts), "%USERPROFILE%", text, flags=re.I)
        name = os.path.basename(home.rstrip("\\/"))
        if len(name) >= 3:
            text = re.sub(r"(?i)\b" + re.escape(name) + r"\b", "[you]", text)
    return text


def _tail(path, n):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def report(lib=None, extra=None):
    """A plain-text summary of this PC's Junction, for sending to whoever helps you."""
    settings = config.load_settings()
    try:
        from importlib.metadata import version
        wv = version("pywebview")
    except Exception:  # noqa: BLE001
        wv = "?"
    lines = [
        "Junction problem report",
        f"Made: {time.strftime('%Y-%m-%d %H:%M')}",
        f"Junction {config.VERSION} ({'installed' if getattr(sys, 'frozen', False) else 'from source'})",
        f"Windows: {platform.platform()} · Python {platform.python_version()} · pywebview {wv}",
    ]
    accs = settings.get("steamAccounts") or []
    lines.append(f"Keys: Steam {sum(1 for a in accs if a.get('apiKey'))} of {len(accs)} accounts · "
                 f"prices {'own key' if settings.get('itadApiKey') else 'built in' if keys.builtin_prices_key() else 'no'} · cover art {'yes' if settings.get('steamGridDbKey') else 'no'}")
    lines.append(f"Prices for: {config.country(settings)} (Windows region {config.windows_region() or 'unknown'})")
    for k, v in (extra or {}).items():
        lines.append(f"{k}: {v}")
    if lib:
        names = lib.get("launchers") or {}
        per = {}
        for g in lib.get("games") or []:
            c = per.setdefault(g["launcher"], [0, 0])
            c[0] += 1
            c[1] += bool(g.get("installed"))
        lines.append("Games:" + ("" if per else " none found"))
        lines += [f"  {names.get(k, k)}: {n} ({i} installed)" for k, (n, i) in sorted(per.items(), key=lambda x: -x[1][0])]
        if lib.get("errors"):
            lines.append("Launcher problems:")
            lines += [f"  {names.get(k, k)}: {v}" for k, v in lib["errors"].items()]
        lines.append(f"Last scan: {time.strftime('%Y-%m-%d %H:%M', time.localtime(lib.get('scannedAt') or 0))}")
    log_lines = _tail(LOG_FILE + ".1", REPORT_LINES) + _tail(LOG_FILE, REPORT_LINES)
    lines += ["", f"--- The log's last {min(len(log_lines), REPORT_LINES)} lines ---"]
    lines += [x.rstrip("\n") for x in log_lines[-REPORT_LINES:]]
    return redact("\n".join(lines), settings)


def copy_to_clipboard(text):
    """Put text on the Windows clipboard."""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    data = text.encode("utf-16-le") + b"\0\0"
    for _ in range(10):  # another program may have the clipboard open for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        return bool(user32.SetClipboardData(13, h))  # CF_UNICODETEXT
    finally:
        user32.CloseClipboard()
