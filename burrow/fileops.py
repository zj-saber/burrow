"""File operations: clipboard, Recycle Bin, shell dialogs, TeraCopy, built-in copier."""
from __future__ import annotations

import ctypes
import itertools
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, QObject, Qt, QUrl, Signal
from PySide6.QtGui import QGuiApplication

WIN = sys.platform == "win32"
# Qt maps this mime type straight onto Explorer's "Preferred DropEffect" clipboard format.
DROPEFFECT_MIME = 'application/x-qt-windows-mime;value="Preferred DropEffect"'
TEMP_DIR = Path(tempfile.gettempdir()) / "Burrow"


# ----- path helpers ---------------------------------------------------------
def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def is_inside(child: str, parent: str) -> bool:
    """True if child is parent or lies somewhere below it."""
    c = os.path.normcase(os.path.normpath(child))
    p = os.path.normcase(os.path.normpath(parent)).rstrip("\\/")
    return c == p or c.startswith(p + os.sep)


def same_volume(a: str, b: str) -> bool:
    return os.path.splitdrive(a)[0].lower() == os.path.splitdrive(b)[0].lower()


def free_name(folder: str, name: str, is_dir: bool, copy_style: bool = False, force_new=False) -> str:
    """A path in folder that doesn't exist yet: name, name (2), ... or name - Copy, name - Copy (2), ..."""
    stem, ext = (name, "") if is_dir else os.path.splitext(name)
    if not force_new and not copy_style:
        p = os.path.join(folder, name)
        if not os.path.lexists(p):
            return p
    for i in itertools.count(1):
        if copy_style:
            cand = f"{stem} - Copy{ext}" if i == 1 else f"{stem} - Copy ({i}){ext}"
        else:
            cand = f"{stem} ({i + 1}){ext}"
        p = os.path.join(folder, cand)
        if not os.path.lexists(p):
            return p
    return ""


def decide_move(modifiers, sources: list[str], target: str) -> bool:
    """Explorer rules: Ctrl copies, Shift moves, otherwise move within a drive and copy across drives."""
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        return False
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        return True
    return bool(sources) and same_volume(sources[0], target)


# ----- clipboard (interoperates with Explorer) ----------------------------
def set_clipboard_paths(paths: list[str], cut: bool):
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(p) for p in paths])
    md.setData(DROPEFFECT_MIME, QByteArray(struct.pack("<I", 2 if cut else 5)))
    QGuiApplication.clipboard().setMimeData(md)


def clipboard_paths() -> tuple[list[str], bool]:
    md = QGuiApplication.clipboard().mimeData()
    if md is None or not md.hasUrls():
        return [], False
    paths = [os.path.normpath(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]
    cut = False
    if md.hasFormat(DROPEFFECT_MIME):
        raw = md.data(DROPEFFECT_MIME).data()
        if len(raw) >= 4:
            cut = bool(struct.unpack("<I", raw[:4])[0] & 2)
    return paths, cut


# ----- shell ----------------------------------------------------------------
if WIN:
    from ctypes import wintypes

    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", ctypes.c_void_p), ("pTo", ctypes.c_void_p),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
                    ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    _shell32 = ctypes.windll.shell32
    _shell32.SHFileOperationW.argtypes = [ctypes.POINTER(_SHFILEOPSTRUCTW)]
    _shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]


def delete_paths(paths: list[str], hwnd: int = 0, permanent: bool = False) -> tuple[bool, bool]:
    """Send to the Recycle Bin (or delete permanently). Returns (succeeded, user_aborted)."""
    paths = [os.path.abspath(p) for p in paths]
    if not WIN:
        for p in paths:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        return True, False
    joined = "\0".join(paths) + "\0\0"
    buf = ctypes.create_unicode_buffer(joined, len(joined) + 1)
    op = _SHFILEOPSTRUCTW()
    op.hwnd = hwnd
    op.wFunc = 3                       # FO_DELETE
    op.pFrom = ctypes.addressof(buf)
    op.pTo = None
    flags = 0x10                       # FOF_NOCONFIRMATION (we already asked)
    if not permanent:
        flags |= 0x40 | 0x4000         # FOF_ALLOWUNDO | FOF_WANTNUKEWARNING
    op.fFlags = flags
    res = _shell32.SHFileOperationW(ctypes.byref(op))
    return res == 0, bool(op.fAnyOperationsAborted)


