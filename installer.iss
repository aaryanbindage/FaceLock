; Inno Setup script: builds release\FaceLock-Setup.exe from dist\FaceLock (run build.ps1 first).

#define AppName "FaceLock"
#define AppVersion "2.0.1"
#define AppExe "FaceLock.exe"

[Setup]
AppId={{6F1C2B4E-9A7D-4E2B-B8F1-FACE10C20000}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=aaryanbindage
AppPublisherURL=https://github.com/aaryanbindage/FaceLock
AppSupportURL=https://github.com/aaryanbindage/FaceLock/issues
AppUpdatesURL=https://github.com/aaryanbindage/FaceLock/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=release
OutputBaseFilename=FaceLock-Setup
SetupIconFile=assets\facelock.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start FaceLock when I sign in to Windows (recommended)"; GroupDescription: "Startup:"

[Files]
Source: "dist\FaceLock\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Face ID for Windows"
Name: "{userprograms}\Lock PC with {#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--lock"; Comment: "Lock this PC with Face ID"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"" --startup"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch FaceLock"; Flags: nowait postinstall skipifsilent

[Code]
function DataDir(): String;
begin
  Result := ExpandConstant('{userappdata}\FaceLock');
end;

{ Stop a running FaceLock. quit.flag tells its watchdog not to restart it. }
procedure StopFaceLock();
var
  Code: Integer;
begin
  ForceDirectories(DataDir());
  SaveStringToFile(DataDir() + '\quit.flag', '', False);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM FaceLock.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopFaceLock();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    DeleteFile(DataDir() + '\quit.flag');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopFaceLock();
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'FaceLock');
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    if DirExists(DataDir()) then
    begin
      if (not UninstallSilent()) and (MsgBox('Also delete your FaceLock data (face samples, PIN, settings, history and intruder photos)?',
                                      mbConfirmation, MB_YESNO) = IDYES) then
        DelTree(DataDir(), True, True, True)
      else
        DeleteFile(DataDir() + '\quit.flag');
    end;
  end;
end;
