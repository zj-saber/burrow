"""Windows integration: Burrow as the default file manager, and a single running instance.

Default file manager (per user, HKCU only, fully reversible):
  - Directory and Drive get a default "Open in Burrow" verb, so opening a folder anywhere
    that goes through the shell (desktop, Explorer, Run box, folder shortcuts, apps that
    "open" a folder path) opens Burrow.
  - The "This PC" shell object's open-new-window command points to Burrow, which is what
    Win+E launches.
What can't be redirected: the Open/Save/Browse dialogs inside other apps and browsers (drawn by
Windows inside each app's process) and apps that start explorer.exe directly ("Show in folder").

Single instance: a second launch hands its paths to the running window over a local socket
and exits, so folders open as new tabs instead of new windows.
"""
from __future__ import annotations

import ctypes
import getpass
import json
import os
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

WIN = sys.platform == "win32"
SERVER_NAME = f"Burrow-{getpass.getuser()}"
VERB = "openinburrow"
THIS_PC_CLSID = "{52205fd8-5dfb-447d-801a-d0b52f2e83e1}"


# ----- launch command ---------------------------------------------------------------------
def launch_command() -> str:
    """How Windows should start Burrow: the installed exe, or pythonw + Burrow.pyw from source."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Burrow.pyw")
    return f'"{pyw if os.path.isfile(pyw) else exe}" "{script}"'


def temporary_location() -> bool:
    """Burrow is running from a place that won't last (inside a zip, %TEMP%). Registering it
    there would leave Windows pointing at a missing program ("Application not found" when any
    folder, OneDrive's included, is opened), so it isn't allowed."""
    if not getattr(sys, "frozen", False):
        return False
    import tempfile
    here = os.path.normcase(os.path.realpath(sys.executable))
    tmp = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
    return here.startswith(tmp + os.sep) or ".zip" + os.sep in here


TEMP_MESSAGE = ("Burrow is running from a temporary place (inside a zip or the Temp folder), so Windows can't be "
                "told to open folders with it: once that copy is gone, every folder would fail to open. Install "
                "Burrow first (install.ps1, or unzip it to a permanent folder), then turn this on there.")


def registered_command(kind: str) -> str | None:
    """The command Windows has for Burrow (kind: "folders" or "win_e"), or None."""
    if not WIN:
        return None
    winreg = _reg()
    path = (rf"Software\Classes\Directory\shell\{VERB}\command" if kind == "folders"
            else rf"Software\Classes\CLSID\{THIS_PC_CLSID}\shell\opennewwindow\command")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, "")[0]
    except OSError:
        return None


def repair() -> list[str]:
    """At start: if Windows opens folders (or Win+E) with a Burrow that is no longer where it
    was registered (moved, reinstalled, updated elsewhere), point it at this one. If this copy
    is a temporary one, take the registration away instead, so folders open in Explorer again.
    Returns what was fixed."""
    fixed = []
    if not WIN or not getattr(sys, "frozen", False):
        return fixed
    want = launch_command()
    for kind, on, setter in (("folders", is_default(), set_default), ("win_e", is_win_e(), set_win_e)):
        cmd = registered_command(kind)
        if not on or not cmd:
            continue
        if temporary_location():
            setter(False)
            fixed.append(f"{kind}: removed (temporary copy)")
        elif not cmd.startswith(want):
            setter(True)
            fixed.append(f"{kind}: now points at {sys.executable}")
    return fixed


# ----- default file manager ----------------------------------------------------------------
def _reg():
    import winreg
    return winreg


def is_default() -> bool:
    if not WIN:
        return False
    winreg = _reg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\Directory\shell") as k:
            return winreg.QueryValueEx(k, "")[0] == VERB
    except OSError:
        return False


def _set_value(path: str, name: str, value: str):
    winreg = _reg()
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def _delete_tree(path: str):
    """Delete a registry key under HKCU with everything below it."""
    winreg = _reg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(path + "\\" + sub)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def _notify_assoc_changed():
    if WIN:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # SHCNE_ASSOCCHANGED


def set_default(enable: bool) -> str | None:
    """Register (or unregister) Burrow as the default file manager. Returns an error or None."""
    if not WIN:
        return "Only available on Windows."
    if enable and temporary_location():
        return TEMP_MESSAGE
    winreg = _reg()
    cmd = launch_command()
    try:
        for cls in ("Directory", "Drive"):
            base = rf"Software\Classes\{cls}\shell"
            if enable:
                _set_value(base + "\\" + VERB, "", "Open in Burrow")
                _set_value(base + "\\" + VERB + r"\command", "", f'{cmd} "%V"')
                _set_value(base, "", VERB)
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base, 0, winreg.KEY_ALL_ACCESS) as k:
                        if winreg.QueryValueEx(k, "")[0] == VERB:
                            winreg.DeleteValue(k, "")
                except OSError:
                    pass
                _delete_tree(base + "\\" + VERB)
        _notify_assoc_changed()
        return None
    except OSError as ex:
        return str(ex)


def is_win_e() -> bool:
    if not WIN:
        return False
    winreg = _reg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf"Software\Classes\CLSID\{THIS_PC_CLSID}\shell\opennewwindow\command") as k:
            return "burrow" in winreg.QueryValueEx(k, "")[0].lower()
    except OSError:
        return False


def set_win_e(enable: bool) -> str | None:
    """Win+E opens Burrow (the same per-user override the Files app uses). Separate from
    opening folders in Burrow."""
    if not WIN:
        return "Only available on Windows."
    if enable and temporary_location():
        return TEMP_MESSAGE
    try:
        key = rf"Software\Classes\CLSID\{THIS_PC_CLSID}\shell\opennewwindow\command"
        if enable:
            _set_value(key, "", launch_command())
            _set_value(key, "DelegateExecute", "")
        else:
            _delete_tree(rf"Software\Classes\CLSID\{THIS_PC_CLSID}")
        _notify_assoc_changed()
        return None
    except OSError as ex:
        return str(ex)


# ----- Explorer Quick Access ------------------------------------------------------------------
QUICK_ACCESS = "shell:::{679f85cb-0220-4080-b29b-5540cc05aab6}"


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _run_ps(script: str):
    import subprocess
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def pin_to_quick_access(path: str):
    """Pin a folder in Explorer's Quick Access (no-op if already pinned). Slow-ish: run off-thread."""
    _run_ps(f"$f=(New-Object -ComObject Shell.Application).Namespace({_ps_quote(path)}); "
            f"if ($f) {{ $f.Self.InvokeVerb('pintohome') }}")


def unpin_from_quick_access(path: str):
    _run_ps(f"$qa=(New-Object -ComObject Shell.Application).Namespace('{QUICK_ACCESS}'); "
            f"$qa.Items() | Where-Object {{ $_.Path -eq {_ps_quote(path)} }} | "
            f"ForEach-Object {{ $_.InvokeVerb('unpinfromhome') }}")


# ----- single instance -----------------------------------------------------------------------
def send_to_running(args: list[str]) -> bool:
    """If Burrow is already running, give it these paths and return True."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(400):
        return False
    if WIN:
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # let the running window come forward
    sock.write(json.dumps(args).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(1500)
    sock.disconnectFromServer()
    return True


MAX_MESSAGE = 256 * 1024        # a launch hands over a few paths, never more than this
MAX_PATHS = 64


class InstanceServer(QObject):
    """Receives paths from later launches."""
    received = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(SERVER_NAME):
            QLocalServer.removeServer(SERVER_NAME)          # a stale name from a crash
            self.server.listen(SERVER_NAME)
        self.server.newConnection.connect(self._accept)

    def _accept(self):
        """Read a later launch's paths (JSON list, size-capped) and pass them on."""
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def read(sock=sock, buf=buf):
                buf.extend(bytes(sock.readAll().data()))
                if len(buf) > MAX_MESSAGE:                  # paths only: anything bigger is junk
                    buf.clear()
                    sock.abort()

            def done(sock=sock, buf=buf):
                read()
                try:
                    args = json.loads(bytes(buf).decode("utf-8") or "[]")
                except ValueError:
                    args = []
                if isinstance(args, list):
                    self.received.emit([a for a in args if isinstance(a, str)][:MAX_PATHS])
                sock.deleteLater()
            sock.readyRead.connect(read)
            sock.disconnected.connect(done)
