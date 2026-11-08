# Installs FaceLock for the current user (no admin needed) and adds shortcuts.
$ErrorActionPreference = "Stop"
# Works from the source repo (dist\FaceLock) or from the unzipped release folder.
$src = if (Test-Path (Join-Path $PSScriptRoot "FaceLock.exe")) { $PSScriptRoot } else { Join-Path $PSScriptRoot "dist\FaceLock" }
$dest = Join-Path $env:LOCALAPPDATA "Programs\FaceLock"
if (-not (Test-Path "$src\FaceLock.exe")) { throw "Build first: powershell -File build.ps1" }

# Stop a running copy cleanly (quit.flag tells the watchdog not to restart it).
$data = Join-Path $env:APPDATA "FaceLock"
New-Item -ItemType Directory -Force $data | Out-Null
New-Item -ItemType File -Force (Join-Path $data "quit.flag") | Out-Null
Get-Process FaceLock -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item -Recurse -Force "$src\*" $dest
Remove-Item (Join-Path $data "quit.flag") -ErrorAction SilentlyContinue

$shell = New-Object -ComObject WScript.Shell
$exe = Join-Path $dest "FaceLock.exe"
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "FaceLock.lnk"))
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = $dest
    $lnk.Description = "Face ID for Windows"
    $lnk.Save()
}
$lock = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Programs")) "Lock PC with FaceLock.lnk"))
$lock.TargetPath = $exe
$lock.Arguments = "--lock"
$lock.WorkingDirectory = $dest
$lock.Save()

# Keep an existing "Start with Windows" entry pointing at the installed copy.
$run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty $run -Name FaceLock -ErrorAction SilentlyContinue) {
    Set-ItemProperty $run -Name FaceLock -Value "`"$exe`" --startup"
}
Write-Host "FaceLock installed to $dest"
Start-Process $exe
