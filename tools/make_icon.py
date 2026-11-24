"""Draw Burrow's app icon and write it as a multi-size .ico (PNG-compressed entries).
Usage: python tools/make_icon.py assets/burrow.ico"""
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath

SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(size: int) -> bytes:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 256.0
    # rounded tile
    grad = QLinearGradient(QPointF(0, 0), QPointF(0, size))
    grad.setColorAt(0, QColor("#4d8dff"))
    grad.setColorAt(1, QColor("#2a5fd6"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 56 * s, 56 * s)
    # folder: back tab + front body
    p.setBrush(QColor(255, 255, 255, 150))
    tab = QPainterPath()
    tab.addRoundedRect(QRectF(52 * s, 70 * s, 70 * s, 40 * s), 12 * s, 12 * s)
    p.drawPath(tab)
    p.drawRoundedRect(QRectF(52 * s, 88 * s, 152 * s, 100 * s), 16 * s, 16 * s)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(QRectF(52 * s, 108 * s, 152 * s, 88 * s), 16 * s, 16 * s)
    # burrow opening
    p.setBrush(QColor("#2a5fd6"))
    p.drawEllipse(QRectF(104 * s, 132 * s, 48 * s, 40 * s))
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba.data())


def main(out: str):
    app = QGuiApplication(sys.argv[:1])  # noqa: F841 - needed for QPainter
    images = [(sz, render(sz)) for sz in SIZES]
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


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "assets/burrow.ico")
