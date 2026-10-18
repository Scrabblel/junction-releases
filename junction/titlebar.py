"""Junction draws its own title bar, like Steam does.

Windows' caption is taken away, but the window keeps everything else that makes it a real window:
the resize edges, the shadow and rounded corners, minimise/maximise animations, and Snap.
The page's top bar is where you drag it (CSS `app-region: drag`, which WebView2 turns into a
caption), and the page draws its own minimise, maximise and close buttons.

    titlebar.prepare()        # before webview.start(): lets the page mark its drag areas
    titlebar.attach(window)   # once the window exists: takes the caption away
"""
import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger("junction")

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
HTCLIENT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 1, 12, 13, 14
SM_CXFRAME, SM_CYFRAME, SM_CXPADDEDBORDER = 32, 33, 92
SWP_FRAMECHANGED = 0x0020
SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0004, 0x0010

LRESULT = ctypes.c_ssize_t
SUBCLASSPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                  ctypes.c_size_t, ctypes.c_size_t)
_user = ctypes.WinDLL("user32", use_last_error=True)
_comctl = ctypes.WinDLL("comctl32")
_comctl.SetWindowSubclass.argtypes = [wintypes.HWND, SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t]
_comctl.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_comctl.DefSubclassProc.restype = LRESULT
_user.IsZoomed.argtypes = [wintypes.HWND]
_user.GetDpiForWindow.argtypes = [wintypes.HWND]
_user.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
_user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]

_procs = {}  # keep the callbacks alive as long as their windows


def prepare():
    """Turn on WebView2's support for `app-region: drag` before the page first loads."""
    try:
        from webview.platforms import edgechromium
    except Exception:  # noqa: BLE001 - not on Windows, or no WebView2: Windows' own title bar stays
        log.exception("Custom title bar not available")
        return False
    ready = edgechromium.EdgeChrome.on_webview_ready
    if getattr(ready, "_junction", False):
        return True

    def on_webview_ready(self, sender, args):
        if args.IsSuccess:
            try:
                sender.CoreWebView2.Settings.IsNonClientRegionSupportEnabled = True
            except Exception:  # noqa: BLE001 - an old WebView2: the window can still be moved with Alt+Space
                log.exception("WebView2 can't use the page's drag areas")
        return ready(self, sender, args)

    on_webview_ready._junction = True
    edgechromium.EdgeChrome.on_webview_ready = on_webview_ready
    return True


def _frame(hwnd):
    dpi = _user.GetDpiForWindow(hwnd) or 96
    pad = _user.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi)
    return _user.GetSystemMetricsForDpi(SM_CYFRAME, dpi) + pad


def _proc(hwnd, msg, wparam, lparam, _id, _data):
    if msg == WM_NCCALCSIZE and wparam:
        # Let Windows size the frame as usual, then give the caption back to the page.
        # Maximised, the window hangs over the screen edge by one frame, so keep that much.
        rect = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents
        top = rect.top
        _comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
        rect.top = top + (_frame(hwnd) if _user.IsZoomed(hwnd) else 0)
        return 0
    if msg == WM_NCHITTEST:
        hit = _comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
        if hit != HTCLIENT or _user.IsZoomed(hwnd):
            return hit
        # The top edge still resizes, like the other three.
        y = ctypes.c_short(lparam >> 16 & 0xFFFF).value
        x = ctypes.c_short(lparam & 0xFFFF).value
        r = wintypes.RECT()
        _user.GetWindowRect(hwnd, ctypes.byref(r))
        edge = _frame(hwnd)
        if y < r.top + edge:
            return HTTOPLEFT if x < r.left + 2 * edge else HTTOPRIGHT if x >= r.right - 2 * edge else HTTOP
        return hit
    return _comctl.DefSubclassProc(hwnd, msg, wparam, lparam)


def attach(window):
    """Take Windows' caption off this pywebview window. Returns False if it couldn't."""
    done = []

    def on_ui_thread():  # a window can only be subclassed from its own thread
        hwnd = int(str(form.Handle.ToInt64()))
        proc = SUBCLASSPROC(_proc)
        if _comctl.SetWindowSubclass(hwnd, proc, 1, 0):
            _procs[hwnd] = proc
            _user.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            done.append(True)

    try:
        from System import Action
        form = window.native
        form.Invoke(Action(on_ui_thread))
    except Exception:  # noqa: BLE001 - Windows' own title bar stays
        log.exception("Couldn't take the title bar off")
    return bool(done)


def is_maximized(window):
    try:
        return bool(_user.IsZoomed(int(str(window.native.Handle.ToInt64()))))
    except Exception:  # noqa: BLE001
        return False
