"""Drives, network locations and cloud folders.

Everything that runs on the GUI thread here is local-only: drive letters, drive types and the
remembered network mappings come from fast Windows calls that never wait on the network.
Anything that can touch the network (listing a share, free space of a network drive) goes
through run_async().
"""
from __future__ import annotations

import ctypes
import os
import re
import string
import sys
import threading

from PySide6.QtCore import QObject, Signal, Slot

WIN = sys.platform == "win32"
DRIVES = "::drives"      # "This PC"
NETWORK = "::network"    # mapped drives + recent servers

RECENT_SERVERS: list[str] = []     # kept in sync with the config by the main window

_server_re = re.compile(r"^\\\\([^\\/]+)[\\/]?$")


# ----- background work --------------------------------------------------------------
class _Bridge(QObject):
    done = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.done.connect(self._deliver)

    @Slot(object, object)
    def _deliver(self, callback, result):
        try:
            callback(result)
        except RuntimeError:          # the widget that asked was closed meanwhile
            pass


_bridge: _Bridge | None = None


def run_async(fn, callback):
    """Run fn() on a daemon thread and call callback(result) on the GUI thread.
    Exceptions are passed to the callback as the result. Daemon threads never block exit,
    even if a network call hangs."""
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()

    def work():
        try:
            res = fn()
        except Exception as ex:          # noqa: BLE001 - handed to the callback
            res = ex
        _bridge.done.emit(callback, res)

    threading.Thread(target=work, daemon=True).start()


# ----- path classification ------------------------------------------------------------
def is_special(path: str) -> bool:
    return path in (DRIVES, NETWORK)


def is_server(path: str) -> bool:
    return bool(path) and bool(_server_re.match(path))


def is_unc(path: str) -> bool:
    return bool(path) and path.startswith("\\\\")


def server_of(path: str) -> str | None:
    if not is_unc(path):
        return None
    parts = path[2:].split("\\")
    return parts[0] if parts and parts[0] else None


def drive_type(root: str) -> int:
    if not WIN:
        return 3
    return ctypes.windll.kernel32.GetDriveTypeW(root)


def is_remote(path: str) -> bool:
    """True for UNC paths and mapped network drives (decided locally, no network I/O)."""
    if not path or is_special(path):
        return False
    if is_unc(path):
        return True
    if re.match(r"^[A-Za-z]:", path):
        return drive_type(path[:2] + "\\") == 4
    return False


# ----- Windows APIs -------------------------------------------------------------------
if WIN:
    from ctypes import wintypes
    _k32 = ctypes.windll.kernel32
    _mpr = ctypes.windll.mpr
    try:
        _k32.SetThreadErrorMode(1, None)     # no "insert a disk" popups on empty drives
    except (AttributeError, OSError):
        pass

    class NETRESOURCEW(ctypes.Structure):
        _fields_ = [("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD),
                    ("dwDisplayType", wintypes.DWORD), ("dwUsage", wintypes.DWORD),
                    ("lpLocalName", wintypes.LPWSTR), ("lpRemoteName", wintypes.LPWSTR),
                    ("lpComment", wintypes.LPWSTR), ("lpProvider", wintypes.LPWSTR)]

    class SHARE_INFO_1(ctypes.Structure):
        _fields_ = [("shi1_netname", wintypes.LPWSTR), ("shi1_type", wintypes.DWORD),
                    ("shi1_remark", wintypes.LPWSTR)]


def _volume_label(root: str) -> str:
    buf = ctypes.create_unicode_buffer(261)
    if _k32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0):
        return buf.value
    return ""


def _disk_space(root: str):
    free, total, tfree = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
    if _k32.GetDiskFreeSpaceExW(root, ctypes.byref(free), ctypes.byref(total), ctypes.byref(tfree)):
        return total.value, free.value
    return None, None


