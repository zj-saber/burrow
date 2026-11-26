"""Camera RAW photos and Blackmagic RAW clips, for the inspector. Runs on a worker thread.

RAW photos: cameras embed a JPEG preview (usually full size) in the RAW file. It's found by
walking the TIFF structure (CR2, NEF, ARW, DNG, ORF, PEF, SRW, RW2), the RAF header, or the
CR3 PRVW box, and decoded without touching the raw sensor data. If none decodes, Windows'
own thumbnail (Microsoft RAW Image Extension / camera codecs) is used instead.

BRAW: Qt can't decode Blackmagic RAW, so only the clip's QuickTime metadata is read
(resolution, frame rate, duration, codec). Opening the file plays it in Blackmagic's player.
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys

from PySide6.QtGui import QImage, QTransform

RAW_EXTS = set("cr2 cr3 crw nef nrw arw srf sr2 dng raf orf pef srw rw2 rwl 3fr iiq mos mrw x3f erf kdc".split())
BRAW_EXTS = {"braw"}
# photo formats Qt can't read but Windows can (via its HEIF/AV1/JPEG XL extensions): previewed
# through Windows' thumbnail (the TIFF/JPEG search simply finds nothing and falls back)
SHELL_IMAGE_EXTS = set("heic heif hif avif jxl jxr wdp hdp".split())
WIN = sys.platform == "win32"

_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


# ----- TIFF-based RAW ---------------------------------------------------------------------
class _Tiff:
    """Minimal TIFF/IFD reader: enough to find the JPEG previews and EXIF in a camera RAW."""
    def __init__(self, f, base=0):
        self.f, self.base = f, base
        f.seek(base)
        head = f.read(8)
        self.e = "<" if head[:2] == b"II" else ">"
        if head[:2] not in (b"II", b"MM"):
            raise ValueError("not TIFF")
        self.first = struct.unpack(self.e + "I", head[4:8])[0]

    def ifd(self, off):
        """{tag: (type, count, value_or_offset_bytes)} and the next IFD offset."""
        f, e = self.f, self.e
        f.seek(self.base + off)
        raw = f.read(2)
        if len(raw) < 2:
            return {}, 0
        n = struct.unpack(e + "H", raw)[0]
        if n > 1000:
            return {}, 0
        data = f.read(12 * n + 4)
        tags = {}
        for i in range(n):
            tag, typ, cnt = struct.unpack(e + "HHI", data[i * 12:i * 12 + 8])
            tags[tag] = (typ, cnt, data[i * 12 + 8:i * 12 + 12])
        nxt = struct.unpack(e + "I", data[12 * n:12 * n + 4])[0] if len(data) >= 12 * n + 4 else 0
        return tags, nxt

    def values(self, entry):
        """The values of one IFD entry (read from its offset when they don't fit inline)."""
        typ, cnt, raw = entry
        size = _TYPE_SIZES.get(typ, 1) * cnt
        if size > 4:
            self.f.seek(self.base + struct.unpack(self.e + "I", raw)[0])
            raw = self.f.read(min(size, 1 << 16))
        e = self.e
        if typ == 2:
            return raw[:cnt].split(b"\0")[0].decode("latin-1", "replace").strip()
        if typ == 3:
            return list(struct.unpack(e + "%dH" % cnt, raw[:2 * cnt]))
        if typ in (4, 13):
            return list(struct.unpack(e + "%dI" % cnt, raw[:4 * cnt]))
        if typ in (5, 10):
            vals = struct.unpack(e + ("%dI" if typ == 5 else "%di") % (2 * cnt), raw[:8 * cnt])
            return [(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]
        return raw[:size]

    def one(self, tags, tag, default=None):
        if tag not in tags:
            return default
        v = self.values(tags[tag])
        return v[0] if isinstance(v, list) and v else v


def _tiff_preview(f, info, base=0):
    """Walk a RAW's IFDs for the largest embedded JPEG and the EXIF block."""
    t = _Tiff(f, base)
    jpegs = []          # (length, offset)
    exif_off = None
    seen = set()
    todo = [t.first]
    while todo:
        off = todo.pop(0)
        if not off or off in seen or len(seen) > 40:
            continue
        seen.add(off)
        tags, nxt = t.ifd(off)
        if not tags:
            continue
        if 0x10F in tags and "make" not in info:
            info["make"] = t.one(tags, 0x10F, "")
        if 0x110 in tags and "model" not in info:
            info["model"] = t.one(tags, 0x110, "")
        if 0x112 in tags and "orientation" not in info and off == t.first:
            info["orientation"] = t.one(tags, 0x112, 1)
        if 0x201 in tags and 0x202 in tags:
            jpegs.append((t.one(tags, 0x202, 0), t.one(tags, 0x201, 0)))
        comp = t.one(tags, 0x103, 0)
        if comp in (6, 7) and 0x111 in tags and 0x117 in tags:
            offs, lens = t.values(tags[0x111]), t.values(tags[0x117])
            if len(offs) == 1 and len(lens) == 1:
                jpegs.append((lens[0], offs[0]))
        if 0x2E in tags:                             # RW2 JpgFromRaw
            typ, cnt, raw = tags[0x2E]
            jpegs.append((cnt, struct.unpack(t.e + "I", raw)[0]))
        if 0x14A in tags:                            # SubIFDs
            todo += t.values(tags[0x14A])
        if 0x8769 in tags:
            exif_off = t.one(tags, 0x8769)
        todo.append(nxt)
    if exif_off:
        tags, _ = t.ifd(exif_off)
        exp = t.one(tags, 0x829A)
        fn = t.one(tags, 0x829D)
        foc = t.one(tags, 0x920A)
        if exp and exp[1]:
            info["exposure"] = f"1/{round(exp[1] / exp[0])} s" if exp[0] and exp[0] < exp[1] else f"{exp[0] / exp[1]:g} s"
        if fn and fn[1]:
            info["aperture"] = f"f/{fn[0] / fn[1]:.1f}"
        if foc and foc[1]:
            info["focal"] = f"{foc[0] / foc[1]:g} mm"
        iso = t.one(tags, 0x8827)
        if iso:
            info["iso"] = f"ISO {iso}"
        if 0xA434 in tags:
            info["lens"] = t.one(tags, 0xA434, "")
        if 0x9003 in tags:
            info["taken"] = t.one(tags, 0x9003, "")
    out = []
    for length, off in sorted(set(jpegs), reverse=True):
        if 0 < length < 200 * 1024 * 1024:
            f.seek(base + off)
            data = f.read(length)
            if data[:2] == b"\xff\xd8":
                out.append(data)
    return out


def _raf_preview(f, info):
    f.seek(0)
    head = f.read(100)
    if not head.startswith(b"FUJIFILMCCD-RAW"):
        return []
    off, length = struct.unpack(">II", head[84:92])
    f.seek(off)
    data = f.read(length)
    return [data] if data[:2] == b"\xff\xd8" else []


def _cr3_preview(f, info):
    f.seek(0)
    blob = f.read(4 * 1024 * 1024)
    i = blob.find(b"PRVW")
    if i < 4:
        return []
    size = struct.unpack(">I", blob[i - 4:i])[0]
    box = blob[i - 4:i - 4 + size]
    j = box.find(b"\xff\xd8")
    return [box[j:]] if j >= 0 else []


def _decode(data: bytes) -> QImage:
    img = QImage.fromData(data)
    return img


def _orient(img: QImage, o) -> QImage:
    angle = {3: 180, 6: 90, 8: 270}.get(o or 1)
    return img.transformed(QTransform().rotate(angle)) if angle else img


# ----- Windows thumbnail fallback (IShellItemImageFactory) --------------------------------
def shell_thumbnail(path: str, size: int = 1600) -> QImage:
    """A large thumbnail from Windows' own codecs (HEIC, AVIF, RAW codec packs)."""
    if not WIN:
        return QImage()
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                    ("bmWidthBytes", ctypes.c_long), ("bmPlanes", ctypes.c_ushort),
                    ("bmBitsPixel", ctypes.c_ushort), ("bmBits", ctypes.c_void_p)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    ole32, shell32, gdi32, user32 = ctypes.windll.ole32, ctypes.windll.shell32, ctypes.windll.gdi32, ctypes.windll.user32
    ole32.CoInitializeEx(None, 0x2)
    iid = GUID()
    ole32.CLSIDFromString("{bcc18b79-ba16-442f-80c4-8a59c30c463b}", ctypes.byref(iid))
    ptr = ctypes.c_void_p()
    shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    if shell32.SHCreateItemFromParsingName(path, None, ctypes.byref(iid), ctypes.byref(ptr)) != 0 or not ptr:
        return QImage()
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    get_image = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, SIZE, ctypes.c_int, ctypes.POINTER(wintypes.HBITMAP))(vtbl[3])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
    hbm = wintypes.HBITMAP()
    try:
        hr = get_image(ptr, SIZE(size, size), 0x1 | 0x8, ctypes.byref(hbm))    # BIGGERSIZEOK | THUMBNAILONLY
    finally:
        release(ptr)
    if hr != 0 or not hbm:
        return QImage()
    try:
        bm = BITMAP()
        gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm))
        w, h = bm.bmWidth, abs(bm.bmHeight)
        bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        dc = user32.GetDC(None)
        gdi32.GetDIBits(dc, hbm, 0, h, buf, ctypes.byref(bih), 0)
        user32.ReleaseDC(None, dc)
        return QImage(buf.raw, w, h, w * 4, QImage.Format.Format_RGB32).copy()
    finally:
        gdi32.DeleteObject(hbm)


