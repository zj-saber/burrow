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
