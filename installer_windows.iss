; FTHR Clips — Windows Installer (Inno Setup 6)
; Windows-only: bundles the DXGI capture engine. Does NOT include the Linux engine.
; Build: iscc installer_windows.iss
; Prerequisite: redist\vc_redist.x64.exe — https://aka.ms/vs/17/release/vc_redist.x64.exe

#define MyAppName      "FTHR Clips"
#define MyAppVersion   "1.0.0-alpha"
#define MyAppPublisher "FTHR Community"
#define MyAppExeName   "FTHRClips.exe"
#define MyAppURL       "https://github.com/fthr/clips"
#define MyAppId        "{{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"
; The source is the Microsoft permalink documented in BUILDING.md.  Its binary
; version is read at compile time, so the runtime check never claims that an
; older VC++ runtime is sufficient for the redistributable actually bundled.
#define VCRedistVersion GetVersionNumbersString(AddBackslash(SourcePath) + "redist\vc_redist.x64.exe")

#if VCRedistVersion == ""
  #error "redist\\vc_redist.x64.exe has no readable version resource; run python tools/fetch_third_party.py --vcredist"
#endif

#ifndef BundleDir
#define BundleDir "dist\FTHRClips"
#endif

[Setup]
; Do not change this AppId. It is the update/uninstall identity of the
; already-installed public-alpha predecessor.
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=Copyright (c) 2026 FTHR Community
DefaultDirName={autopf}\FTHRClips
DefaultGroupName={#MyAppName}
UsePreviousAppDir=yes
UsePreviousGroup=yes
OutputBaseFilename=FTHRClips-Setup-{#MyAppVersion}-x64
OutputDir=Output

; Keep Installer metadata visible and consistent with the frozen executable.
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=FTHR Clips Windows Setup
VersionInfoProductName={#MyAppName}
; PE ProductVersion is numeric; keep the alpha suffix in the textual field.
VersionInfoProductVersion=1.0.0.0
VersionInfoTextVersion={#MyAppVersion}
VersionInfoVersion=1.0.0.0

; Compression — maximum, solid
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=2

; Visual style
WizardStyle=modern
WizardSizePercent=120
WizardImageFile=installer_assets\wizard_banner.bmp
WizardSmallImageFile=installer_assets\wizard_small.bmp

; Icon
SetupIconFile=FTHR_UI\assets\fthr_logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

; Platform
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; Never force-kill a capture session. Restart Manager offers to close the
; processes holding FTHR files; an unresolved lock leaves the user in control.
CloseApplications=yes
CloseApplicationsFilter=FTHRClips.exe
RestartApplications=no

; Uninstall
UninstallDisplayName={#MyAppName}
CreateUninstallRegKey=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
english.WelcomeLabel1=Welcome to FTHR Clips Setup
english.WelcomeLabel2=FTHR Clips is a lightweight game capture tool for Windows.%n%nThis will install or update version {#MyAppVersion} on your computer. An existing matching version is repaired in place.%n%nClose FTHR Clips before continuing so capture can shut down cleanly.
english.FinishedHeadingLabel=FTHR Clips is installed!
english.FinishedLabel=FTHR Clips has been installed on your computer. Use the desktop shortcut or Start Menu to launch it.%n%nFor global hotkeys to work, no additional setup is required on Windows.

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[InstallDelete]
; These are exact remnants from the pre-AUDIT-005/013 Windows bundle. Leaving
; them beside a current PySide6/LGPL payload would keep excluded GPL FFmpeg and
; PyQt6 bytes installed after an otherwise normal in-place update. Do not turn
; this into a broad {app} cleanup: only these project-known stale paths are safe
; to remove before the current files are copied.
Type: filesandordirs; Name: "{app}\_internal\imageio_ffmpeg"
Type: filesandordirs; Name: "{app}\_internal\imageio_ffmpeg-*.dist-info"
Type: filesandordirs; Name: "{app}\_internal\PyQt6"
Type: filesandordirs; Name: "{app}\_internal\PyQt6-*.dist-info"

[Files]
; Windows bundle — produced by: pyinstaller FTHR.spec --clean --noconfirm
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Licence paperwork (AUDIT-005/AUDIT-013). The installed app carries the
; project licence and all bundled third-party notices locally.
Source: "LICENSE";                DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "redist\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#MyAppName}";           Filename: "{app}\{#MyAppExeName}"; \
    Comment: "Launch FTHR Clips game capture"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{group}\Licences and Third-Party Notices"; Filename: "{app}\THIRD_PARTY_NOTICES.md"
Name: "{commondesktop}\{#MyAppName}";   Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon; Comment: "Launch FTHR Clips game capture"

[Run]
; VC++ Runtime: only run when the installed 2015–2022 x64 runtime is missing
; or older than the Microsoft redistributable bundled with this installer.
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/quiet /norestart"; \
    StatusMsg: "Installing Visual C++ Runtime..."; Flags: waituntilterminated runhidden; \
    Check: NeedsVCRedist

; Optional launch after install
Filename: "{app}\{#MyAppExeName}"; \
    Description: "Launch {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; Optional packages are activated outside Program Files only after in-app
; consent. Remove those executables on uninstall; uploader preferences remain
; governed by the existing settings-cleanup prompt below.
Type: filesandordirs; Name: "{localappdata}\FTHR Clips\plugins"
; Clips, screenshots, exports, and sidecar manifests are deliberately outside
; the installer tree at %USERPROFILE%\FTHR_Clips and are never an uninstall
; target. The app-owned settings/cache directory is opt-in only.
Type: filesandordirs; Name: "{code:UserProfilePath}\.fthr"; Check: ShouldRemoveSettingsAndCache

[Code]
const
  ProductUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}_is1';
  LegacyUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\FTHR Clips';
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RunValueName = 'FTHRClips';
  LegacyInstallDir = '{localappdata}\Programs\FTHR Clips';
  LegacyProgramsDir = '{userprograms}\FTHR Clips';
  FTHRMutex = 'Local\FTHR_Clips_SingleInstance_v1';

var
  InstalledVersion: String;
  PreserveAutostart: Boolean;
  RemoveSettingsAndCache: Boolean;
  LegacyCleanupPage: TInputOptionWizardPage;

function NextVersionNumber(const Value: String; var Position: Integer): Integer;
var
  Digits: String;
begin
  Digits := '';
  while (Position <= Length(Value)) and
        ((Value[Position] < '0') or (Value[Position] > '9')) do
    Position := Position + 1;
  while (Position <= Length(Value)) and
        (Value[Position] >= '0') and (Value[Position] <= '9') do begin
    Digits := Digits + Value[Position];
    Position := Position + 1;
  end;
  Result := StrToIntDef(Digits, 0);
end;

function CompareDottedVersion(const Left, Right: String): Integer;
var
  Part, LeftPos, RightPos, LeftValue, RightValue: Integer;
begin
  LeftPos := 1;
  RightPos := 1;
  for Part := 1 to 4 do begin
    LeftValue := NextVersionNumber(Left, LeftPos);
    RightValue := NextVersionNumber(Right, RightPos);
    if LeftValue < RightValue then begin
      Result := -1;
      exit;
    end;
    if LeftValue > RightValue then begin
      Result := 1;
      exit;
    end;
  end;
  Result := 0;
end;

function ReadInstalledVersion: String;
begin
  Result := '';
  if not RegQueryStringValue(HKLM64, ProductUninstallKey, 'DisplayVersion', Result) then
    RegQueryStringValue(HKCU, ProductUninstallKey, 'DisplayVersion', Result);
end;

function IsDowngradeAllowed: Boolean;
begin
  Result := Pos('/ALLOWDOWNGRADE', Uppercase(GetCmdTail)) > 0;
end;

function HasOwnedAutostart: Boolean;
var
  Command: String;
begin
  Result := False;
  if RegQueryStringValue(HKCU, RunKey, RunValueName, Command) then begin
    Command := Lowercase(Command);
    Result := (Pos('fthrclips.exe', Command) > 0) and
              (Pos('--background', Command) > 0);
  end;
end;

function LegacyInstallPresent: Boolean;
var
  LegacyLocation: String;
begin
  Result := RegQueryStringValue(HKCU, LegacyUninstallKey, 'InstallLocation', LegacyLocation) and
            SameText(LegacyLocation, ExpandConstant(LegacyInstallDir));
end;

function LegacyCleanupRequested: Boolean;
begin
  Result := Pos('/REMOVELEGACY', Uppercase(GetCmdTail)) > 0;
  if LegacyCleanupPage <> nil then
    Result := Result or LegacyCleanupPage.Values[0];
end;

function NeedsVCRedist: Boolean;
var
  Installed: Cardinal;
  InstalledVersion: String;
begin
  Result := True;
  if not RegQueryDWordValue(HKLM64,
      'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
      'Installed', Installed) then
    exit;
  if Installed <> 1 then
    exit;
  if not RegQueryStringValue(HKLM64,
      'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
      'Version', InstalledVersion) then
    exit;
  Result := CompareDottedVersion(InstalledVersion, '{#VCRedistVersion}') < 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if (InstalledVersion <> '') and
     (CompareDottedVersion(InstalledVersion, '{#MyAppVersion}') > 0) and
     not IsDowngradeAllowed then begin
    Result := 'A newer FTHR Clips version (' + InstalledVersion +
      ') is already installed. This installer will not downgrade it. ' +
      'Use a matching or newer installer, or explicitly pass /ALLOWDOWNGRADE.';
  end;
end;

procedure InitializeWizard;
begin
  if LegacyInstallPresent then begin
    LegacyCleanupPage := CreateInputOptionPage(wpSelectTasks,
      'Older FTHR installation found',
      'An older per-user FTHR test installation is still registered.',
      'Select this only if you want to remove that obsolete installation. ' +
      'It removes its exact install folder, registry entry, and its two known ' +
      'per-user Start Menu shortcuts. It never removes clips or current settings.',
      False, False);
    LegacyCleanupPage.Add('Remove the obsolete per-user FTHR test installation');
    LegacyCleanupPage.Values[0] := False;
  end;
end;

procedure RemoveLegacyInstall;
var
  LegacyDir, ProgramsDir: String;
begin
  if not LegacyCleanupRequested then
    exit;
  if CheckForMutexes(FTHRMutex) then begin
    Log('Legacy cleanup skipped: FTHR Clips is still running.');
    if not WizardSilent then
      MsgBox('The old FTHR installation is still running, so it was left in place. ' +
        'Close FTHR Clips and run this installer again to remove it.',
        mbInformation, MB_OK);
    exit;
  end;
  if not LegacyInstallPresent then begin
    Log('Legacy cleanup skipped: expected legacy registry record/path no longer matched.');
    exit;
  end;
  LegacyDir := ExpandConstant(LegacyInstallDir);
  ProgramsDir := ExpandConstant(LegacyProgramsDir);
  if DelTree(LegacyDir, True, True, True) then begin
    RegDeleteKeyIncludingSubkeys(HKCU, LegacyUninstallKey);
    DeleteFile(AddBackslash(ProgramsDir) + 'FTHR Clips.lnk');
    DeleteFile(AddBackslash(ProgramsDir) + 'Uninstall FTHR Clips.lnk');
    RemoveDir(ProgramsDir);
    Log('Removed the selected legacy per-user FTHR installation.');
  end else begin
    Log('Legacy cleanup failed without removing its registry record.');
    if not WizardSilent then
      MsgBox('The old FTHR installation could not be removed and was left registered. ' +
        'No clips or settings were touched.', mbError, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if PreserveAutostart then begin
      RegWriteStringValue(HKCU, RunKey, RunValueName,
        '"' + ExpandConstant('{app}\{#MyAppExeName}') + '" --background');
      Log('Preserved FTHR Clips autostart with the current install path.');
    end;
    RemoveLegacyInstall;
  end;
end;

function InitializeSetup: Boolean;
begin
  InstalledVersion := ReadInstalledVersion;
  PreserveAutostart := HasOwnedAutostart;
  RemoveSettingsAndCache := False;
  Result := True;
end;

function ShouldRemoveSettingsAndCache: Boolean;
begin
  Result := RemoveSettingsAndCache;
end;

function UserProfilePath(Param: String): String;
begin
  Result := GetEnv('USERPROFILE');
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  RemoveSettingsAndCache := False;
  if not UninstallSilent then
    RemoveSettingsAndCache :=
      SuppressibleMsgBox(
        'Keep your clips, screenshots, exports, and sidecar files?' + #13#10 + #13#10 +
        'Choose Yes only to also remove FTHR Clips settings, logs, hotkeys, ' +
        'themes, and cache from %USERPROFILE%\\.fthr. Your clip folder is ' +
        'never removed by this installer.',
        mbConfirmation, MB_YESNO, IDNO) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    RegDeleteValue(HKCU, RunKey, RunValueName);
    Log('Removed the FTHR Clips per-user autostart value.');
  end;
end;
