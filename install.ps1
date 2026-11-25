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

# Close running copies so their files can be replaced (ask nicely, then force), and retry the
# delete for a few seconds while Windows releases the files.
$running = Get-Process Burrow -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$dest*" }
foreach ($p in $running) { $p.CloseMainWindow() | Out-Null }
foreach ($p in $running) { if (-not $p.WaitForExit(4000)) { $p.Kill(); $p.WaitForExit(3000) | Out-Null } }
if (Test-Path $dest) {
    for ($i = 0; $i -lt 10; $i++) {
        try { Remove-Item -Recurse -Force $dest -ErrorAction Stop; break }
        catch {
            if ($i -eq 9) { throw "Couldn't replace $dest (a file is still in use): $_" }
            Get-Process Burrow -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$dest*" } | Stop-Process -Force
            Start-Sleep -Milliseconds 700
        }
    }
}
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
