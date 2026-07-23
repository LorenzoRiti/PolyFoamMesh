; Inno Setup script for CFMesh-AutoGUI v2.0
; Requires: Inno Setup 6.x

#define MyAppName "CFMesh-AutoGUI"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Davide Valoroso"
#define MyAppURL "https://cfmesh-autogui.local"
#define MyAppExeName "run_app.bat"

[Setup]
AppId={{8A7B3C2D-9E4F-5A6B-7C8D-9E0F1A2B3C4D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf64}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=.
OutputBaseFilename=CFMesh-AutoGUI-{#MyAppVersion}-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Files]
Source: "..\src\cfmesh_autogui\*.py"; DestDir: "{app}\src\cfmesh_autogui"
Source: "..\src\cfmesh_autogui\core\*.py"; DestDir: "{app}\src\cfmesh_autogui\core"
Source: "..\src\cfmesh_autogui\gui\*.py"; DestDir: "{app}\src\cfmesh_autogui\gui"
Source: "..\pyproject.toml"; DestDir: "{app}"
Source: "..\README.md"; DestDir: "{app}"
Source: "..\templates\*.json"; DestDir: "{app}\templates"
Source: "..\locale\*"; DestDir: "{app}\locale"; Flags: recursesubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent
