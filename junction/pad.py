"""Controller input for the desktop app, read straight from Windows (XInput).

The page inside the app window can't always see controllers itself, so this reads them and
hands the page simple button names: up, down, left, right, a, b, x, y, lb, rb, lt, rt, and menu (the ☰ Start button).
Buttons only reach Junction while its window is in front, so a game you're playing never
moves Junction around in the background. Holding a direction repeats it, like a console menu.

Xbox controllers (and anything Steam Input or DS4Windows presents as one) work this way.
"""
import ctypes
import os
import time
from ctypes import wintypes

BUTTONS = {0x0001: "up", 0x0002: "down", 0x0004: "left", 0x0008: "right",
           0x0010: "menu", 0x0100: "lb", 0x0200: "rb", 0x1000: "a", 0x2000: "b", 0x4000: "x", 0x8000: "y"}
STICK = 0.6 * 32767      # how far the left stick must move to count as a direction
TRIGGER = 128            # of 255
REPEAT_AFTER, REPEAT_EVERY = 0.40, 0.11  # seconds, for held directions
DIRECTIONS = {"up", "down", "left", "right"}


class _Gamepad(ctypes.Structure):
    _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class _State(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _Gamepad)]


def _xinput():
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            return ctypes.WinDLL(name)
        except OSError:
            continue
    return None


def _in_front():
    """Is one of this program's windows the one in front?"""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def _held(g):
    keys = {name for bit, name in BUTTONS.items() if g.wButtons & bit}
    if g.sThumbLY > STICK:
        keys.add("up")
    if g.sThumbLY < -STICK:
        keys.add("down")
    if g.sThumbLX < -STICK:
        keys.add("left")
    if g.sThumbLX > STICK:
        keys.add("right")
    if g.bLeftTrigger > TRIGGER:
        keys.add("lt")
    if g.bRightTrigger > TRIGGER:
        keys.add("rt")
    return keys


def run(on_press, on_connected, rate=60):
    """Poll controllers forever: on_press(name) for each press, on_connected(bool) when that changes."""
    xi = _xinput()
    if not xi:
        return
    get = xi.XInputGetState
    get.argtypes = [wintypes.DWORD, ctypes.POINTER(_State)]
    get.restype = wintypes.DWORD
    state = _State()
    connected, last_search = set(), 0.0
    prev, since, last_sent = set(), {}, {}
    was_connected = False
    while True:
        time.sleep(1 / rate)
        now = time.monotonic()
        # Asking an empty slot is slow, so look for new controllers only every two seconds.
        slots = list(connected)
        if now - last_search > 2:
            slots, last_search = range(4), now
        held = set()
        for i in slots:
            if get(i, ctypes.byref(state)) == 0:
                connected.add(i)
                held |= _held(state.Gamepad)
            else:
                connected.discard(i)
        if bool(connected) != was_connected:
            was_connected = bool(connected)
            on_connected(was_connected)
        if not _in_front():
            prev, since = held, {}
            continue
        for k in held:
            if k not in prev:
                since[k], last_sent[k] = now, now
                on_press(k)
            elif k in DIRECTIONS and now - since.get(k, now) > REPEAT_AFTER and now - last_sent.get(k, 0) > REPEAT_EVERY:
                last_sent[k] = now
                on_press(k)
        for k in prev - held:
            since.pop(k, None)
        prev = held
