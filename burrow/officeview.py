"""Office files in Quick Look.

1. Windows' own previewer for the type (the one Explorer's preview pane uses: Word, Excel,
   PowerPoint, Outlook, Visio... when Office is installed, and others like PDF). It runs in
   Windows' separate preview process (prevhost.exe), so a previewer that fails can't take
   Burrow down with it.
2. Without one, Burrow reads the file itself: Word (.docx) as text with headings, lists and
   tables; Excel (.xlsx) as a table per sheet; PowerPoint (.pptx) as each slide's text, with
   the file's own thumbnail on top. Only the XML inside the file is read; no macros or links
   are run or followed.
"""
from __future__ import annotations

import ctypes
import html
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

WIN = sys.platform == "win32"
OOXML = {"docx", "docm", "dotx", "xlsx", "xlsm", "xltx", "pptx", "pptm", "ppsx", "potx"}
OFFICE = OOXML | {"doc", "dot", "rtf", "xls", "xlsb", "ppt", "pps", "msg", "vsd", "vsdx", "pub", "one", "odt",
                  "ods", "odp"}
MAX_ROWS, MAX_COLS = 2000, 60
MAX_XML = 40 * 1024 * 1024                 # a part bigger than this isn't read (zip bombs)


# ----- 1. Windows' preview handlers --------------------------------------------------------------
IID_PREVIEW = "{8895b1c6-b41f-4c1c-a562-0d564250836f}"
IID_INIT_FILE = "{b7d14566-0509-4cce-a71f-0a554233bd9b}"
IID_INIT_STREAM = "{b824b49d-22ac-4161-ac8a-9916e8fa3f7f}"
IID_INIT_ITEM = "{7f73be3f-fb79-493c-a6c7-7ee14e245841}"
IID_SHELLITEM = "{43826d1e-e718-42ee-bc55-a1e261c37bfe}"

if WIN:
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    _ole32 = ctypes.windll.ole32
    _shlwapi = ctypes.windll.shlwapi
    _shell32 = ctypes.windll.shell32
    _ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    _shlwapi.AssocQueryStringW.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                           wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]


def _guid(s: str):
    g = GUID()
    _ole32.CLSIDFromString(s, ctypes.byref(g))
    return g


def _call(ptr, index, argtypes, *args) -> int:
    """Call method `index` of a COM interface; returns the HRESULT (0 = OK)."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(ptr, *args)


def _release(ptr):
    if ptr:
        _call(ptr, 2, [])


def _qi(ptr, iid: str):
    out = ctypes.c_void_p()
    hr = _call(ptr, 0, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(_guid(iid)), ctypes.byref(out))
    return out if hr == 0 and out else None


def handler_for(ext: str) -> str | None:
    """The CLSID of Windows' previewer for a file type, or None."""
    if not WIN or not ext:
        return None
    buf = ctypes.create_unicode_buffer(64)
    n = wintypes.DWORD(64)
    hr = _shlwapi.AssocQueryStringW(0, 16, "." + ext.lower(), IID_PREVIEW, buf, ctypes.byref(n))   # SHELLEXTENSION
    return buf.value if hr == 0 and buf.value else None


