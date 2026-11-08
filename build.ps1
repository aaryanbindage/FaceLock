# Builds dist\FaceLock\FaceLock.exe (run from this folder)
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --windowed --name FaceLock `
    --icon assets\facelock.ico `
    --add-data "models;models" `
    --exclude-module tkinter --exclude-module PIL --exclude-module sv_ttk --exclude-module pystray `
    FaceLock.pyw
