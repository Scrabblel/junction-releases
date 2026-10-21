"""Junction's icon in the notification area (the tray, next to the clock).

Left-click opens Junction. Right-click lists the games you played last, to start one straight
away, and Quit. When "keep running in the tray" is on, closing the window only hides it.

The right-click menu is Junction's own (_Menu), in the app's dark colours with rounded corners, because
Windows' standard one is always light. Uses Windows Forms through pythonnet, which pywebview already brings along.
"""
import ctypes
import threading

import clr  # noqa: F401 - pythonnet

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
import System.Windows.Forms as WinForms  # noqa: E402
from System import Action  # noqa: E402
from System.Drawing import Bitmap, Color, Font, FontStyle, Graphics, GraphicsUnit, Icon, Pen, Rectangle, SolidBrush  # noqa: E402
from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode  # noqa: E402
from System.Drawing.Imaging import PixelFormat  # noqa: E402
from System.Threading import ApartmentState, Thread, ThreadStart  # noqa: E402


def _rgb(hex_):
    return Color.FromArgb(int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16))


# The colours of Junction's own menus (web/index.html: --raised, --raised-2, a rule, --ink, --ink-faint).
BACK, HOVER, RULE, INK, FAINT = map(_rgb, ("#1a2029", "#242c37", "#2c3542", "#f3f5f8", "#6e7886"))
# Each launcher's colour line, as in the app.
LINES = {k: _rgb(v) for k, v in {
    "steam": "#4ea3e8", "epic": "#cfd5dd", "gog": "#a874f5", "ubisoft": "#1fc1c4", "ea": "#ff5d55",
    "battlenet": "#6f7dff", "rockstar": "#ff8a3d", "xbox": "#9bd14a", "manual": "#e38fd9"}.items()}


def _later(fn, *args):
    """Run app code off the tray's own thread, so the menu never waits on it."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _rounded(x, y, w, h, r):
    path = GraphicsPath()
    d = max(1, r * 2)
    path.AddArc(x, y, d, d, 180, 90)
    path.AddArc(x + w - d, y, d, d, 270, 90)
    path.AddArc(x + w - d, y + h - d, d, d, 0, 90)
    path.AddArc(x, y + h - d, d, d, 90, 90)
    path.CloseFigure()
    return path


class _Menu:
    """A small borderless window that looks like Junction's own menus: dark, rounded corners (drawn by
    Windows 11 itself), a highlight that follows the mouse, and the arrow keys, Enter and Escape.
    It closes when you click anywhere else.

    Rows: ("item", text, run, {"bold": bool, "line": launcher colour}), ("label", text), ("sep",).
    """

    def __init__(self):
        f = self.form = WinForms.Form()
        f.FormBorderStyle = getattr(WinForms.FormBorderStyle, "None")
        f.ShowInTaskbar = False
        f.TopMost = True
        f.StartPosition = WinForms.FormStartPosition.Manual
        f.KeyPreview = True
        f.BackColor = BACK
        self.pic = WinForms.PictureBox()  # shows a finished picture, so the highlight never flickers
        self.pic.Dock = WinForms.DockStyle.Fill
        f.Controls.Add(self.pic)
        self.pic.MouseMove += lambda s, e: self._hover(self._at(e.Y))
        self.pic.MouseLeave += lambda s, e: self._hover(-1)
        self.pic.MouseUp += self._mouse_up
        f.KeyDown += self._key
        f.Deactivate += lambda s, e: self.hide()
        self.rows, self.boxes, self.hovered, self.scale, self.fonts = [], [], -1, 1.0, None
        # Windows 11 rounds the corners and draws a thin border in our colour; on Windows 10 the menu draws its own.
        hwnd = ctypes.c_void_p(f.Handle.ToInt64())
        dwm = ctypes.windll.dwmapi
        corner = ctypes.c_int(2)  # DWMWA_WINDOW_CORNER_PREFERENCE: DWMWCP_ROUND
        self.rounded = dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4) == 0
        if self.rounded:
            border = ctypes.c_uint(RULE.R | RULE.G << 8 | RULE.B << 16)  # DWMWA_BORDER_COLOR, 0x00BBGGRR
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), 4)

    def _make_fonts(self):
        """Regular, bold and label fonts at this screen's size: made once per opening, not on every redraw."""
        for f in self.fonts or ():
            f.Dispose()
        px = lambda n: float(n * self.scale)  # noqa: E731
        self.fonts = (Font("Segoe UI", px(14), FontStyle.Regular, GraphicsUnit.Pixel),
                      Font("Segoe UI Semibold", px(14), FontStyle.Regular, GraphicsUnit.Pixel),
                      Font("Segoe UI Semibold", px(11), FontStyle.Regular, GraphicsUnit.Pixel))

    def _px(self, n):
        return int(round(n * self.scale))

    def show(self, rows, at):
        self.rows = rows
        self.scale = max(1.0, self.form.DeviceDpi / 96)
        self._make_fonts()
        regular, bold, _ = self.fonts
        pad, item_h, label_h, sep_h = self._px(6), self._px(34), self._px(26), self._px(9)
        widest = max([WinForms.TextRenderer.MeasureText(r[1], bold if r[3].get("bold") else regular).Width
                      for r in rows if r[0] == "item"] + [0])
        w = min(self._px(340), max(self._px(224), self._px(28) + widest + self._px(28)))
        y, self.boxes = pad, []
        for r in rows:
            h = {"item": item_h, "label": label_h}.get(r[0], sep_h)
            self.boxes.append((y, h))
            y += h
        h = y + pad
        # Next to the pointer and inside the screen: above the taskbar when it's at the bottom.
        area = WinForms.Screen.FromPoint(at).WorkingArea
        x = at.X - w if at.X + w > area.Right else at.X
        y = at.Y - h if at.Y + h > area.Bottom else at.Y
        self.form.SetBounds(max(area.Left, x), max(area.Top, y), w, h)
        self.hovered = -1
        self._draw()
        self.form.Show()
        self.form.Activate()
        ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(self.form.Handle.ToInt64()))

    def hide(self):
        if self.form.Visible:
            self.form.Hide()

    def _draw(self):
        w, h = self.form.Width, self.form.Height
        bmp = Bitmap(w, h, PixelFormat.Format24bppRgb)
        g = Graphics.FromImage(bmp)
        used = [g]  # drawing objects hold Windows resources: freed as soon as the picture is done

        def keep(obj):
            used.append(obj)
            return obj
        g.SmoothingMode = SmoothingMode.AntiAlias
        g.Clear(BACK)
        if not self.rounded:
            g.DrawRectangle(keep(Pen(RULE)), 0, 0, w - 1, h - 1)
        regular, bold, small = self.fonts
        flags = (WinForms.TextFormatFlags.VerticalCenter | WinForms.TextFormatFlags.Left
                 | WinForms.TextFormatFlags.EndEllipsis | WinForms.TextFormatFlags.NoPrefix)
        inset, text_x = self._px(6), self._px(28)
        for i, (r, (y, rh)) in enumerate(zip(self.rows, self.boxes)):
            if r[0] == "sep":
                g.FillRectangle(keep(SolidBrush(RULE)), Rectangle(self._px(12), y + rh // 2, w - self._px(24), max(1, self._px(1))))
                continue
            if r[0] == "label":
                WinForms.TextRenderer.DrawText(g, r[1].upper(), small, Rectangle(text_x, y + self._px(4), w - text_x, rh - self._px(4)),
                                               FAINT, BACK, flags)
                continue
            back = HOVER if i == self.hovered else BACK
            if i == self.hovered:
                g.FillPath(keep(SolidBrush(HOVER)), keep(_rounded(inset, y + 1, w - 2 * inset, rh - 2, self._px(6))))
            line = r[3].get("line")
            if line is not None:  # the launcher's colour, like the lines in the app
                bar = self._px(3)
                g.FillPath(keep(SolidBrush(line)), keep(_rounded(self._px(14), y + rh // 2 - self._px(7), bar, self._px(14), bar // 2)))
            WinForms.TextRenderer.DrawText(g, r[1], bold if r[3].get("bold") else regular,
                                           Rectangle(text_x, y, w - text_x - self._px(14), rh), INK, back, flags)
        for obj in used:
            obj.Dispose()
        old = self.pic.Image
        self.pic.Image = bmp
        if old is not None:
            old.Dispose()

    def _at(self, y):
        for i, (top, h) in enumerate(self.boxes):
            if top <= y < top + h and self.rows[i][0] == "item":
                return i
        return -1

    def _hover(self, i):
        if i != self.hovered:
            self.hovered = i
            self._draw()

    def _run(self, i):
        if 0 <= i < len(self.rows) and self.rows[i][0] == "item":
            self.hide()
            _later(self.rows[i][2])

    def _mouse_up(self, sender, e):
        if e.Button == WinForms.MouseButtons.Left:
            self._run(self._at(e.Y))

    def _key(self, sender, e):
        items = [i for i, r in enumerate(self.rows) if r[0] == "item"]
        if e.KeyCode in (WinForms.Keys.Down, WinForms.Keys.Up) and items:
            step = 1 if e.KeyCode == WinForms.Keys.Down else -1
            at = items.index(self.hovered) if self.hovered in items else (-1 if step == 1 else 0)
            self._hover(items[(at + step) % len(items)])
        elif e.KeyCode in (WinForms.Keys.Enter, WinForms.Keys.Space):
            self._run(self.hovered)
        elif e.KeyCode == WinForms.Keys.Escape:
            self.hide()


class Tray:
    def __init__(self, icon_path, on_open, on_play, on_quit):
        self.icon_path, self.on_open, self.on_play, self.on_quit = icon_path, on_open, on_play, on_quit
        self.recent = []  # [(game id, name)], newest first
        self._ready = threading.Event()
        self._icon = self._ctl = self._menu = None

    def start(self):
        t = Thread(ThreadStart(self._run))
        t.SetApartmentState(ApartmentState.STA)
        t.IsBackground = True
        t.Start()
        self._ready.wait(5)

    def _run(self):
        self._ctl = WinForms.Control()
        self._ctl.CreateControl()  # gives the thread a handle to post work to
        self._menu = _Menu()
        icon = self._icon = WinForms.NotifyIcon()
        icon.Icon = Icon(self.icon_path)
        icon.Text = "Junction"
        icon.MouseClick += self._click
        icon.BalloonTipClicked += lambda s, e: _later(self.on_open)
        icon.Visible = True
        self._ready.set()
        WinForms.Application.Run()

    def _click(self, sender, e):
        if e.Button == WinForms.MouseButtons.Left:
            _later(self.on_open)
        elif e.Button == WinForms.MouseButtons.Right:
            self._menu.show(self.rows(), WinForms.Cursor.Position)

    def rows(self):
        """The right-click menu: Open, the games played last (each with its launcher's colour), and Quit."""
        rows = [("item", "Open Junction", self.on_open, {"bold": True})]
        if self.recent:
            rows += [("sep",), ("label", "Play")]
            rows += [("item", name, lambda gid=gid: self.on_play(gid), {"line": LINES.get(gid.split(":", 1)[0])})
                     for gid, name in self.recent]
        rows += [("sep",), ("item", "Quit Junction", self.on_quit, {})]
        return rows

    def _on_thread(self, fn):
        if self._ctl is not None and self._ctl.IsHandleCreated:
            try:
                self._ctl.BeginInvoke(Action(fn))
            except Exception:  # noqa: BLE001 - the tray is already gone
                pass

    def tip(self, title, text):
        self._on_thread(lambda: self._icon.ShowBalloonTip(5000, title, text, WinForms.ToolTipIcon.Info))

    def stop(self):
        if getattr(self, "_stopped", False):
            return
        self._stopped = True

        def _stop():
            self._icon.Visible = False
            self._icon.Dispose()
            self._menu.form.Dispose()
            WinForms.Application.ExitThread()
        try:
            # Asked from the tray's own thread ("Quit Junction" in its menu): stop now. Waiting for our own thread
            # to get round to it would only make quitting two seconds slower.
            if self._ctl is not None and self._ctl.IsHandleCreated and not self._ctl.InvokeRequired:
                return _stop()
        except Exception:  # noqa: BLE001
            pass
        done = threading.Event()
        self._on_thread(lambda: (_stop(), done.set()))
        done.wait(2)