def read_raw(path: str) -> dict:
    """{'image': QImage, 'info': {...}, 'source': 'embedded'|'windows', 'error': str}"""
    info: dict = {}
    candidates = []
    try:
        with open(path, "rb") as f:
            head = f.read(16)
            if head.startswith(b"FUJIFILMCCD-RAW"):
                candidates = _raf_preview(f, info)
            elif head[4:8] == b"ftyp" and b"crx" in head:
                candidates = _cr3_preview(f, info)
            else:
                try:
                    candidates = _tiff_preview(f, info)
                except (ValueError, struct.error):
                    candidates = []
    except OSError as ex:
        return {"image": QImage(), "info": info, "source": "", "error": ex.strerror or str(ex)}
    for data in candidates:                     # largest first; lossless/odd JPEGs simply fail to decode
        img = _decode(data)
        if not img.isNull() and img.width() >= 160:
            return {"image": _orient(img, info.get("orientation")), "info": info, "source": "embedded", "error": ""}
    img = shell_thumbnail(path)
    if not img.isNull():
        return {"image": img, "info": info, "source": "windows", "error": ""}
    return {"image": QImage(), "info": info, "source": "", "error": "No preview could be read from this RAW file."}


# ----- Blackmagic RAW (QuickTime container) -----------------------------------------------
def _atoms(f, start, end):
    """Yield (type, start, end) for each box in a QuickTime/MP4-style container."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        h = f.read(16)
        if len(h) < 8:
            return
        size, typ = struct.unpack(">I4s", h[:8])
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack(">Q", h[8:16])[0], 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            return
        yield typ, pos + hdr, pos + size
        pos += size


def braw_info(path: str) -> dict:
    """Resolution, frame rate, duration and codec of a Blackmagic RAW clip, from its header."""
    info = {}
    with open(path, "rb") as f:
        end = os.fstat(f.fileno()).st_size
        moov = next(((s, e) for t, s, e in _atoms(f, 0, end) if t == b"moov"), None)
        if not moov:
            return {"error": "Not a readable Blackmagic RAW clip."}
        for t, s, e in _atoms(f, *moov):
            if t == b"mvhd":
                f.seek(s)
                d = f.read(32)
                if d[0] == 1:
                    ts, dur = struct.unpack(">IQ", d[20:32])
                else:
                    ts, dur = struct.unpack(">II", d[12:20])
                if ts:
                    info["duration_s"] = dur / ts
            elif t == b"trak":
                vid = {}
                for t2, s2, e2 in _atoms(f, s, e):
                    if t2 == b"tkhd":
                        f.seek(s2)
                        d = f.read(92)
                        wo = 84 if d[0] == 1 else 76
                        w, h = struct.unpack(">II", d[wo:wo + 8])
                        vid["size"] = (w >> 16, h >> 16)
                    elif t2 == b"mdia":
                        for t3, s3, e3 in _atoms(f, s2, e2):
                            if t3 == b"mdhd":
                                f.seek(s3)
                                d = f.read(32)
                                vid["timescale"] = struct.unpack(">I", d[20:24] if d[0] == 1 else d[12:16])[0]
                            elif t3 == b"hdlr":
                                f.seek(s3)
                                vid["handler"] = f.read(12)[8:12]
                            elif t3 == b"minf":
                                for t4, s4, e4 in _atoms(f, s3, e3):
                                    if t4 == b"stbl":
                                        for t5, s5, e5 in _atoms(f, s4, e4):
                                            f.seek(s5)
                                            d = f.read(24)
                                            if t5 == b"stsd":
                                                vid["codec"] = d[12:16].decode("latin-1", "replace")
                                            elif t5 == b"stts" and len(d) >= 16:
                                                vid["delta"] = struct.unpack(">I", d[12:16])[0]
                if vid.get("handler") == b"vide":
                    w, h = vid.get("size", (0, 0))
                    if w and h:
                        info["resolution"] = f"{w} × {h}"
                    if vid.get("timescale") and vid.get("delta"):
                        info["fps"] = vid["timescale"] / vid["delta"]
                    info["codec"] = vid.get("codec", "")
    return info
