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
        win_e = rf"Software\Classes\CLSID\{THIS_PC_CLSID}\shell\opennewwindow\command"
        if enable:
            _set_value(win_e, "", cmd)
            _set_value(win_e, "DelegateExecute", "")
        else:
            _delete_tree(rf"Software\Classes\CLSID\{THIS_PC_CLSID}")
        _notify_assoc_changed()
        return None
    except OSError as ex:
        return str(ex)


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
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def read(sock=sock, buf=buf):
                buf.extend(bytes(sock.readAll().data()))

            def done(sock=sock, buf=buf):
                read()
                try:
                    args = json.loads(bytes(buf).decode("utf-8") or "[]")
                except ValueError:
                    args = []
                self.received.emit([a for a in args if isinstance(a, str)])
                sock.deleteLater()
            sock.readyRead.connect(read)
            sock.disconnected.connect(done)
