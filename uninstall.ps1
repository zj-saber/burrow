# Removes Burrow installed by install.ps1. Your settings in %APPDATA%\Burrow are kept.
$ErrorActionPreference = "SilentlyContinue"
$dest = Join-Path $env:LOCALAPPDATA "Programs\Burrow"
Get-Process Burrow | Where-Object { $_.Path -like "$dest*" } | Stop-Process -Force
Remove-Item (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Burrow.lnk")
Remove-Item (Join-Path ([Environment]::GetFolderPath("Desktop")) "Burrow.lnk")
Remove-Item -Recurse "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Burrow"
# This script lives inside the folder being removed, so delete it from a separate process.
Start-Process -WindowStyle Hidden cmd.exe -ArgumentList "/c timeout /t 2 >nul & rmdir /s /q `"$dest`""
Write-Host "Burrow was uninstalled. Settings in $env:APPDATA\Burrow were kept."
# Undo "Use Burrow as the default file manager" so folders open in Explorer again.
foreach ($cls in "Directory", "Drive") {
    $base = "HKCU:\Software\Classes\$cls\shell"
    if ((Get-ItemProperty $base -Name "(default)" -ErrorAction SilentlyContinue)."(default)" -eq "openinburrow") {
        Remove-ItemProperty $base -Name "(default)"
    }
    Remove-Item -Recurse "$base\openinburrow"
}
Remove-Item -Recurse "HKCU:\Software\Classes\CLSID\{52205fd8-5dfb-447d-801a-d0b52f2e83e1}"
