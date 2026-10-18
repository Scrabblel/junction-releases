r"""Starting Junction with Windows: a value under HKEY_CURRENT_USER\...\Run, for this Windows user only.

It starts Junction in the tray (--tray), so no window pops up at sign-in.
"""
import os
import sys
import winreg

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Junction"


def command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Junction.pyw")
    return f'"{pythonw}" "{script}" --tray'


def enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN) as k:
            winreg.QueryValueEx(k, NAME)
            return True
    except OSError:
        return False


def set_enabled(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass
