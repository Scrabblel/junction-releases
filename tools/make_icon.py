"""Draw Junction's app icon (web/icon.ico): the logo's three lines joining into one, pointing up.

    python tools/make_icon.py
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "web", "icon.ico")
STEAM, GOG, EA, SIGNAL = "#4ea3e8", "#a874f5", "#ff5d55", "#ffb21d"
S = 1024           # drawn big, then shrunk, for smooth edges
VIEW = (22, 34)    # the logo's viewBox


def bezier(p0, p1, p2, p3, n=64):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append(tuple(u**3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t**3 * d for a, b, c, d in zip(p0, p1, p2, p3)))
    return out


def draw(width):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    scale = S * 0.86 / VIEW[1]
    ox, oy = (S - VIEW[0] * scale) / 2, (S - VIEW[1] * scale) / 2
    pt = lambda x, y: (ox + x * scale, oy + y * scale)
    w = round(width * scale)

    def line(points, colour):
        # A round brush stamped along the path: smooth curves and round ends.
        pts = [pt(*p) for p in points]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            n = max(1, int(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** .5 / 2))
            for i in range(n + 1):
                x, y = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n
                d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=colour)

    line(bezier((3, 32), (3, 22), (11, 20), (11, 14)), STEAM)
    line(bezier((19, 32), (19, 22), (11, 20), (11, 14)), EA)
    line([(11, 32), (11, 14)], GOG)  # the middle line is drawn over the curves where they meet
    line([(11, 14), (11, 2)], SIGNAL)
    return img


big, small = draw(3.2), draw(4.6)  # thicker lines for the tiny tray sizes
sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
frames = [(small if s <= 32 else big).resize((s, s), Image.LANCZOS) for s in sizes]
frames[-1].save(OUT, sizes=[(s, s) for s in sizes], append_images=frames[:-1])
print("wrote", OUT)
