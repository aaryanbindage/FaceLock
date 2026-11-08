# Removes FaceLock. Add -KeepData to keep face data, PIN and settings.
param([switch]$KeepData)
$data = Join-Path $env:APPDATA "FaceLock"
New-Item -ItemType Directory -Force $data | Out-Null
New-Item -ItemType File -Force (Join-Path $data "quit.flag") | Out-Null
Get-Process FaceLock -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
Remove-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name FaceLock -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $env:LOCALAPPDATA "Programs\FaceLock") -ErrorAction SilentlyContinue
foreach ($p in @("$([Environment]::GetFolderPath('Programs'))\FaceLock.lnk",
                 "$([Environment]::GetFolderPath('Programs'))\Lock PC with FaceLock.lnk",
                 "$([Environment]::GetFolderPath('Desktop'))\FaceLock.lnk")) { Remove-Item $p -ErrorAction SilentlyContinue }
if (-not $KeepData) { Remove-Item -Recurse -Force $data -ErrorAction SilentlyContinue }
else { Remove-Item (Join-Path $data "quit.flag") -ErrorAction SilentlyContinue }
Write-Host "FaceLock removed."
