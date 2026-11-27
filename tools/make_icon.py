"""Draw Burrow's app icon and write a multi-size .ico (PNG-compressed entries).

The mark: a minimal forest green folder (flat, two tones, no tile), matching the Forest colors.

Usage: python tools/make_icon.py assets/burrow.ico [preview.png]
"""
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath

SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
BACK, FRONT = "#265c3a", "#367d4c"     # forest green: back and tab, front


def _rounded(x, y, w, h, r):
    p = QPainterPath()
    p.addRoundedRect(QRectF(x, y, w, h), r, r)
    return p


def draw(p: QPainter, size: int):
    """A minimal forest green folder: flat, two tones (a darker back and tab, a lighter front),
    no tile. Tiny sizes get a slightly bigger, bolder shape so they stay crisp in the taskbar."""
    small = size <= 24
    s = size / 256.0
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    fx, fw = (14, 228) if small else (22, 212)
    back_y, front_y, bottom = (26, 70, 230) if small else (32, 74, 224)
    tab_w = 92 if small else 86
    back = _rounded(fx * s, (back_y + 14) * s, fw * s, (bottom - back_y - 14) * s, 22 * s).united(
        _rounded(fx * s, back_y * s, tab_w * s, 40 * s, 16 * s))
    p.fillPath(back, QColor(BACK))
    p.fillPath(_rounded(fx * s, front_y * s, fw * s, (bottom - front_y) * s, 24 * s), QColor(FRONT))


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