class PreviewHost(QWidget):
    """A window that a Windows previewer draws into."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen, False)
        self._ph = None
        self._keep = []                          # stream / item the previewer reads from

    def load(self, path: str) -> bool:
        """Start the previewer for this file; False if there is none or it refused."""
        self.unload()
        ext = os.path.splitext(path)[1][1:]
        clsid = handler_for(ext)
        if not clsid:
            return False
        ph = ctypes.c_void_p()
        # CLSCTX_LOCAL_SERVER: in Windows' preview process, like Explorer does
        hr = _ole32.CoCreateInstance(ctypes.byref(_guid(clsid)), None, 0x4, ctypes.byref(_guid(IID_PREVIEW)),
                                     ctypes.byref(ph))
        if hr != 0 or not ph:
            return False
        if not self._initialize(ph, path):
            _release(ph)
            return False
        self._ph = ph
        hwnd = int(self.winId())
        _call(ph, 3, [wintypes.HWND, ctypes.POINTER(RECT)], hwnd, ctypes.byref(self._rect()))   # SetWindow
        if _call(ph, 5, []) != 0:                                                                # DoPreview
            self.unload()
            return False
        return True

    def _initialize(self, ph, path) -> bool:
        init = _qi(ph, IID_INIT_FILE)
        if init:
            hr = _call(init, 3, [wintypes.LPCWSTR, wintypes.DWORD], path, 0)       # STGM_READ
            _release(init)
            return hr == 0
        init = _qi(ph, IID_INIT_STREAM)
        if init:
            stream = ctypes.c_void_p()
            hr = _shlwapi.SHCreateStreamOnFileEx(path, 0x40, 0, False, None, ctypes.byref(stream))  # read, share
            if hr == 0:
                hr = _call(init, 3, [ctypes.c_void_p, wintypes.DWORD], stream, 0)
                self._keep.append(stream)
            _release(init)
            return hr == 0
        init = _qi(ph, IID_INIT_ITEM)
        if init:
            item = ctypes.c_void_p()
            _shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                                             ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
            hr = _shell32.SHCreateItemFromParsingName(path, None, ctypes.byref(_guid(IID_SHELLITEM)), ctypes.byref(item))
            if hr == 0:
                hr = _call(init, 3, [ctypes.c_void_p, wintypes.DWORD], item, 0)
                self._keep.append(item)
            _release(init)
            return hr == 0
        return False

    def _rect(self):
        r = self.devicePixelRatioF()
        return RECT(0, 0, int(self.width() * r), int(self.height() * r))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._ph:
            _call(self._ph, 4, [ctypes.POINTER(RECT)], ctypes.byref(self._rect()))                # SetRect

    def unload(self):
        if self._ph:
            try:
                _call(self._ph, 6, [])                                                            # Unload
            finally:
                _release(self._ph)
                self._ph = None
        for p in self._keep:
            _release(p)
        self._keep.clear()

    def paintEngine(self):
        return None if self._ph else super().paintEngine()


# ----- 2. reading the file ourselves ------------------------------------------------------------
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _xml(z: zipfile.ZipFile, name: str):
    info = z.getinfo(name)
    if info.file_size > MAX_XML:
        raise ValueError("This file is too big to preview.")
    return ET.fromstring(z.read(name))


def read_office(path: str) -> dict:
    """{'kind': 'html'|'sheets', 'html': str, 'sheets': [(name, rows)], 'thumb': bytes|None,
    'error': str}. Safe on a worker thread."""
    ext = os.path.splitext(path)[1][1:].lower()
    try:
        with zipfile.ZipFile(path) as z:
            thumb = None
            for n in ("docProps/thumbnail.jpeg", "docProps/thumbnail.jpg", "docProps/thumbnail.png"):
                if n in z.namelist():
                    thumb = z.read(n)
                    break
            if ext.startswith("doc") or ext == "dotx":
                return {"kind": "html", "html": _docx_html(z), "thumb": None, "error": ""}
            if ext.startswith("xl"):
                return {"kind": "sheets", "sheets": _xlsx_sheets(z), "thumb": None, "error": ""}
            return {"kind": "html", "html": _pptx_html(z), "thumb": thumb, "error": ""}
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, OSError) as ex:
        return {"kind": "", "error": str(ex) if isinstance(ex, ValueError) else
                "This file couldn't be read (it may be password-protected or damaged)."}


def _runs(p) -> str:
    """A Word paragraph's text, with bold / italic / underline kept."""
    out = []
    for r in p.iter(W + "r"):
        text = "".join((t.text or "") if t.tag == W + "t" else ("\t" if t.tag == W + "tab" else "\n")
                       for t in r if t.tag in (W + "t", W + "tab", W + "br"))
        if not text:
            continue
        s = html.escape(text).replace("\n", "<br>")
        rp = r.find(W + "rPr")
        if rp is not None:
            if rp.find(W + "b") is not None and rp.find(W + "b").get(W + "val") not in ("0", "false"):
                s = f"<b>{s}</b>"
            if rp.find(W + "i") is not None and rp.find(W + "i").get(W + "val") not in ("0", "false"):
                s = f"<i>{s}</i>"
            if rp.find(W + "u") is not None and rp.find(W + "u").get(W + "val") not in ("none", None):
                s = f"<u>{s}</u>"
        out.append(s)
    return "".join(out)


def _docx_html(z) -> str:
    body = _xml(z, "word/document.xml").find(W + "body")
    parts = []
    for el in body if body is not None else []:
        if el.tag == W + "p":
            ppr = el.find(W + "pPr")
            style = ""
            lst = False
            if ppr is not None:
                ps = ppr.find(W + "pStyle")
                style = (ps.get(W + "val") or "").lower() if ps is not None else ""
                lst = ppr.find(W + "numPr") is not None
            text = _runs(el)
            m = re.match(r"heading(\d)", style)
            if m and text:
                n = min(4, int(m.group(1)))
                parts.append(f"<h{n}>{text}</h{n}>")
            elif style == "title" and text:
                parts.append(f"<h1>{text}</h1>")
            elif lst and text:
                parts.append(f"<p style='margin-left:18px'>•&nbsp;&nbsp;{text}</p>")
            else:
                parts.append(f"<p>{text or '&nbsp;'}</p>")
        elif el.tag == W + "tbl":
            rows = []
            for tr in el.iter(W + "tr"):
                cells = ["<td style='padding:3px 8px; border:1px solid #8884'>"
                         + "<br>".join(_runs(p) for p in tc.iter(W + "p")) + "</td>" for tc in tr.iter(W + "tc")]
                rows.append("<tr>" + "".join(cells) + "</tr>")
            parts.append("<table cellspacing='0' style='border-collapse:collapse; margin:6px 0'>"
                         + "".join(rows) + "</table>")
        if len(parts) > 20000:
            parts.append("<p><i>…</i></p>")
            break
    return "".join(parts)


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + ord(ch.upper()) - 64
        else:
            break
    return max(0, n - 1)


