"""Draw the installer's pictures (installer/*.bmp), in the dark colours of Junction itself:

- wizard-*.bmp: the tall picture on the first and last pages. The three launcher lines rise from the bottom,
  join, and go on up as one, like the opening animation.
- small-*.bmp: the logo in the top corner of the other pages.

    python tools/make_installer_art.py

Each comes in three sizes (100, 150 and 200% display scaling); Junction.iss lists them and Setup picks the sharpest.
"""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "installer")
GROUND = (18, 22, 29)       # --panel, the installer's background (WizardBackColor in Junction.iss)
DEEP = (10, 13, 18)         # --ground
STEAM, GOG, EA, SIGNAL = (78, 163, 232), (168, 116, 245), (255, 93, 85), (255, 178, 29)
SS = 4                      # drawn 4x as big, then shrunk, for smooth edges


def bezier(p0, p1, p2, p3, n=96):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append(tuple(u**3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t**3 * d for a, b, c, d in zip(p0, p1, p2, p3)))
    return out


def stroke(d, points, colour, w):
    """A round brush stamped along the path: smooth curves and round ends."""
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        n = max(1, int(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** .5 / (w / 6)))
        for i in range(n + 1):
            x, y = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=colour)


def lines(d, w, h, width):
    """The logo's lines in a w x h box: up from the bottom edge, joining, then on up as one."""
    cx, join, top, spread = w / 2, h * .52, h * .2, w * .3
    left = bezier((cx - spread, h + width), (cx - spread, h * .7), (cx, h * .64), (cx, join))
    right = bezier((cx + spread, h + width), (cx + spread, h * .7), (cx, h * .64), (cx, join))
    stroke(d, left, STEAM, width)
    stroke(d, right, EA, width)
    stroke(d, [(cx, h + width), (cx, join)], GOG, width)
    stroke(d, [(cx, join), (cx, top)], SIGNAL, width)


def wizard(scale):
    w, h = 164 * scale * SS, 314 * scale * SS
    # A little darker towards the bottom, where the lines come in.
    shade = Image.linear_gradient("L").resize((w, h)).point(lambda v: int(v * .8))
    img = Image.composite(Image.new("RGB", (w, h), DEEP), Image.new("RGB", (w, h), GROUND), shade)
    # A soft glow behind the lines, then the lines themselves.
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    lines(ImageDraw.Draw(glow), w, h, 9 * scale * SS)
    glow = glow.filter(ImageFilter.GaussianBlur(14 * scale * SS)).point(lambda v: int(v * .35))
    img = ImageChops.add(img, glow)
    lines(ImageDraw.Draw(img), w, h, 5 * scale * SS)
    return img.resize((164 * scale, 314 * scale), Image.LANCZOS)


def small(scale):
    n = 55 * scale * SS
    img = Image.new("RGB", (n, n), GROUND)
    d = ImageDraw.Draw(img)
    # The app icon's shape: the viewBox 22 x 34, centred.
    k = n * .8 / 34
    ox, oy = (n - 22 * k) / 2, (n - 34 * k) / 2
    pt = lambda x, y: (ox + x * k, oy + y * k)  # noqa: E731
    width = 3.4 * k
    stroke(d, [pt(*p) for p in bezier((3, 32), (3, 22), (11, 20), (11, 14))], STEAM, width)
    stroke(d, [pt(*p) for p in bezier((19, 32), (19, 22), (11, 20), (11, 14))], EA, width)
    stroke(d, [pt(11, 32), pt(11, 14)], GOG, width)
    stroke(d, [pt(11, 14), pt(11, 2)], SIGNAL, width)
    return img.resize((55 * scale, 55 * scale), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    for pct, scale in ((100, 1), (150, 1.5), (200, 2)):
        for name, make, base in (("wizard", wizard, (164, 314)), ("small", small, (55, 55))):
            img = make(3).resize((round(base[0] * 1.5), round(base[1] * 1.5)), Image.LANCZOS) if scale == 1.5 else make(scale)
            path = os.path.join(OUT, f"{name}-{pct}.bmp")
            img.save(path)
            print("wrote", path, img.size)


if __name__ == "__main__":
    main()
