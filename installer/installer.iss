; CFMesh-AutoGUI Inno Setup Script
; Enterprise installer with silent mode, GPO deployment, and dependency check

#define MyAppName "CFMesh-AutoGUI"
#define MyAppVersion "2.0.1"
#define MyAppPublisher "CFMesh-AutoGUI Project"
#define MyAppURL "https://github.com/cfmesh-autogui"
#define MyAppExeName "CFMesh-AutoGUI.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\build
OutputBaseFilename=CFMesh-AutoGUI-{#MyAppVersion}-Setup
SetupIconFile=..\build\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
MinVersion=10.0.0
ArchitecturesInstallIn64BitMode=x64compatible

; Silent switches for GPO deployment
;/VERYSILENT /SUPPRESSMSGBOXES /NORESTART

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\CFMesh-AutoGUI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\templates\*"; DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\plugins\*"; DestDir: "{app}\plugins"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\locale\*"; DestDir: "{app}\locale"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\sample_cad\*"; DestDir: "{app}\tutorials"; Flags: ignoreversion recursesubdirs

; VC++ redistributable (bundled)
Source: "vc_redist.x64.exe"; DestDir: {tmp}; Flags: deleteafterinstall; Check: IsWin64

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; File associations
Root: HKCR; Subkey: ".step"; ValueType: string; ValueName: ""; ValueData: "CFMesh-AutoGUI.STEP"; Flags: uninsdeletevalue
Root: HKCR; Subkey: "CFMesh-AutoGUI.STEP"; ValueType: string; ValueName: ""; ValueData: "STEP Model File"
Root: HKCR; Subkey: "CFMesh-AutoGUI.STEP\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKCR; Subkey: "CFMesh-AutoGUI.STEP\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

Root: HKCR; Subkey: ".stp"; ValueType: string; ValueName: ""; ValueData: "CFMesh-AutoGUI.STP"; Flags: uninsdeletevalue
Root: HKCR; Subkey: "CFMesh-AutoGUI.STP"; ValueType: string; ValueName: ""; ValueData: "STEP Model File"
Root: HKCR; Subkey: "CFMesh-AutoGUI.STP\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKCR; Subkey: "CFMesh-AutoGUI.STP\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

Root: HKCR; Subkey: ".stl"; ValueType: string; ValueName: ""; ValueData: "CFMesh-AutoGUI.STL"; Flags: uninsdeletevalue
Root: HKCR; Subkey: "CFMesh-AutoGUI.STL"; ValueType: string; ValueName: ""; ValueData: "STL Mesh File"
Root: HKCR; Subkey: "CFMesh-AutoGUI.STL\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKCR; Subkey: "CFMesh-AutoGUI.STL\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Run]
; Check WSL2 availability (non-blocking)
Filename: "wsl.exe"; Parameters: "--status"; Flags: runhidden shellexec waituntilterminated; Description: "Checking WSL2..."
; Install VC++ redist if needed
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/quiet /norestart"; StatusMsg: "Installing VC++ redistributable..."; Check: NeedsVCRedist

[Code]
function NeedsVCRedist: Boolean;
begin
  Result := False;
  if not RegKeyExists(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64') then
    Result := True;
end;

function InitializeSetup: Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Create log marker
    SaveStringToFile(ExpandConstant('{app}\install_log.txt'),
      'Installed: ' + GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + #13#10 +
      'Version: {#MyAppVersion}' + #13#10 +
      'Silent: ' + GetCmdSwitch('VERYSILENT') + #13#10, False);
  end;
end;
