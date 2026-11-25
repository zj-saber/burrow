# Builds dist\Burrow\Burrow.exe with PyInstaller.
# A folder build (not --onefile) on purpose: a one-file exe unpacks itself to a temp folder on
# every launch, which makes it start noticeably slower.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
}
$py = ".venv\Scripts\python.exe"
& $py -m pip install --quiet --disable-pip-version-check -r requirements.txt pyinstaller

New-Item -ItemType Directory -Force assets | Out-Null
& $py tools\make_icon.py assets\burrow.ico

& $py -m PyInstaller --noconfirm --clean --windowed `
    --name Burrow `
    --icon assets\burrow.ico `
    --add-data "assets\burrow.ico;assets" `
    --exclude-module tkinter `
    --exclude-module ssl `
    --exclude-module _ssl `
    --exclude-module _hashlib `
    --exclude-module PySide6.QtWebEngineCore `
    --exclude-module PySide6.QtWebEngineWidgets `
    --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.QtMultimediaQuick `
    --hidden-import PySide6.QtMultimedia `
    --hidden-import PySide6.QtMultimediaWidgets `
    --hidden-import PySide6.QtPdf `
    --hidden-import PySide6.QtNetwork `
    --exclude-module PySide6.Qt3DCore `
    --exclude-module PySide6.QtCharts `
    Burrow.pyw

# Leave out Qt parts Burrow never loads (checked against every DLL's import table):
# software OpenGL, the QML/Quick stack (only the on-screen keyboard plugin pulled it in),
# TLS backends (Burrow makes no web requests), translations and spare platform plugins.
$int = "dist\Burrow\_internal"
$drop = @(
    "PySide6\opengl32sw.dll", "PySide6\Qt6Quick.dll", "PySide6\Qt6Qml.dll", "PySide6\Qt6QmlModels.dll",
    "PySide6\Qt6QmlMeta.dll", "PySide6\Qt6QmlWorkerScript.dll", "PySide6\Qt6VirtualKeyboard.dll",
    "PySide6\Qt6OpenGL.dll", "PySide6\plugins\platforminputcontexts", "PySide6\plugins\tls",
    "PySide6\plugins\networkinformation", "PySide6\plugins\platforms\qdirect2d.dll",
    "PySide6\plugins\platforms\qminimal.dll", "PySide6\plugins\platforms\qoffscreen.dll",
    "PySide6\translations", "libssl-3-x64.dll", "libcrypto-3-x64.dll", "libssl-3.dll", "libcrypto-3.dll"
)
$before = (Get-ChildItem dist\Burrow -Recurse -File | Measure-Object Length -Sum).Sum
foreach ($d in $drop) {
    $p = Join-Path $int $d
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}
$after = (Get-ChildItem dist\Burrow -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("Trimmed {0:N0} MB -> {1:N0} MB" -f ($before / 1MB), ($after / 1MB))
Write-Host "Built: $PSScriptRoot\dist\Burrow\Burrow.exe"
