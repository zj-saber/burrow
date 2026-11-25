"""Draw Burrow's app icon and write a multi-size .ico (PNG-compressed entries).

The mark (option B, "Warm"): a cream folder on an espresso tile, with a rounded "burrow" opening tunnelled into its
front, lit from the top. Large sizes get depth (gradients, a soft shadow, a highlight);
16-24 px are drawn simpler and bolder so they stay crisp in the taskbar and title bar.

Usage: python tools/make_icon.py assets/burrow.ico [preview.png]
"""
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter,
                           QPainterPath, QRadialGradient)

SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def _rounded(x, y, w, h, r):
    p = QPainterPath()
    p.addRoundedRect(QRectF(x, y, w, h), r, r)
    return p


def draw(p: QPainter, size: int):
    """B · Warm: espresso tile, cream folder, dark arched doorway (matches the Primary theme)."""
    small = size <= 24
    s = size / 256.0
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)

    # tile: warm brown to espresso, with a soft top-left glow on larger sizes
    inset = 6 if small else 10
    tile = _rounded(inset * s, inset * s, (256 - 2 * inset) * s, (256 - 2 * inset) * s, 58 * s)
    g = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    g.setColorAt(0.0, QColor("#7a5a40"))
    g.setColorAt(0.55, QColor("#4f3b2b"))
    g.setColorAt(1.0, QColor("#2e261f"))
    p.fillPath(tile, g)
    if not small:
        glow = QRadialGradient(QPointF(80 * s, 36 * s), 200 * s)
        glow.setColorAt(0.0, QColor(255, 240, 220, 60))
        glow.setColorAt(1.0, QColor(255, 240, 220, 0))
        p.fillPath(tile, glow)

    # folder geometry (256-space); bolder and larger at small sizes
    fx, fw, back_y, front_y, bottom = (34, 188, 56, 96, 206) if small else (50, 156, 70, 104, 196)
    back = _rounded(fx * s, (back_y + 12) * s, fw * s, (bottom - back_y - 12) * s, 16 * s).united(
        _rounded(fx * s, back_y * s, 66 * s, 34 * s, 12 * s))
    p.fillPath(back, QColor("#cfb696"))

    if not small:   # soft shadow under the front panel
        for i, a in enumerate((34, 22, 12)):
            sh = _rounded((fx - 1) * s, (front_y + 6 + i * 3) * s, (fw + 2) * s, (bottom - front_y) * s, 18 * s)
            p.fillPath(sh, QColor(30, 18, 8, a))

    front = _rounded(fx * s, front_y * s, fw * s, (bottom - front_y) * s, 18 * s)
    fg = QLinearGradient(QPointF(0, front_y * s), QPointF(0, bottom * s))
    fg.setColorAt(0.0, QColor("#fbf8f4"))
    fg.setColorAt(1.0, QColor("#e4d7c3"))
    p.fillPath(front, fg)

    # the burrow: an arched doorway sitting on the bottom edge of the front panel
    cx, hw = 128, (42 if small else 30)
    top = 124 if small else 138
    arch = QPainterPath()
    arch.moveTo((cx - hw) * s, bottom * s)
    arch.lineTo((cx - hw) * s, (top + hw) * s)
    arch.arcTo(QRectF((cx - hw) * s, top * s, 2 * hw * s, 2 * hw * s), 180, -180)
    arch.lineTo((cx + hw) * s, bottom * s)
    arch.closeSubpath()
    ag = QLinearGradient(QPointF(0, top * s), QPointF(0, bottom * s))
    ag.setColorAt(0.0, QColor("#2a2018"))
    ag.setColorAt(1.0, QColor("#593e22"))
    p.fillPath(arch.intersected(front), ag)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    draw(p, size)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba.data())


def write_ico(out: str):
    images = [(sz, png_bytes(render(sz))) for sz in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for sz, data in images:
        dim = 0 if sz >= 256 else sz
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    with open(out, "wb") as f:
        f.write(header + entries + blobs)
    print("wrote", out)


def write_preview(out: str):
    """All sizes at 1:1 on light and dark strips, plus a 4x enlargement of 16 and 24 px."""
    shown = [256, 128, 64, 48, 32, 24, 16]
    w = sum(shown) + 24 * (len(shown) + 1) + 2 * (64 + 96 + 24)
    h = 2 * (256 + 48)
    sheet = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(sheet)
    for row, bg in enumerate(("#f3f4f6", "#1b1c1f")):
        y0 = row * (h // 2)
        p.fillRect(0, y0, w, h // 2, QColor(bg))
        x = 24
        for sz in shown:
            p.drawImage(x, y0 + 24 + (256 - sz) // 2, render(sz))
            x += sz + 24
        for sz in (16, 24):
            big = render(sz).scaled(sz * 4, sz * 4, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.FastTransformation)
            p.drawImage(x, y0 + 24 + (256 - sz * 4) // 2, big)
            x += sz * 4 + 24
    p.end()
    sheet.save(out)
    print("wrote", out)


if __name__ == "__main__":
    app = QGuiApplication(sys.argv[:1])  # noqa: F841 - needed for QPainter
    write_ico(sys.argv[1] if len(sys.argv) > 1 else "assets/burrow.ico")
    if len(sys.argv) > 2:
        write_preview(sys.argv[2])
