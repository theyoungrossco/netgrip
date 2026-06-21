; Inno Setup script for NetGrip — wraps the PyInstaller one-folder build into a
; single setup.exe with Start-Menu and (optional) desktop shortcuts.
;
; Compile from the repo root after PyInstaller has produced dist\NetGrip\:
;
;     iscc /DAppVersion=0.3.0 installer\windows\netgrip.iss
;
; AppVersion is passed in by the build script (read from netgrip.__version__);
; the #ifndef below is only a fallback for compiling by hand.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

; Folder holding the PyInstaller output. Overridable with /DSourceDir=...
#ifndef SourceDir
  #define SourceDir "..\..\dist\NetGrip"
#endif

#define AppName "NetGrip"
#define AppPublisher "NetGrip contributors"
#define AppURL "https://github.com/theyoungrossco/netgrip"
#define AppExeName "NetGrip.exe"

[Setup]
; A stable AppId keeps upgrades/uninstall tied to one product across versions.
AppId={{E27003BC-1ACD-4E18-BD76-E085ECB7121B}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Install per-user by default so no UAC prompt is needed; the user can still
; choose an all-users install if they run elevated.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#SourceDir}\..
OutputBaseFilename=NetGrip-{#AppVersion}-setup
; Relative paths resolve from this .iss file's directory (installer\windows).
SetupIconFile=..\..\data\icons\netgrip.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
