"""Safely remove a USB stick, card or external disk, or open a DVD tray (what Explorer's "Eject"
does). Detection is one cheap IOCTL per local drive, cached; ejecting runs on a worker thread
because Windows may take a second or two to flush and power down the device."""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

WIN = sys.platform == "win32"

# STORAGE_BUS_TYPE values that mean "plugged in from outside"
_EXTERNAL_BUS = {4, 7, 12, 13}          # 1394, USB, SD, MMC
_cache: dict = {}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def make(cls, d1, d2, d3, d4):
        g = cls(d1, d2, d3)
        g.Data4[:] = d4
        return g


GUID_DISK = _GUID.make(0x53F56307, 0xB6BF, 0x11D0, (0x94, 0xF2, 0x00, 0xA0, 0xC9, 0x1E, 0xFB, 0x8B))
GUID_CDROM = _GUID.make(0x53F56308, 0xB6BF, 0x11D0, (0x94, 0xF2, 0x00, 0xA0, 0xC9, 0x1E, 0xFB, 0x8B))


class _DEVNUM(ctypes.Structure):
    _fields_ = [("DeviceType", wintypes.DWORD), ("DeviceNumber", wintypes.DWORD),
                ("PartitionNumber", wintypes.DWORD)]


class _IFDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", _GUID), ("Flags", wintypes.DWORD),
                ("Reserved", ctypes.c_size_t)]


class _DEVINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", _GUID), ("DevInst", wintypes.DWORD),
                ("Reserved", ctypes.c_size_t)]


if WIN:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                     wintypes.LPVOID]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
INVALID = ctypes.c_void_p(-1).value


def _open(path: str, access: int = 0):
    h = _k32.CreateFileW(path, access, 3, None, 3, 0, None)      # share read|write, OPEN_EXISTING
    return None if h in (None, INVALID) else h


def _ioctl(h, code, inbuf=None, outbuf=None) -> bool:
    ret = wintypes.DWORD()
    return bool(_k32.DeviceIoControl(h, code, ctypes.byref(inbuf) if inbuf is not None else None,
                                     ctypes.sizeof(inbuf) if inbuf is not None else 0,
                                     ctypes.byref(outbuf) if outbuf is not None else None,
                                     ctypes.sizeof(outbuf) if outbuf is not None else 0,
                                     ctypes.byref(ret), None))


def _bus_type(letter: str):
    """STORAGE_BUS_TYPE of the disk behind a drive letter (opening with no access rights
    doesn't wake a sleeping disk)."""
    h = _open(f"\\\\.\\{letter}")
    if h is None:
        return None
    try:
        query = (ctypes.c_ubyte * 12)()                  # StorageDeviceProperty, PropertyStandardQuery
        out = (ctypes.c_ubyte * 1024)()
        if not _ioctl(h, 0x2D1400, query, out):          # IOCTL_STORAGE_QUERY_PROPERTY
            return None
        return int.from_bytes(bytes(out[28:32]), "little")   # STORAGE_DEVICE_DESCRIPTOR.BusType
    finally:
        _k32.CloseHandle(h)


def can_eject(info: dict) -> bool:
    """Show an eject button for this drive? USB sticks, cards, DVDs and external disks."""
    if not WIN or info.get("kind") == "network":
        return False
    letter = info["letter"].upper()
    if letter == os.environ.get("SystemDrive", "C:").upper():
        return False
    if info.get("kind") in ("removable", "optical"):
        return True
    key = (letter, info.get("total"))
    if key not in _cache:
        _cache[key] = _bus_type(letter) in _EXTERNAL_BUS
    return _cache[key]


def _device_number(path: str):
    h = _open(path)
    if h is None:
        return None
    try:
        n = _DEVNUM()
        return (n.DeviceType, n.DeviceNumber) if _ioctl(h, 0x2D1080, None, n) else None   # GET_DEVICE_NUMBER
    finally:
        _k32.CloseHandle(h)


def _devinst_for(number, guid) -> int | None:
    sa = ctypes.WinDLL("setupapi", use_last_error=True)
    sa.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    sa.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    sa.SetupDiEnumDeviceInterfaces.argtypes = [wintypes.HANDLE, wintypes.LPVOID, ctypes.POINTER(_GUID),
                                               wintypes.DWORD, ctypes.POINTER(_IFDATA)]
    sa.SetupDiGetDeviceInterfaceDetailW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IFDATA), wintypes.LPVOID,
                                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                                    ctypes.POINTER(_DEVINFO)]
    sa.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    hdev = sa.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x12)   # PRESENT | DEVICEINTERFACE
    if hdev in (None, INVALID):
        return None
    try:
        i = 0
        while True:
            ifd = _IFDATA(cbSize=ctypes.sizeof(_IFDATA))
            if not sa.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid), i, ctypes.byref(ifd)):
                return None
            i += 1
            need = wintypes.DWORD()
            sa.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), None, 0, ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value + 8)
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            dev = _DEVINFO(cbSize=ctypes.sizeof(_DEVINFO))
            if not sa.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), buf, need.value + 8, None,
                                                       ctypes.byref(dev)):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            if _device_number(path) == number:
                return dev.DevInst
    finally:
        sa.SetupDiDestroyDeviceInfoList(hdev)


def eject(letter: str) -> str | None:
    """Eject a drive. Returns None when done, or a message saying why it couldn't.
    Blocking (up to a few seconds): call through places.run_async."""
    letter = letter.rstrip("\\").upper()
    root = letter + "\\"
    dtype = _k32.GetDriveTypeW(root)
    if dtype == 5:                                       # DVD/CD: open the tray / unmount the image
        h = _open(f"\\\\.\\{letter}", 0x80000000)        # GENERIC_READ
        if h is None:
            return "Windows couldn't open the drive."
        try:
            return None if _ioctl(h, 0x2D4808) else "The disc couldn't be ejected."   # IOCTL_STORAGE_EJECT_MEDIA
        finally:
            _k32.CloseHandle(h)
    num = _device_number(f"\\\\.\\{letter}")
    if num is None:
        return "Windows couldn't find this drive."
    inst = _devinst_for(num, GUID_DISK)
    if inst is None:
        return "Windows couldn't find the device for this drive."
    cfg = ctypes.WinDLL("cfgmgr32")
    parent = wintypes.DWORD()
    if cfg.CM_Get_Parent(ctypes.byref(parent), wintypes.DWORD(inst), 0) != 0:
        return "Windows couldn't find the device for this drive."
    veto_type = ctypes.c_int()
    veto = ctypes.create_unicode_buffer(260)
    for _ in range(3):                                   # Windows sometimes needs a second try
        rc = cfg.CM_Request_Device_EjectW(parent, ctypes.byref(veto_type), veto, 260, 0)
        if rc == 0 and veto_type.value == 0:
            return None
    return ("It's in use. Close the files and windows that use it, then try again."
            if veto_type.value else f"Windows couldn't eject it (error {rc}).")
