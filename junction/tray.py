"""Junction's icon in the notification area (the tray, next to the clock).

Left-click opens Junction. Right-click lists the games you played last, to start one straight
away, and Quit. When "keep running in the tray" is on, closing the window only hides it.

Uses Windows Forms through pythonnet, which pywebview already brings along.
"""
import threading

import clr  # noqa: F401 - pythonnet

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
import System.Windows.Forms as WinForms  # noqa: E402
from System import Action  # noqa: E402
from System.Drawing import Font, FontStyle, Icon  # noqa: E402
from System.Threading import ApartmentState, Thread, ThreadStart  # noqa: E402


def _later(fn, *args):
    """Run app code off the tray's own thread, so the menu never waits on it."""
    threading.Thread(target=fn, args=args, daemon=True).start()


class Tray:
    def __init__(self, icon_path, on_open, on_play, on_quit):
        self.icon_path, self.on_open, self.on_play, self.on_quit = icon_path, on_open, on_play, on_quit
        self.recent = []  # [(game id, name)], newest first
        self._ready = threading.Event()
        self._icon = self._ctl = None

    def start(self):
        t = Thread(ThreadStart(self._run))
        t.SetApartmentState(ApartmentState.STA)
        t.IsBackground = True
        t.Start()
        self._ready.wait(5)

    def _run(self):
        self._ctl = WinForms.Control()
        self._ctl.CreateControl()  # gives the thread a handle to post work to
        icon = self._icon = WinForms.NotifyIcon()
        icon.Icon = Icon(self.icon_path)
        icon.Text = "Junction"
        menu = WinForms.ContextMenuStrip()
        menu.Opening += self._fill
        icon.ContextMenuStrip = menu
        icon.MouseClick += self._click
        icon.BalloonTipClicked += lambda s, e: _later(self.on_open)
        icon.Visible = True
        self._ready.set()
        WinForms.Application.Run()

    def _click(self, sender, e):
        if e.Button == WinForms.MouseButtons.Left:
            _later(self.on_open)

    def _item(self, text, fn, bold=False):
        it = WinForms.ToolStripMenuItem(text)
        it.Click += lambda s, e: _later(fn)
        if bold:
            it.Font = Font(it.Font, FontStyle.Bold)
        return it

    def _fill(self, sender, e):
        items = sender.Items
        items.Clear()
        items.Add(self._item("Open Junction", self.on_open, bold=True))
        if self.recent:
            items.Add(WinForms.ToolStripSeparator())
            head = WinForms.ToolStripMenuItem("Play")
            head.Enabled = False
            items.Add(head)
            for gid, name in self.recent:
                label = name if len(name) <= 40 else name[:39] + "…"
                items.Add(self._item("    " + label.replace("&", "&&"), lambda gid=gid: self.on_play(gid)))
        items.Add(WinForms.ToolStripSeparator())
        items.Add(self._item("Quit Junction", self.on_quit))
        e.Cancel = False

    def _on_thread(self, fn):
        if self._ctl is not None and self._ctl.IsHandleCreated:
            try:
                self._ctl.BeginInvoke(Action(fn))
            except Exception:  # noqa: BLE001 - the tray is already gone
                pass

    def tip(self, title, text):
        self._on_thread(lambda: self._icon.ShowBalloonTip(5000, title, text, WinForms.ToolTipIcon.Info))

    def stop(self):
        def _stop():
            self._icon.Visible = False
            self._icon.Dispose()
            WinForms.Application.ExitThread()
        done = threading.Event()
        self._on_thread(lambda: (_stop(), done.set()))
        done.wait(2)
