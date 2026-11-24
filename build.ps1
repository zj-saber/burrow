# Builds dist\Burrow\Burrow.exe with PyInstaller.
# A folder build (not --onefile) on purpose: a one-file exe unpacks itself to a temp folder on
# every launch, which makes it start noticeably slower.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
}
$py = ".venv\Scripts\python.exe"
& $py -m pip install --quiet -r requirements.txt pyinstaller

New-Item -ItemType Directory -Force assets | Out-Null
& $py tools\make_icon.py assets\burrow.ico

& $py -m PyInstaller --noconfirm --clean --windowed `
    --name Burrow `
    --icon assets\burrow.ico `
    --add-data "assets\burrow.ico;assets" `
    --exclude-module tkinter `
    --exclude-module PySide6.QtWebEngineCore `
    --exclude-module PySide6.QtWebEngineWidgets `
    --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.QtMultimediaQuick `
    --hidden-import PySide6.QtMultimedia `
    --hidden-import PySide6.QtMultimediaWidgets `
    --exclude-module PySide6.Qt3DCore `
    --exclude-module PySide6.QtCharts `
    --exclude-module PySide6.QtPdf `
    Burrow.pyw

Write-Host "Built: $PSScriptRoot\dist\Burrow\Burrow.exe"
