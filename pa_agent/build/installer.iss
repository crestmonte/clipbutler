; Inno Setup script for CLPBTLR Windows installer
; Build after PyInstaller: iscc build\installer.iss

#define MyAppName "CLPBTLR"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "CLPBTLR Inc."
#define MyAppURL "https://clpbtlr.com"
#define MyAppExeName "CLPBTLR.exe"
#define DistFolder "..\dist\CLPBTLR"
#define CEPSource "..\premiere_panel"

[Setup]
AppId={{8A2B4C7D-1F3E-4A5B-9C8D-2E6F0A1B3C4D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist\installer
OutputBaseFilename=CLPBTLR_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupservice"; Description: "Start CLPBTLR automatically at Windows login"; GroupDescription: "Startup:"

[Files]
; Main application
Source: "{#DistFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Premiere CEP panel
Source: "{#CEPSource}\*"; DestDir: "{userappdata}\Adobe\CEP\extensions\CLPBTLR"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Auto-start on login via registry Run key
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "CLPBTLR"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startupservice

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\Adobe\CEP\extensions\CLPBTLR"

[Code]
// Check if Adobe Premiere Pro is installed
function IsPremierePro: Boolean;
var
  S: String;
begin
  Result := RegQueryStringValue(HKLM, 'SOFTWARE\Adobe\Premiere Pro', 'InstallPath', S);
  if not Result then
    Result := RegQueryStringValue(HKCU, 'SOFTWARE\Adobe\Premiere Pro', 'InstallPath', S);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if not IsPremierePro then
      MsgBox('Adobe Premiere Pro was not detected. The CLPBTLR panel will be available once Premiere Pro is installed.', mbInformation, MB_OK);
  end;
end;
