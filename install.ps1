# Installs Burrow for the current user (no admin rights needed):
#   - copies dist\Burrow to %LOCALAPPDATA%\Programs\Burrow
#   - adds a Start menu shortcut (and a desktop shortcut with -Desktop)
#   - registers it in Settings > Apps so it can be uninstalled from there
param([switch]$Desktop)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$src = Join-Path $PSScriptRoot "dist\Burrow"
if (-not (Test-Path "$src\Burrow.exe")) { throw "Build first: .\build.ps1" }
$dest = Join-Path $env:LOCALAPPDATA "Programs\Burrow"

# Close a running copy so its files can be replaced.
Get-Process Burrow -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$dest*" } | ForEach-Object {
    $_.CloseMainWindow() | Out-Null
    if (-not $_.WaitForExit(5000)) { $_.Kill() }
}

if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item -Recurse -Force "$src\*" $dest
Copy-Item -Force "$PSScriptRoot\uninstall.ps1" $dest

$exe = Join-Path $dest "Burrow.exe"
$shell = New-Object -ComObject WScript.Shell
function New-Shortcut($path) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = $dest
    $lnk.IconLocation = "$exe,0"
    $lnk.Description = "Burrow file manager"
    $lnk.Save()
}
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Burrow.lnk"
New-Shortcut $startMenu
if ($Desktop) { New-Shortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "Burrow.lnk") }

$version = (Select-String -Path "$PSScriptRoot\burrow\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Burrow"
New-Item -Force $key | Out-Null
$size = [int]((Get-ChildItem -Recurse $dest | Measure-Object Length -Sum).Sum / 1KB)
Set-ItemProperty $key DisplayName "Burrow"
Set-ItemProperty $key DisplayVersion $version
Set-ItemProperty $key Publisher "Burrow"
Set-ItemProperty $key DisplayIcon "$exe,0"
Set-ItemProperty $key InstallLocation $dest
Set-ItemProperty $key EstimatedSize $size -Type DWord
Set-ItemProperty $key NoModify 1 -Type DWord
Set-ItemProperty $key NoRepair 1 -Type DWord
Set-ItemProperty $key UninstallString "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$dest\uninstall.ps1`""

Write-Host "Installed Burrow $version to $dest"
Write-Host "Start menu: Burrow"
