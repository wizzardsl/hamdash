; Windows installer for hamdash (Inno Setup 6).
;
;   pyinstaller --noconfirm --clean hamdash.spec
;   iscc /DAppVersion=1.4.0 installer\hamdash.iss   ->  dist\hamdash-setup.exe
;
; Built and tested by build.yml on every Windows build. What it is for: a
; zip leaves the operator to choose a folder, keep a shortcut pointing at
; it, and replace the folder correctly on every upgrade - and "Extract All"
; names the folder after the version, so the old shortcut quietly keeps
; opening the old version. An installer does all of that once, and every
; later one upgrades in place.
;
; Things that must stay true:
;
; * AppId never changes. It is how a new installer finds the old install
;   to upgrade it; a new AppId installs a second copy beside the first.
; * Per-user by default (PrivilegesRequired=lowest): it goes to
;   %LOCALAPPDATA%\Programs\hamdash, needs no administrator, and an upgrade
;   never asks for one either. The dialog still offers all users.
; * Settings, the log and the board are NOT in {app}. They are in
;   %APPDATA%\hamdash, so neither an upgrade nor an uninstall touches them.
;   Nothing here may ever add that folder to [UninstallDelete].
; * {app}\_internal is deleted before files are copied, so nothing from an
;   older version lingers - a bundled card that was removed would otherwise
;   go on appearing in the Cards menu.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{66475C6D-9543-4AB0-93A2-87E298BEC771}
AppName=hamdash
AppVersion={#AppVersion}
AppVerName=hamdash {#AppVersion}
AppPublisher=hamdash
AppPublisherURL=https://github.com/wizzardsl/hamdash
AppSupportURL=https://github.com/wizzardsl/hamdash/issues
AppUpdatesURL=https://github.com/wizzardsl/hamdash/releases/latest
DefaultDirName={autopf}\hamdash
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=hamdash-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Closes a running hamdash before replacing its files (it is asked to
; close, which is what unkeys a radio), rather than failing half way.
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\hamdash.exe
UninstallDisplayName=hamdash
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\hamdash\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\hamdash"; Filename: "{app}\hamdash.exe"
Name: "{autodesktop}\hamdash"; Filename: "{app}\hamdash.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\hamdash.exe"; Description: "{cm:LaunchProgram,hamdash}"; Flags: nowait postinstall skipifsilent
