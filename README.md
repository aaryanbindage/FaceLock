# FaceLock 2 — Face ID for Windows with a regular webcam

Lock **apps** and your **whole PC** with your face. No IR / Windows Hello camera needed.

> 🍎 **On a Mac?** FaceLock is Windows-only. Use **[FaceGate for Mac](https://github.com/dweep-desai/FaceGate-Mac)** instead.

## Download
Get the latest version from the **[Releases](https://github.com/aaryanbindage/FaceLock/releases/latest)** page:

- **`FaceLock-Setup.exe` (recommended):** run it and click through. It installs for your user only (no admin needed), adds Start Menu shortcuts, and can start FaceLock with Windows. Uninstall it from **Settings → Apps**.
- **`FaceLock-Windows.zip` (portable):** unzip it anywhere and run `FaceLock.exe`.

If Windows SmartScreen warns about an unknown publisher, click **More info → Run anyway**. The app isn't code-signed.

Requires Windows 10 or 11 (64-bit) and any webcam.

## Build from source
- **Run:** `pip install -r requirements.txt`, then `pythonw FaceLock.pyw`
- **Build the exe:** `powershell -File build.ps1` (output goes to `dist\FaceLock\`; if Inno Setup is installed, it also builds `release\FaceLock-Setup.exe` from `installer.iss`)
- **Install the build:** `powershell -File install.ps1`. To uninstall, run `uninstall.ps1` (add `-KeepData` to keep your face data).

The first launch walks you through setup: scan your face, choose a backup PIN, pick apps to lock, and decide whether to start with Windows. After that FaceLock lives in the system tray. Click the tray icon to open the dashboard (this needs Face ID); right-click for Lock / Pause / Quit.

## Features
**App Lock**
- Locked apps freeze the moment they start. They continue only after Face ID or your PIN; cancel and they close.
- A pop-up shows the app's real icon. One look approves every app that's waiting at that moment.
- Apps re-lock immediately when closed, or after 1 to 60 minutes.

**System Lock**
- Covers every monitor with a blurred-desktop lock screen, a clock and a Face ID ring. It unlocks when it sees you, or with your PIN.
- Win key, Alt+Tab, Alt+F4 and Ctrl+Shift+Esc are blocked while locked.
- Lock with your shortcut (click the box in System Lock and press keys to change it), from the tray, or with the dashboard button.
- **Walk-away lock:** locks after 15 s to 5 min without seeing *your* face (including when someone else sits down). It can use either the FaceLock screen or the real Windows lock screen.
- **Two-factor sign-in:** Face ID right after Windows starts, and/or after every Windows unlock.

**Security**
- **Liveness check:** turn your head slightly to unlock, so a photo of you won't work.
- **Self-protection:** a detached watchdog restarts FaceLock (locked, if it was locked) when someone kills it.
- **Administrator mode:** can lock admin apps and closes Task Manager while locked.
- PIN backoff: after 5 wrong tries, PIN entry pauses for 1 min, doubling up to 15 min.
- **Intruder photos**, plus an **Activity** timeline of every unlock, block and lock.
- Settings, Pause and Quit all require Face ID.

**Recognition quality**
- Strictness setting (Relaxed / Balanced / Strict) and "alternate looks" for glasses, hats and so on.
- Low-light boost for dark rooms.
- The camera stays warm for a few seconds between prompts, so back-to-back unlocks are instant.

**Privacy:** only 128-number face templates are stored (no photos), encrypted with Windows DPAPI in `%APPDATA%\FaceLock`. Everything runs offline.

## Command line
`FaceLock.exe --lock` starts FaceLock and locks immediately.

## Honest limits
- A webcam is weaker than IR Face ID. Keep the liveness check on, and use Strict mode if you want fewer false accepts.
- No app can block Ctrl+Alt+Del. Run FaceLock as administrator and keep self-protection on. For walk-away locking, use the Windows lock option for the strongest protection.
- Replacing the real Windows sign-in screen would need a signed Credential Provider DLL, so FaceLock's lock is an overlay on your session.

Models: OpenCV Zoo YuNet (detection) and SFace (recognition), in `models/`.
