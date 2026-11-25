"""List what's inside an archive, for the inspector. Runs on a worker thread.

.zip-style files are read directly with Python's zipfile (fast, no process). Everything else
goes through 7-Zip's command line when available (7-Zip or NanaZip: rar/RAR5, 7z, iso, cab,
tar.*, wim...), otherwise through Windows' built-in tar.exe (libarchive). Nothing is extracted
and a password is never prompted for.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from datetime import datetime

ARCHIVE_EXTS = set("""zip 7z rar tar gz tgz bz2 tbz tbz2 xz txz zst tzst lz lzma iso cab wim
jar apk cbz cbr cb7 001 arj lzh lha z taz msi""".split())
ZIP_EXTS = {"zip", "jar", "apk", "cbz"}
TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tbz2", ".tar.xz", ".txz", ".tar.zst", ".tzst",
                ".tar.lz", ".taz")   # 7-Zip would only show the inner .tar for these
LIMIT = 5000           # entries kept for display; totals still count everything
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_7z() -> str | None:
    for cand in ("7z", "7z.exe", "NanaZipC.exe"):
        p = shutil.which(cand)
        if p:
            return p
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            p = os.path.join(base, "7-Zip", "7z.exe")
            if os.path.isfile(p):
                return p
    return None


def _result(kind, tool):
    return {"kind": kind, "tool": tool, "entries": [], "files": 0, "dirs": 0, "size": 0,
            "packed": None, "encrypted": False, "truncated": False, "error": ""}


def _add(res, name, size, is_dir, mtime):
    name = name.replace("\\", "/").rstrip("/")
    if not name:
        return
    if is_dir:
        res["dirs"] += 1
    else:
        res["files"] += 1
        res["size"] += size or 0
    if len(res["entries"]) < LIMIT:
        res["entries"].append((name, size, is_dir, mtime))
    else:
        res["truncated"] = True


def _list_zip(path):
    res = _result("ZIP archive", "built-in")
    with zipfile.ZipFile(path) as z:
        packed = 0
        for info in z.infolist():
            is_dir = info.is_dir()
            try:
                mt = datetime(*info.date_time).timestamp()
            except (ValueError, OverflowError):
                mt = None
            _add(res, info.filename, info.file_size, is_dir, mt)
            packed += info.compress_size
            if info.flag_bits & 0x1:
                res["encrypted"] = True
        res["packed"] = packed
    return res


def _list_7z(path, exe):
    # -slt: one "Key = value" block per item; -p...: never wait for a password
    cmd = [exe, "l", "-slt", "-sccUTF-8", "-pBURROW_NO_PASSWORD", "--", path]
    proc = subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                          creationflags=NO_WINDOW)
    out = proc.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    err_text = proc.stderr.decode("utf-8", errors="replace")
    res = _result("Archive", os.path.basename(exe))
    blocks = out.split("\n----------\n", 1)
    head, body = (blocks[0], blocks[1]) if len(blocks) == 2 else (out, "")
    m = re.search(r"^Type = (.+)$", head, re.M)
    if m:
        res["kind"] = f"{m.group(1).strip().upper()} archive"
    if "Encrypted = +" in out or "Wrong password" in err_text or "encrypted archive" in (out + err_text).lower():
        res["encrypted"] = True
    item = {}

    def flush():
        if "Path" in item:
            size = int(item.get("Size") or 0) if (item.get("Size") or "").isdigit() else 0
            is_dir = item.get("Folder") == "+" or "D" in item.get("Attributes", "")[:1]
            mt = None
            if item.get("Modified"):
                try:
                    mt = datetime.strptime(item["Modified"][:19], "%Y-%m-%d %H:%M:%S").timestamp()
                except ValueError:
                    pass
            _add(res, item["Path"], size, is_dir, mt)
            ps = item.get("Packed Size", "")
            if ps.isdigit():
                res["packed"] = (res["packed"] or 0) + int(ps)
    for line in body.splitlines():
        if not line.strip():
            flush()
            item = {}
            continue
        if " = " in line:
            k, v = line.split(" = ", 1)
            item[k.strip()] = v.strip()
    flush()
    if not res["files"] and not res["dirs"] and proc.returncode != 0:
        err = err_text.strip().splitlines()
        res["error"] = "Encrypted archive: the file list is password-protected." if res["encrypted"] \
            else (err[-1] if err else "Can't read this archive.")
    return res


_tar_line = re.compile(r"^(\S{10})\s+\S+\s+\S+\s+\S+\s+(\d+)\s+(\w{3}\s+\d+\s+[\d:]+)\s+(.+)$")


def _list_tar(path):
    tar = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe")
    proc = subprocess.run([tar, "-tvf", path], capture_output=True, timeout=30,
                          stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    res = _result("Archive", "tar")
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        m = _tar_line.match(line)
        if m:
            _add(res, m.group(4), int(m.group(2)), m.group(1).startswith("d"), None)
    if not res["files"] and not res["dirs"]:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        res["error"] = err.splitlines()[-1] if err else "Can't read this archive."
    return res


def list_archive(path: str) -> dict:
    ext = os.path.splitext(path)[1][1:].lower()
    try:
        if ext in ZIP_EXTS:
            try:
                return _list_zip(path)
            except zipfile.BadZipFile:
                pass                      # maybe another format with a .zip name: try 7-Zip
        if path.lower().endswith(TAR_SUFFIXES):
            return _list_tar(path)
        exe = find_7z()
        if exe:
            return _list_7z(path, exe)
        return _list_tar(path)
    except subprocess.TimeoutExpired:
        r = _result("Archive", "")
        r["error"] = "Reading this archive took too long."
        return r
    except OSError as ex:
        r = _result("Archive", "")
        r["error"] = ex.strerror or str(ex)
        return r
