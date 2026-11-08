# Builds dist\FaceLock\FaceLock.exe (run from this folder)
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --windowed --name FaceLock `
    --icon assets\facelock.ico `
    --add-data "models;models" `
    --exclude-module tkinter --exclude-module PIL --exclude-module sv_ttk --exclude-module pystray `
    FaceLock.pyw

# Optional: build release\FaceLock-Setup.exe (needs Inno Setup: winget install JRSoftware.InnoSetup)
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) { & $iscc /Q installer.iss; Write-Host "Installer: release\FaceLock-Setup.exe" }
