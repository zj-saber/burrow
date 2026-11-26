"""Update Burrow from its GitHub releases without leaving the app.

Loaded only when an update check runs (every 12 hours at most, or from the menu). Downloads use
Qt's own networking with Windows' TLS (schannel), so the build needs no OpenSSL. Installing:
the new build is unpacked to %TEMP%, a tiny script waits for Burrow to quit, copies the files
over the install folder and starts the new version.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QProgressBar, QPushButton,
                               QTextBrowser, QVBoxLayout)

from . import __version__

REPO = "zj-saber/burrow"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"   # the only place updates come from
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
TMP_PREFIX = "burrow-update-"


def parse_version(s: str) -> tuple:
    out = []
    for part in s.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def sha256_of(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def install_dir() -> str | None:
    return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else None


def can_install() -> bool:
    """Only the built app updates itself; running from source just points to the page."""
    d = install_dir()
    return bool(d) and sys.platform == "win32" and os.access(d, os.W_OK)


def cleanup():
    """Remove what an earlier update left in %TEMP%."""
    for d in glob.glob(os.path.join(tempfile.gettempdir(), TMP_PREFIX + "*")):
        try:
            if time.time() - os.path.getmtime(d) > 60:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def sync_registered_version():
    """Settings ▸ Apps shows the version install.ps1 wrote; keep it current after an update."""
    d = install_dir()
    if not d or sys.platform != "win32":
        return
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Burrow"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            loc = winreg.QueryValueEx(k, "InstallLocation")[0]
            if os.path.normcase(os.path.normpath(loc)) == os.path.normcase(d) \
                    and winreg.QueryValueEx(k, "DisplayVersion")[0] != __version__:
                winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, __version__)
    except OSError:
        pass


class Updater(QObject):
    """Talks to GitHub: latest release, and downloading its zip (checked against GitHub's SHA-256)."""
    found = Signal(dict)          # version, notes, zip, size, page
    upToDate = Signal(str)        # latest version
    failed = Signal(str)
    progress = Signal(int, int)
    downloaded = Signal(str)      # path of the downloaded zip

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)
        self._reply = None
        self._file = None

    @staticmethod
    def _request(url: str) -> QNetworkRequest:
        r = QNetworkRequest(QUrl(url))
        r.setRawHeader(b"User-Agent", f"Burrow/{__version__}".encode())
        r.setRawHeader(b"Accept", b"application/vnd.github+json")
        r.setTransferTimeout(30000)
        return r

    def check(self):
        reply = self.nam.get(self._request(API))
        reply.finished.connect(lambda: self._checked(reply))

    def _checked(self, reply):
        """Read the release info; only this repo's zip with a published checksum can be installed."""
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.failed.emit(reply.errorString())
            return
        import json
        try:
            data = json.loads(bytes(reply.readAll().data()).decode("utf-8"))
            version = data["tag_name"].lstrip("vV")
            asset = next((a for a in data.get("assets", []) if a["name"].lower().endswith("windows-x64.zip")), None)
        except (ValueError, KeyError, TypeError):
            self.failed.emit("GitHub sent an answer Burrow didn't understand.")
            return
        if not is_newer(version):
            self.upToDate.emit(version)
            return
        url = asset.get("browser_download_url", "") if asset else ""
        digest = (asset.get("digest") or "") if asset else ""
        ok = url.startswith(DOWNLOAD_PREFIX) and digest.startswith("sha256:")
        self.found.emit({"version": version, "notes": data.get("body") or "", "page": data.get("html_url") or PAGE,
                         # without a GitHub checksum (or from anywhere else) Burrow won't install it
                         "zip": url if ok else None, "sha256": digest[7:].lower() if ok else "",
                         "size": asset.get("size", 0) if asset else 0})

    def download(self, info: dict):
        self._expected = info.get("sha256", "")
        folder = tempfile.mkdtemp(prefix=TMP_PREFIX)
        path = os.path.join(folder, f"Burrow-{info['version']}.zip")
        self._file = open(path, "wb")
        reply = self._reply = self.nam.get(self._request(info["zip"]))
        reply.readyRead.connect(lambda: self._file.write(bytes(reply.readAll().data())))
        reply.downloadProgress.connect(lambda done, total: self.progress.emit(int(done), int(total or info["size"])))
        reply.finished.connect(lambda: self._downloaded(reply, path))

    def _downloaded(self, reply, path):
        """Download finished: keep it only if its SHA-256 matches what GitHub published."""
        self._file.write(bytes(reply.readAll().data()))
        self._file.close()
        reply.deleteLater()
        self._reply = None
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.failed.emit("Download stopped." if reply.error() == QNetworkReply.NetworkError.OperationCanceledError
                             else reply.errorString())
            return
        if sha256_of(path) != self._expected:
            os.remove(path)
            self.failed.emit("The download doesn't match the checksum GitHub published, so it wasn't installed.")
            return
        self.downloaded.emit(path)

    def cancel(self):
        if self._reply is not None:
            self._reply.abort()


def prepare(zip_path: str) -> str:
    """Unpack the downloaded build next to the zip; returns the folder holding Burrow.exe."""
    dest = os.path.join(os.path.dirname(zip_path), "new")
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():                 # no absolute paths or ".." in a download
            if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
                raise OSError("The download looks damaged.")
        z.extractall(dest)
    for root, _dirs, files in os.walk(dest):
        if "Burrow.exe" in files:
            return root
    raise OSError("The download doesn't contain Burrow.exe.")


