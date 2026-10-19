; Junction's installer (Inno Setup 6). Build it with tools/build.py, which builds Junction.exe first and
; passes the version from junction/config.py:
;
;     python tools/build.py        ->  dist/JunctionSetup-<version>.exe
;
; It installs for the current Windows user only (no administrator needed), in %LOCALAPPDATA%\Programs\Junction.
; Settings and keys live in %APPDATA%\Junction and are kept on uninstall unless the person asks otherwise.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Never change the AppId: it's how Windows knows a new version replaces the old one.
AppId={{AD9E5250-82CC-44C7-96D7-6235953AD5F2}
AppName=Junction
AppVersion={#AppVersion}
AppVerName=Junction {#AppVersion}
AppPublisher=Junction
DefaultDirName={autopf}\Junction
DefaultGroupName=Junction
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=dist
OutputBaseFilename=JunctionSetup-{#AppVersion}
SetupIconFile=web\icon.ico
UninstallDisplayIcon={app}\Junction.exe
UninstallDisplayName=Junction
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Junction
; Dark, in Junction's own colours, with its title bar to match. The pictures come from tools/make_installer_art.py.
WizardStyle=modern dark includetitlebar
WizardBackColor=#12161d
WizardImageFile=installer\wizard-100.bmp,installer\wizard-150.bmp,installer\wizard-200.bmp
WizardSmallImageFile=installer\small-100.bmp,installer\small-150.bmp,installer\small-200.bmp
Compression=lzma2/max
SolidCompression=yes
; Junction is closed by the code below (Junction.exe --quit), not by Windows' Restart Manager.
CloseApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An update replaces the app's files completely, so nothing from an older version lingers.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\Junction\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Junction"; Filename: "{app}\Junction.exe"
Name: "{autodesktop}\Junction"; Filename: "{app}\Junction.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Junction.exe"; Description: "{cm:LaunchProgram,Junction}"; Flags: nowait postinstall skipifsilent
; An update started from inside Junction runs silently with /relaunch=1: open Junction again afterwards.
Filename: "{app}\Junction.exe"; Flags: nowait; Check: Relaunch

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  WebView2Key = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Download = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

function HasVersion(Root: Integer; Key: String): Boolean;
var
  V: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', V) and (V <> '') and (V <> '0.0.0.0');
end;

// Junction draws its window with Microsoft's WebView2, which comes with Windows 11 and most of Windows 10.
function WebView2Installed: Boolean;
begin
  Result := HasVersion(HKLM, 'SOFTWARE\WOW6432Node\' + WebView2Key) or HasVersion(HKLM, 'SOFTWARE\' + WebView2Key)
    or HasVersion(HKCU, 'Software\' + WebView2Key);
end;

function InitializeSetup: Boolean;
var
  Code: Integer;
begin
  Result := True;
  if not WebView2Installed and not WizardSilent then
    if MsgBox('Junction needs Microsoft Edge WebView2, which isn''t on this PC yet.'#13#10#13#10 +
              'Open Microsoft''s download page now? You can finish installing Junction first and install WebView2 after.',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', WebView2Download, '', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

// Asks a running Junction to quit (it saves what it's doing) and waits until it has.
procedure QuitJunction;
var
  Exe: String;
  Code: Integer;
begin
  Exe := ExpandConstant('{app}\Junction.exe');
  if FileExists(Exe) then
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function Relaunch: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:relaunch|0}') = '1');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  QuitJunction;
  Result := '';
end;

function InitializeUninstall: Boolean;
begin
  QuitJunction;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Cmd: String;
begin
  // "Start with Windows" points at this copy: take it out, but leave any other Junction's alone.
  if CurUninstallStep = usUninstall then
    if RegQueryStringValue(HKCU, RunKey, 'Junction', Cmd) and
       (Pos(Lowercase(ExpandConstant('{app}')), Lowercase(Cmd)) > 0) then
      RegDeleteValue(HKCU, RunKey, 'Junction');
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox('Also remove your Junction settings, keys and play time from this PC?'#13#10#13#10 +
              'Choose No to keep them for when you install Junction again.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      DelTree(ExpandConstant('{userappdata}\Junction'), True, True, True);
      DelTree(ExpandConstant('{localappdata}\Junction'), True, True, True);
    end;
end;