def _net_connection(letter: str) -> tuple[str, int]:
    """Remote path of a mapped drive from Windows' local cache. Returns (remote, status):
    status 0 = connected, 1201 = remembered but unavailable, other = not a network drive."""
    buf = ctypes.create_unicode_buffer(1024)
    n = wintypes.DWORD(1024)
    rc = _mpr.WNetGetConnectionW(letter, buf, ctypes.byref(n))
    return (buf.value if rc in (0, 1201) else ""), rc


def _remembered_mappings() -> dict[str, str]:
    import winreg
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Network") as k:
            i = 0
            while True:
                try:
                    letter = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, letter) as sub:
                        out[letter.upper() + ":"] = winreg.QueryValueEx(sub, "RemotePath")[0]
                except OSError:
                    pass
    except OSError:
        pass
    return out


def drive_infos() -> list[dict]:
    """All drive letters, fast. Network drives are described from the local mapping cache
    (no I/O to the server); local drives get label and free space."""
    if not WIN:
        return []
    mask = _k32.GetLogicalDrives()
    letters = [f"{c}:" for i, c in enumerate(string.ascii_uppercase) if mask & (1 << i)]
    remembered = _remembered_mappings()
    for letter in remembered:
        if letter not in letters:
            letters.append(letter)
    out = []
    for letter in sorted(letters):
        root = letter + "\\"
        dtype = _k32.GetDriveTypeW(root)
        info = {"letter": letter, "root": root, "kind": "local", "label": "", "total": None,
                "free": None, "remote": "", "connected": True}
        if dtype == 4 or letter in remembered:
            remote, rc = _net_connection(letter)
            info.update(kind="network", remote=remote or remembered.get(letter, ""),
                        connected=(rc == 0))
        elif dtype in (2, 5):
            info["kind"] = "removable" if dtype == 2 else "optical"
        if info["kind"] != "network":
            info["label"] = _volume_label(root)
            info["total"], info["free"] = _disk_space(root)
            if info["label"].lower() == "google drive":
                info["kind"] = "cloud"
        out.append(info)
    return out


def drive_display_name(info: dict) -> str:
    letter = info["letter"]
    if info["kind"] == "network":
        remote = info["remote"]
        m = re.match(r"^\\\\([^\\]+)\\(.+)$", remote)
        return f"{m.group(2)} ({m.group(1)}) ({letter})" if m else f"Network drive ({letter})"
    label = info["label"] or {"removable": "USB Drive", "optical": "DVD Drive"}.get(info["kind"], "Local Disk")
    return f"{label} ({letter})"


def reconnect(letter: str, hwnd: int = 0) -> bool:
    """Reconnect a remembered network drive; Windows asks for credentials if it needs them."""
    remote = _remembered_mappings().get(letter.upper())
    if not WIN or not remote:
        return False
    nr = NETRESOURCEW()
    nr.dwType = 1                                   # RESOURCETYPE_DISK
    nr.lpLocalName = letter.upper()
    nr.lpRemoteName = remote
    return _mpr.WNetAddConnection3W(hwnd, ctypes.byref(nr), None, None, 0x8) == 0   # CONNECT_INTERACTIVE


def needs_reconnect(path: str) -> bool:
    if not WIN or not re.match(r"^[A-Za-z]:", path or ""):
        return False
    letter = path[:2].upper()
    if letter not in _remembered_mappings():
        return False
    return _net_connection(letter)[1] != 0


def map_drive_dialog(hwnd: int = 0):
    if WIN:
        _mpr.WNetConnectionDialog(hwnd, 1)


def disconnect_dialog(hwnd: int = 0):
    if WIN:
        _mpr.WNetDisconnectDialog(hwnd, 1)


def disconnect(letter: str) -> bool:
    if not WIN:
        return False
    return _mpr.WNetCancelConnection2W(letter.upper(), 1, False) == 0     # CONNECT_UPDATE_PROFILE


def list_shares(server: str) -> list[str]:
    """Disk shares of a server (\\\\server). Talks to the network: call through run_async."""
    if not WIN:
        return []
    net = ctypes.windll.netapi32
    buf = ctypes.c_void_p()
    read, total, resume = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    rc = net.NetShareEnum(server, 1, ctypes.byref(buf), 0xFFFFFFFF, ctypes.byref(read),
                          ctypes.byref(total), ctypes.byref(resume))
    if rc not in (0, 234):                       # NERR_Success, ERROR_MORE_DATA
        raise OSError(rc, ctypes.FormatError(rc).strip() or f"Can't list shares (error {rc})")
    try:
        arr = ctypes.cast(buf, ctypes.POINTER(SHARE_INFO_1))
        names = []
        for i in range(read.value):
            s = arr[i]
            if (s.shi1_type & 0xFF) == 0 and not (s.shi1_type & 0x80000000):   # disk, not hidden admin
                names.append(s.shi1_netname)
        return sorted(names, key=str.lower)
    finally:
        net.NetApiBufferFree(buf)


# ----- cloud folders ----------------------------------------------------------------------
def _indirect(s: str) -> str:
    if not WIN or not s.startswith("@"):
        return s
    out = ctypes.create_unicode_buffer(512)
    if ctypes.windll.shlwapi.SHLoadIndirectString(s, out, 512, None) == 0:
        return out.value
    return ""


_COMMON_CLOUD = [("iCloud Drive", "iCloudDrive"), ("Dropbox", "Dropbox"), ("Google Drive", "Google Drive"),
                 ("Google Drive", "My Drive"), ("Box", "Box"), ("MEGA", "MEGA"), ("pCloud", "pCloud Drive"),
                 ("Nextcloud", "Nextcloud"), ("Sync", "Sync")]


def cloud_folders() -> list[tuple[str, str]]:
    """(name, path) of cloud-synced folders: Windows sync roots (OneDrive, iCloud, Dropbox,
    Box, Google Drive...), OneDrive's environment variables, and common folder names."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    home = os.path.normcase(os.path.expanduser("~"))
    users_dir = os.path.normcase(os.path.dirname(home))

    def add(name, path):
        if not path:
            return
        path = os.path.normpath(path)
        key = os.path.normcase(path)
        # skip other users' sync roots
        if key.startswith(users_dir + os.sep) and not (key == home or key.startswith(home + os.sep)):
            return
        if key in seen or not os.path.isdir(path):
            return
        seen.add(key)
        out.append((name, path))

    if WIN:
        import winreg
        roots = []
        try:
            base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(k, sub) as sk:
                            try:
                                name = _indirect(winreg.QueryValueEx(sk, "DisplayNameResource")[0])
                            except OSError:
                                name = ""
                            name = name or sub.split("!")[0]
                            with winreg.OpenKey(sk, "UserSyncRoots") as uk:
                                j = 0
                                while True:
                                    try:
                                        _, path, _ = winreg.EnumValue(uk, j)
                                    except OSError:
                                        break
                                    j += 1
                                    roots.append((name, path))
                    except OSError:
                        pass
        except OSError:
            pass
        for name, path in sorted(roots, key=lambda r: r[0].lower()):
            add(name, path)
    for var, name in (("OneDriveCommercial", "OneDrive - Work"), ("OneDriveConsumer", "OneDrive"),
                      ("OneDrive", "OneDrive")):
        add(name, os.environ.get(var))
    for name, folder in _COMMON_CLOUD:
        add(name, os.path.join(os.path.expanduser("~"), folder))
    return out


def remember_server(path: str):
    s = server_of(path)
    if not s:
        return False
    s = "\\\\" + s
    if s in RECENT_SERVERS:
        if RECENT_SERVERS[0] == s:
            return False
        RECENT_SERVERS.remove(s)
    RECENT_SERVERS.insert(0, s)
    del RECENT_SERVERS[8:]
    return True