def launch_installer(src: str):
    """Start the script that swaps the files once this process has exited, then restarts."""
    dst = install_dir()
    work = os.path.dirname(os.path.dirname(src)) if os.path.basename(os.path.dirname(src)) == "new" \
        else os.path.dirname(src)
    pid = os.getpid()
    script = os.path.join(work, "update.cmd")
    with open(script, "w", encoding="utf-8") as f:
        f.write("\r\n".join([
            "@echo off",
            "chcp 65001 >nul",
            ":wait",
            f'tasklist /FI "PID eq {pid}" /NH 2>nul | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)',
            f'robocopy "{src}" "{dst}" /E /R:10 /W:1 /NFL /NDL /NJH /NJS /NP >nul',
            f'start "" "{os.path.join(dst, "Burrow.exe")}"',
            "",
        ]))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd.exe", "/c", script], creationflags=flags, close_fds=True, cwd=work)


class UpdateDialog(QDialog):
    """Check, show what's new, download with progress, install and restart."""

    def __init__(self, window, info: dict | None = None, start: bool = False):
        super().__init__(window)
        self.win = window
        self.info = info
        self.setWindowTitle("Update Burrow")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() + 2)
        f.setBold(True)
        self.title.setFont(f)
        self.sub = QLabel()
        self.sub.setWordWrap(True)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMinimumHeight(220)
        self.notes.hide()
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(6)
        self.bar.hide()
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        lay.addWidget(self.notes, 1)
        lay.addWidget(self.bar)
        self.buttons = QDialogButtonBox()
        self.go = QPushButton("Update now")
        self.skip = QPushButton("Skip this version")
        self.close_btn = QPushButton("Close")
        self.buttons.addButton(self.go, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(self.skip, QDialogButtonBox.ButtonRole.ActionRole)
        self.buttons.addButton(self.close_btn, QDialogButtonBox.ButtonRole.RejectRole)
        self.go.clicked.connect(self._go)
        self.skip.clicked.connect(self._skip)
        self.close_btn.clicked.connect(self.reject)
        lay.addWidget(self.buttons)
        self.up = Updater(self)
        self.up.found.connect(self._show)
        self.up.upToDate.connect(self._latest)
        self.up.failed.connect(self._failed)
        self.up.progress.connect(self._progress)
        self.up.downloaded.connect(self._install)
        if info:
            self._show(info)
            if start and can_install() and info.get("zip"):
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, self._go)       # the Update button means "update now"
        else:
            self.title.setText("Checking for updates…")
            self.sub.setText(f"You have Burrow {__version__}.")
            self.go.hide()
            self.skip.hide()
            self.up.check()

    def _latest(self, version):
        self.win.config.set("update_checked", int(time.time()))
        self.title.setText("Burrow is up to date")
        self.sub.setText(f"You have {__version__}, the latest release.")

    def _failed(self, msg):
        """Show what went wrong and offer the releases page instead."""
        self.bar.hide()
        try:
            self.close_btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.close_btn.clicked.connect(self.reject)
        self.close_btn.setText("Close")
        self.title.setText("Couldn't update" if self.info else "Couldn't check for updates")
        self.sub.setText(f"{msg}\n\nYou can also download it from the releases page.")
        self.go.setText("Open releases page")
        self.go.show()
        self.go.setEnabled(True)
        self.info = dict(self.info or {}, zip=None, page=(self.info or {}).get("page", PAGE))

    def _show(self, info):
        """Show the new version, its notes, and whether this copy can update itself."""
        self.info = info
        self.win.config.set("update_checked", int(time.time()))
        self.title.setText(f"Burrow {info['version']} is available")
        size = f" ({info['size'] / 2**20:.0f} MB)" if info.get("size") else ""
        if can_install() and info.get("zip"):
            self.sub.setText(f"You have {__version__}. Burrow downloads the update{size}, closes, "
                             f"and opens again on the new version.")
            self.go.setText("Update now")
        else:
            self.sub.setText(f"You have {__version__}. This copy can't update itself "
                             f"(it runs from source or a read-only folder).")
            self.go.setText("Open releases page")
        if info.get("notes"):
            self.notes.setMarkdown(info["notes"])
            self.notes.show()
        self.go.show()
        self.skip.show()

    def _go(self):
        """Update now: download (or open the releases page when this copy can't update itself)."""
        if not (can_install() and self.info and self.info.get("zip")):
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl((self.info or {}).get("page") or PAGE))
            self.accept()
            return
        self.go.setEnabled(False)
        self.skip.hide()
        self.close_btn.setText("Cancel")
        self.close_btn.clicked.disconnect()
        self.close_btn.clicked.connect(self.up.cancel)
        self.bar.setRange(0, 0)
        self.bar.show()
        self.sub.setText("Downloading…")
        self.up.download(self.info)

    def _progress(self, done, total):
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.sub.setText(f"Downloading… {done / 2**20:.1f} of {total / 2**20:.0f} MB")

    def _install(self, zip_path):
        self.sub.setText("Installing…")
        self.bar.setRange(0, 0)
        try:
            src = prepare(zip_path)
            launch_installer(src)
        except (OSError, zipfile.BadZipFile) as ex:
            self._failed(str(ex))
            return
        self.win.config.set("update_skip", "")
        self.accept()
        self.win.quit_for_real()

    def _skip(self):
        if self.info:
            self.win.config.set("update_skip", self.info["version"])
        self.reject()


def auto_check(window):
    """Once a day, quietly: if there's a new version, the window shows an 'Update' chip."""
    up = Updater(window)

    def found(info):
        window.config.set("update_checked", int(time.time()))
        if info["version"] != window.config.get("update_skip"):
            window.show_update_available(info)
        up.deleteLater()

    up.found.connect(found)
    up.upToDate.connect(lambda _v: (window.config.set("update_checked", int(time.time())), up.deleteLater()))
    up.failed.connect(lambda _m: up.deleteLater())       # offline: try again next start
    up.check()