def show_properties(path: str, hwnd: int = 0) -> bool:
    if not WIN:
        return False
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x0000000C            # SEE_MASK_INVOKEIDLIST
    info.hwnd = hwnd
    info.lpVerb = "properties"
    info.lpFile = path
    info.nShow = 1
    return bool(_shell32.ShellExecuteExW(ctypes.byref(info)))


def open_path(path: str):
    if WIN:
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])


def open_with_dialog(path: str):
    subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", path])


def show_in_explorer(path: str, select: bool):
    if select:
        subprocess.Popen(f'explorer.exe /select,"{path}"')
    else:
        subprocess.Popen(["explorer.exe", path])


def open_terminal(path: str):
    wt = shutil.which("wt")
    if wt:
        subprocess.Popen([wt, "-d", path])
    else:
        subprocess.Popen(["powershell.exe", "-NoExit"], cwd=path,
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


# ----- TeraCopy ---------------------------------------------------------------
def find_teracopy(configured: str = "") -> str:
    if configured and os.path.isfile(configured):
        return configured
    if WIN:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\TeraCopy.exe") as k:
                    p = winreg.QueryValue(k, None).strip('"')
                    if os.path.isfile(p):
                        return p
            except OSError:
                pass
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            p = os.path.join(base, "TeraCopy", "TeraCopy.exe")
            if os.path.isfile(p):
                return p
    return shutil.which("TeraCopy") or ""


def teracopy_transfer(exe: str, sources: list[str], target: str, move: bool, close_when_done: bool):
    """Hand a copy/move to TeraCopy. Its command line is: TeraCopy.exe Copy|Move <source> <target>,
    where <source> may be *"listfile" for several items."""
    op = "Move" if move else "Copy"
    tgt = target if target.endswith("\\") else target + "\\"
    if len(sources) == 1:
        src_arg = f'"{sources[0]}"'
    else:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        listfile = TEMP_DIR / f"teracopy-{uuid.uuid4().hex}.txt"
        listfile.write_text("\r\n".join(sources) + "\r\n", encoding="utf-16")
        src_arg = f'*"{listfile}"'
    # TeraCopy parses its command line without backslash escapes, so build it by hand.
    cmd = f'"{exe}" {op} {src_arg} "{tgt}"'
    if close_when_done:
        cmd += " /Close"
    subprocess.Popen(cmd, close_fds=True)


def cleanup_temp():
    """Remove TeraCopy file lists older than a day."""
    try:
        cutoff = time.time() - 86400
        for f in TEMP_DIR.glob("teracopy-*.txt"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except OSError:
        pass


# ----- built-in copier (runs in a worker thread) ------------------------
def _is_link(path_or_entry) -> bool:
    if isinstance(path_or_entry, os.DirEntry):
        return path_or_entry.is_symlink() or getattr(path_or_entry, "is_junction", lambda: False)()
    return os.path.islink(path_or_entry) or getattr(os.path, "isjunction", lambda p: False)(path_or_entry)


def _remove(path: str):
    if os.path.isdir(path) and not _is_link(path):
        shutil.rmtree(path, onexc=lambda fn, p, exc: (os.chmod(p, stat.S_IWRITE), fn(p)))
    else:
        if os.path.exists(path) and not os.access(path, os.W_OK):
            os.chmod(path, stat.S_IWRITE)
        os.remove(path)


class CopyJob(QObject):
    """plan: list of [source, destination, mode]; mode is "new" or "overwrite"."""
    progress = Signal(object, object, str)   # bytes done, bytes total, current name
    done = Signal(list, bool)                # errors, cancelled

    CHUNK = 4 * 1024 * 1024

    def __init__(self, plan, move: bool):
        super().__init__()
        self.plan, self.move = plan, move
        self.errors: list[str] = []
        self.total = 0
        self.copied = 0
        self._cancel = False
        self._last_emit = 0.0

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            self.total = sum(self._size(s) for s, d, _ in self.plan
                             if not (self.move and same_volume(s, d)))
            for src, dst, mode in self.plan:
                if self._cancel:
                    break
                try:
                    self._do(src, dst, mode)
                except OSError as ex:
                    self.errors.append(f"{src}: {ex.strerror or ex}")
            self.progress.emit(self.copied, self.total, "")
        finally:
            self.done.emit(self.errors, self._cancel)

    def _size(self, path: str) -> int:
        try:
            if not os.path.isdir(path) or _is_link(path):
                return os.path.getsize(path)
            total = 0
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not _is_link(os.path.join(root, d))]
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return total
        except OSError:
            return 0

    def _report(self, name: str, force=False):
        now = time.monotonic()
        if force or now - self._last_emit > 0.1:
            self._last_emit = now
            self.progress.emit(self.copied, self.total, name)

    def _do(self, src: str, dst: str, mode: str):
        src_is_dir = os.path.isdir(src) and not _is_link(src)
        if self.move and same_volume(src, dst):
            if mode == "overwrite" and os.path.lexists(dst):
                if src_is_dir and os.path.isdir(dst):
                    self._merge_rename(src, dst)
                    return
                _remove(dst)
            os.rename(src, dst)
            self._report(os.path.basename(src), True)
            return
        before = len(self.errors)
        if src_is_dir:
            if mode == "overwrite" and os.path.lexists(dst) and not os.path.isdir(dst):
                _remove(dst)
            self._copy_tree(src, dst)
            if self.move and not self._cancel and len(self.errors) == before:
                _remove(src)
        else:
            if mode == "overwrite" and os.path.isdir(dst):
                _remove(dst)
            self._copy_file(src, dst)
            if self.move and not self._cancel:
                _remove(src)

    def _merge_rename(self, src: str, dst: str):
        """Same-volume folder move onto an existing folder: merge, like Explorer."""
        with os.scandir(src) as it:
            items = list(it)
        for de in items:
            target = os.path.join(dst, de.name)
            try:
                if os.path.lexists(target):
                    if de.is_dir(follow_symlinks=False) and os.path.isdir(target):
                        self._merge_rename(de.path, target)
                        continue
                    _remove(target)
                os.rename(de.path, target)
            except OSError as ex:
                self.errors.append(f"{de.path}: {ex.strerror or ex}")
        try:
            os.rmdir(src)
        except OSError:
            pass

    def _copy_tree(self, src: str, dst: str):
        os.makedirs(dst, exist_ok=True)
        with os.scandir(src) as it:
            items = list(it)
        for de in items:
            if self._cancel:
                return
            d = os.path.join(dst, de.name)
            try:
                if _is_link(de):
                    self.errors.append(f"{de.path}: skipped (link or junction)")
                elif de.is_dir(follow_symlinks=False):
                    self._copy_tree(de.path, d)
                else:
                    if os.path.isdir(d):
                        _remove(d)
                    self._copy_file(de.path, d)
            except OSError as ex:
                self.errors.append(f"{de.path}: {ex.strerror or ex}")
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass

    def _copy_file(self, src: str, dst: str):
        name = os.path.basename(src)
        if os.path.exists(dst) and not os.access(dst, os.W_OK):
            os.chmod(dst, stat.S_IWRITE)
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            while True:
                if self._cancel:
                    break
                buf = fi.read(self.CHUNK)
                if not buf:
                    break
                fo.write(buf)
                self.copied += len(buf)
                self._report(name)
        if self._cancel:
            try:
                os.remove(dst)
            except OSError:
                pass
            return
        shutil.copystat(src, dst)
        self._report(name)