def _xlsx_sheets(z) -> list:
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in _xml(z, "xl/sharedStrings.xml").iter(S + "si"):
            shared.append("".join(t.text or "" for t in si.iter(S + "t")))
    wb = _xml(z, "xl/workbook.xml")
    rels = {r.get("Id"): r.get("Target") for r in _xml(z, "xl/_rels/workbook.xml.rels").iter(PR + "Relationship")}
    out = []
    for sh in wb.iter(S + "sheet"):
        target = rels.get(sh.get(R + "id"), "")
        name = "xl/" + target.lstrip("/").removeprefix("xl/")
        if name not in z.namelist():
            continue
        rows = []
        for row in _xml(z, name).iter(S + "row"):
            if len(rows) >= MAX_ROWS:
                break
            vals = []
            for c in row.iter(S + "c"):
                i = _col_index(c.get("r", "")) if c.get("r") else len(vals)
                if i >= MAX_COLS:
                    continue
                t = c.get("t")
                v = c.find(S + "v")
                if t == "s" and v is not None and (v.text or "").isdigit():
                    k = int(v.text)
                    val = shared[k] if k < len(shared) else ""
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(S + "t"))
                elif t == "b":
                    val = "TRUE" if (v is not None and v.text == "1") else "FALSE"
                else:
                    val = v.text if v is not None and v.text is not None else ""
                    if t != "str" and re.fullmatch(r"-?\d+\.\d{6,}", val or ""):
                        val = f"{float(val):.6g}"        # 0.30000000000000004 -> 0.3
                while len(vals) < i:
                    vals.append("")
                vals.append(val)
            rows.append(vals)
        out.append((sh.get("name") or f"Sheet {len(out) + 1}", rows))
    return out


def _pptx_html(z) -> str:
    pres = _xml(z, "ppt/presentation.xml")
    rels = {r.get("Id"): r.get("Target") for r in _xml(z, "ppt/_rels/presentation.xml.rels").iter(PR + "Relationship")}
    P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    parts = []
    for n, sid in enumerate(pres.iter(P + "sldId"), 1):
        target = rels.get(sid.get(R + "id"), "")
        name = "ppt/" + target.lstrip("/").removeprefix("ppt/")
        if name not in z.namelist():
            continue
        paras = []
        for p in _xml(z, name).iter(A + "p"):
            text = "".join(t.text or "" for t in p.iter(A + "t")).strip()
            if text:
                paras.append(html.escape(text))
        body = "".join(f"<p>{t}</p>" for t in paras[1:])
        parts.append(f"<h3 style='margin-top:16px'>{n}. {paras[0] if paras else ''}</h3>{body}")
    return "".join(parts) or "<p><i>No text on the slides.</i></p>"


def build_view(res: dict, parent=None):
    """The widget for read_office()'s result (GUI thread)."""
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import (QAbstractItemView, QLabel, QTableWidget, QTableWidgetItem, QTabWidget,
                                   QTextBrowser, QVBoxLayout)
    if res.get("kind") == "sheets":
        tabs = QTabWidget(parent)
        tabs.setDocumentMode(True)
        for name, rows in res["sheets"]:
            cols = max((len(r) for r in rows), default=1)
            t = QTableWidget(len(rows), cols)
            t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            t.setHorizontalHeaderLabels([_col_name(i) for i in range(cols)])
            for r, vals in enumerate(rows):
                for c, v in enumerate(vals):
                    if v:
                        t.setItem(r, c, QTableWidgetItem(v))
            t.resizeColumnsToContents()
            for c in range(cols):
                t.setColumnWidth(c, min(t.columnWidth(c), 320))
            tabs.addTab(t, name)
        return tabs
    w = QWidget(parent)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    if res.get("thumb"):
        pm = QPixmap()
        if pm.loadFromData(res["thumb"]):
            pic = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
            pic.setPixmap(pm.scaledToHeight(min(260, pm.height()), Qt.TransformationMode.SmoothTransformation))
            lay.addWidget(pic)
    tb = QTextBrowser()
    tb.setOpenLinks(False)                        # nothing in the file is followed
    tb.setHtml(res.get("html", ""))
    lay.addWidget(tb, 1)
    return w


def _col_name(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s

