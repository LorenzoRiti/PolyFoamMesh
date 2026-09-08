; PolyFoamMesh — Inno Setup installer for the PyInstaller one-dir bundle.
;
; Installs dist/PolyFoamMesh/ (the self-contained app: exe bootloader +
; _internal/ with Python + all dependencies — no Python required on the
; target machine) to %LocalAppData%\Programs\PolyFoamMesh (per-user,
; NO admin needed), with Start Menu + optional desktop shortcut,
; file associations (.step/.stp/.stl) and a registry-cleaning uninstaller.
;
; The bundled app is the "demo no-WSL" build: CFD Poly (GMSH) and FEM
; Tetra work standalone; the cfMesh / checkMesh paths need WSL2+OpenFOAM
; which this installer does NOT ship (a notice in the app explains it).
;
; Build:  ISCC.exe installer\inno_setup.iss   (from the repo root)
; Output: installer\output\PolyFoamMesh-2.2.0-Setup.exe

#ifndef MyAppVersion
#define MyAppVersion "2.2.0"
#endif
#define MyAppName "PolyFoamMesh"
#define MyAppPublisher "PolyFoamMesh Project"
#define MyAppExeName "PolyFoamMesh.exe"
#define MyAppAssocStep ".step"
#define MyAppAssocStp ".stp"
#define MyAppAssocStl ".stl"
; relative to installer/inno_setup.iss -> repo root
#define MyAppDir "..\dist\PolyFoamMesh"

[Setup]
AppId={{BD86A4BE-FD11-45CC-887E-4A6C4B1EE39D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=PolyFoamMesh-{#MyAppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; per-user install — the friend does NOT need administrator rights
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; the whole bundle lives under {app}; the uninstaller removes it all
UninstallFilesDir={app}\unins

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MyAppDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; file associations (per-user, no admin)
Root: HKCU; Subkey: "Software\Classes\{#MyAppAssocStep}\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppName}.step"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\{#MyAppAssocStp}\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppName}.stp"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\{#MyAppAssocStl}\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppName}.stl"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\{#MyAppName}.step"; ValueType: string; ValueName: ""; ValueData: "{#MyAppName} CAD"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppName}.step\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppName}.step\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppName}.stp"; ValueType: string; ValueName: ""; ValueData: "{#MyAppName} CAD"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppName}.stp\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppName}.stp\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppName}.stl"; ValueType: string; ValueName: ""; ValueData: "{#MyAppName} CAD"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppName}.stl\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppName}.stl\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
