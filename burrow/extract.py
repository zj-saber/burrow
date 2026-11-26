"""Extract here / Extract to folder / Add to archive, through whichever archiver is installed.

7-Zip or NanaZip first (their GUI shows progress and asks about passwords and overwrites),
then WinRAR, then Windows' own tar.exe (no window; Burrow runs it in the background). Burrow
only starts the tool and doesn't wait for it, so a big archive never holds up the window.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from .archive import ARCHIVE_EXTS, TAR_SUFFIXES

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EXTRACTABLE = ARCHIVE_EXTS - {"msi", "apk", "jar"}
_tool_cache: list = []


def _first(names, folders=()):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        for folder, exe in folders:
            p = os.path.join(base or "", folder, exe)
            if base and os.path.isfile(p):
                return p
    return None


def tool() -> tuple[str, str] | None:
    """(kind, exe): kind is "7z" (7-Zip / NanaZip GUI), "winrar" or "tar"."""
    if not _tool_cache:
        p = _first(["7zG.exe", "NanaZipG.exe"], [("7-Zip", "7zG.exe"), ("NanaZip", "NanaZipG.exe")])
        if p:
            _tool_cache.append(("7z", p))
        else:
            p = _first(["WinRAR.exe"], [("WinRAR", "WinRAR.exe")])
            if p:
                _tool_cache.append(("winrar", p))
            else:
                p = _first(["tar.exe"])
                _tool_cache.append(("tar", p) if p else None)
    return _tool_cache[0]


def tool_name() -> str:
    t = tool()
    if t is None:
        return ""
    exe = os.path.basename(t[1]).lower()
    return {"winrar": "WinRAR", "tar": "Windows"}.get(t[0], "NanaZip" if "nanazip" in exe
                                                     or "windowsapps" in t[1].lower() else "7-Zip")


def is_archive(path: str) -> bool:
    n = path.lower()
    return not os.path.isdir(path) and (n.rsplit(".", 1)[-1] in EXTRACTABLE or n.endswith(TAR_SUFFIXES))


def folder_name(archive: str) -> str:
    """photos.zip -> photos, backup.tar.gz -> backup, disk.part1.rar -> disk."""
    name = os.path.basename(archive)
    low = name.lower()
    for suf in TAR_SUFFIXES:
        if low.endswith(suf):
            return name[:-len(suf)] or name
    base = os.path.splitext(name)[0]
    if base.lower().rsplit(".", 1)[-1].startswith("part") and "." in base:
        base = base.rsplit(".", 1)[0]
    return base or name


def _start(cmd, cwd):
    subprocess.Popen(cmd, cwd=cwd, close_fds=True, stdin=subprocess.DEVNULL)


def extract(archives: list[str], to_folder: bool, run_hidden) -> str | None:
    """Start extracting. to_folder: each archive into a folder named after it. run_hidden(fn)
    runs a blocking job off the GUI thread (for tar). Returns an error message or None."""
    t = tool()
    if t is None:
        return "No archiver found. Install NanaZip, 7-Zip or WinRAR."
    kind, exe = t
    parent = os.path.dirname(archives[0])
    if kind == "7z":
        out = os.path.join(parent, "*") if to_folder else parent       # 7-Zip: * = the archive's name
        # -an -ai!: several archives in one go (a 2nd plain name would be read as a filter);
        # -snz: extracted files inherit the archive's "downloaded from the internet" mark, like Explorer
        _start([exe, "x", "-an", *[f"-ai!{os.path.basename(x)}" for x in archives], "-snz", f"-o{out}"], parent)
    elif kind == "winrar":
        for a in archives:
            dest = os.path.join(parent, folder_name(a)) if to_folder else parent
            _start([exe, "x", a, dest + os.sep], parent)
    else:
        def job():
            errors = []
            for a in archives:
                dest = os.path.join(parent, folder_name(a)) if to_folder else parent
                os.makedirs(dest, exist_ok=True)
                r = subprocess.run([exe, "-xf", a, "-C", dest], capture_output=True, text=True,
                                   creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
                if r.returncode:
                    errors.append(f"{os.path.basename(a)}: {(r.stderr or '').strip()[:200]}")
                else:
                    _propagate_zone(a, dest, exe)
            return errors
        run_hidden(job)
    return None


def _propagate_zone(archive: str, dest: str, tar: str):
    """tar.exe doesn't copy the Mark-of-the-Web: give each extracted file the archive's
    Zone.Identifier, so Windows still warns before running something that came from the internet."""
    try:
        with open(archive + ":Zone.Identifier", "rb") as f:
            zone = f.read(4096)
    except OSError:
        return                                          # not downloaded: nothing to pass on
    r = subprocess.run([tar, "-tf", archive], capture_output=True, text=True, creationflags=NO_WINDOW,
                       stdin=subprocess.DEVNULL)
    base = os.path.realpath(dest)
    for name in r.stdout.splitlines():
        p = os.path.realpath(os.path.join(dest, name))
        if p.startswith(base + os.sep) and os.path.isfile(p):
            try:
                with open(p + ":Zone.Identifier", "wb") as f:
                    f.write(zone)
            except OSError:
                pass


def archive_name(paths: list[str]) -> str:
    """Name for Add to archive: the item's own name, or the folder's name for several."""
    if len(paths) == 1:
        p = paths[0]
        return os.path.basename(p) if os.path.isdir(p) else os.path.splitext(os.path.basename(p))[0]
    return os.path.basename(os.path.dirname(paths[0])) or "Archive"


def compress(paths: list[str], run_hidden) -> str | None:
    """Add to archive. 7-Zip/NanaZip open their Add dialog (format, level, password) with a
    .zip name filled in; WinRAR and tar make the .zip directly."""
    t = tool()
    if t is None:
        return "No archiver found. Install NanaZip, 7-Zip or WinRAR."
    from .fileops import free_name
    kind, exe = t
    parent = os.path.dirname(paths[0])
    target = free_name(parent, archive_name(paths) + ".zip", False)
    names = [os.path.basename(p) for p in paths]
    if kind == "7z":
        _start([exe, "a", "-ad", "-tzip", target, "--", *names], parent)
    elif kind == "winrar":
        _start([exe, "a", "-afzip", "-ep1", target, "--", *paths], parent)
    else:
        def job():
            r = subprocess.run([exe, "-a", "-cf", target, *names], cwd=parent, capture_output=True, text=True,
                               creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
            return [f"{(r.stderr or '').strip()[:200]}"] if r.returncode else []
        run_hidden(job)
    return None
