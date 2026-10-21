; Inno Setup script for OpenHamShack-Packet-Radio-<v>-windows-x64.exe. Run
; by tools\build-windows.ps1, which passes:
;   /DAppVersion=<VERSION>  /DDistDir=<PyInstaller's dist\shack-packet>
;   /DIconFile=<stage\icons\app.ico>  /DLicenseFile=<stage\LICENSE>
;   /DOutDir=<out\>  /DSign (unless -NoSign)
;
; Generalised from ohs-libs' packaging/windows/shack-app.iss (the kit's
; template for a plain app), with a console-mode shortcut added as
; openhamshack-nb's and openhamshack-datv's installers have: the kit's template
; assumes one windowed .exe, and this app's Windows build freezes a second,
; console-mode one (packaging/qt-app.spec) so --self-test has somewhere to
; print PASS/FAIL. The kit's own windows/build-windows.ps1 is not used here
; at all - it freezes with the kit's spec, which excludes PyQt6
; (HANDOVER.md) - so this app keeps its own Windows pipeline, as the other
; Qt apps (NB, HAM, DATV, ATV) do.
;
; Installs per user by default (PrivilegesRequired=lowest): no UAC prompt,
; into %LOCALAPPDATA%\Programs\OpenHamShack Packet Radio, which is enough
; for a program that writes nothing outside the operator's own profile.
; The wizard (or /ALLUSERS on the command line) still offers Program Files
; for a shared machine.
;
; Uninstalling removes the program and its shortcuts but leaves
; %APPDATA%\shack-packet (settings, logs, the mailbox and the chat log) as
; the .deb leaves ~/.config and ~/.local/share: reinstalling must not cost
; an operator their station or their mail.
;
; Windows Defender Firewall (0.2): firewall.cmd, installed beside the
; program, removes every rule naming its two .exes - the Block rules Windows
; writes itself when its first-run prompt is cancelled included - and adds
; inbound Allow rules for private and public networks; uninstalling removes
; them. firewall.cmd says why. Changing the firewall needs an administrator,
; which a per-user install is not, so the installer asks for it once,
; through UAC, for that one step (RunFirewall below); saying no installs
; the program all the same, without the rules, and the log says so. An
; all-users install is already elevated and runs it directly. The step is
; the "firewall" task, ticked by default.
;
; Signed as 360 Technology, as NB's, HAM's and DATV's are (sign.ps1 says
; how); -NoSign (tools\build-windows.ps1) makes a local test build only,
; never released.

#ifndef AppVersion
  #error Pass /DAppVersion=<version> (tools\build-windows.ps1 does)
#endif

[Setup]
; Made once, 18/10/2026 (shack-app.toml [windows] app_guid), and never
; changed: it is how a newer installer finds the old install to upgrade in
; place, and how Windows matches the uninstaller.
AppId={{572039E7-A9C6-4425-B015-ADA466113B20}
AppName=OpenHamShack Packet Radio
AppVersion={#AppVersion}
AppVerName=OpenHamShack Packet Radio {#AppVersion}
; The publisher Windows shows in Apps is the name on the signature, as for
; NB, HAM and DATV: a different name there would look like a tampered file.
AppPublisher=360 Technology
AppPublisherURL=https://openhamshack.com/
AppComments=APRS, AX.25 chat, a mailbox and IP over packet radio
DefaultDirName={autopf}\OpenHamShack\Packet Radio
DefaultGroupName=OpenHamShack Packet Radio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=OpenHamShack-Packet-Radio-{#AppVersion}-windows-x64
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\shack-packet.exe
UninstallDisplayName=OpenHamShack Packet Radio {#AppVersion}
LicenseFile={#LicenseFile}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Close a running copy before replacing its files, rather than leaving a
; half-updated folder behind a locked DLL.
CloseApplications=yes
#ifdef Sign
; Signed as 360 Technology: tools\build-windows.ps1 defines Sign and the
; "ohssign" tool (packaging\windows\sign.ps1) unless run with -NoSign. Inno
; Setup then signs the uninstaller before packing it - so Windows names a
; publisher when it is run from Apps, as for setup.exe - and setup.exe
; itself once it is written. The program's own .exes arrive in DistDir
; already signed.
SignTool=ohssign
SignedUninstaller=yes
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "firewall"; Description: "Allow the program through Windows Defender Firewall on private and public networks (asks for administrator rights once)"; GroupDescription: "Network:"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Beside the .exes, because it finds them by its own folder (%~dp0), and so
; an operator who said no to UAC can run "firewall.cmd add" later.
Source: "firewall.cmd"; DestDir: "{app}"; Flags: ignoreversion

[UninstallDelete]
; Written by CurStepChanged when the rules went in; not in [Files], so not
; removed by itself.
Type: files; Name: "{app}\firewall-rules-added"

[InstallDelete]
; An upgrade replaces the whole bundle: a DLL a newer PyInstaller no longer
; ships must not linger and be picked up instead of the new one.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\OpenHamShack Packet Radio"; Filename: "{app}\shack-packet.exe"; Comment: "APRS, AX.25 chat, a mailbox and IP over packet radio"; AppUserModelID: "OpenHamShack.Packet"
Name: "{group}\OpenHamShack Packet Radio (console)"; Filename: "{app}\shack-packet-console.exe"; Comment: "The same program with a console, for --self-test and diagnosing start-up problems"
Name: "{group}\Uninstall OpenHamShack Packet Radio"; Filename: "{uninstallexe}"
Name: "{autodesktop}\OpenHamShack Packet Radio"; Filename: "{app}\shack-packet.exe"; Tasks: desktopicon; AppUserModelID: "OpenHamShack.Packet"

[Run]
Filename: "{app}\shack-packet.exe"; Description: "{cm:LaunchProgram,OpenHamShack Packet Radio}"; Flags: nowait postinstall skipifsilent
; The program's own updater (src/shack_packet_app/update.py, INNO_ARGS)
; runs this installer after the program has exited, with
;   /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCH=1
; An unattended install skips the postinstall entry above (skipifsilent),
; so nothing would start the program again; /RELAUNCH=1 is our own switch,
; read below, so only an install started by the updater does - never a
; silent install an administrator runs for another reason.
; runasoriginaluser: if Setup elevated itself (an all-users install), the
; program still comes back as the operator. openhamshack-nb's self-update.iss,
; inlined: this installer has no other #include to share it with.
Filename: "{app}\shack-packet.exe"; Flags: nowait runasoriginaluser; Check: RelaunchAfterUpdate

[Code]
const
  FirewallMarker = 'firewall-rules-added';

// Runs firewall.cmd in {app} with Mode (add, remove or check). check never
// needs an administrator, so it is always run as the operator. add and
// remove are run directly when Setup is already elevated (an all-users
// install), and otherwise through one UAC prompt ("runas"). True only when
// the script ran and said 0; a refused prompt is logged, never fatal.
function RunFirewall(const Mode: String): Boolean;
var
  Code: Integer;
  Params: String;
begin
  // cmd /c ""path with spaces\firewall.cmd" mode": cmd strips the outer
  // pair and keeps the inner quotes round the path.
  Params := '/c ""' + ExpandConstant('{app}\firewall.cmd') + '" ' + Mode + '"';
  if (Mode = 'check') or IsAdmin() then
    Result := Exec(ExpandConstant('{cmd}'), Params, '', SW_HIDE, ewWaitUntilTerminated, Code)
  else
    Result := ShellExec('runas', ExpandConstant('{cmd}'), Params, '', SW_HIDE, ewWaitUntilTerminated, Code);
  if not Result then
    Log('Firewall ' + Mode + ': not run (' + SysErrorMessage(Code) + ')')
  else begin
    Result := Code = 0;
    Log('Firewall ' + Mode + ': exit code ' + IntToStr(Code));
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep <> ssPostInstall) or not WizardIsTaskSelected('firewall') then
    exit;
  // An unattended update over an install that already has the rules (the
  // same folder, so the same programs) asks for nothing: a UAC prompt in
  // the middle of a self-update, for rules that are already right, would
  // only teach people to click yes.
  if WizardSilent() and RunFirewall('check') then begin
    Log('Firewall: rules already there; unattended, so not asking again');
    SaveStringToFile(ExpandConstant('{app}\' + FirewallMarker), 'yes', False);
    exit;
  end;
  if RunFirewall('add') then
    SaveStringToFile(ExpandConstant('{app}\' + FirewallMarker), 'yes', False)
  else
    Log('Firewall: rules not added; the program is installed without them (firewall.cmd add, as an administrator, adds them later)');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Before the files go, while firewall.cmd is still there. Asks for UAC
  // only when this install added rules or they are there anyway, so an
  // operator who never allowed them is not asked on the way out.
  if CurUninstallStep <> usUninstall then
    exit;
  if FileExists(ExpandConstant('{app}\' + FirewallMarker)) or RunFirewall('check') then begin
    if not RunFirewall('remove') then
      Log('Firewall: rules not removed (the prompt was refused?); firewall.cmd remove, as an administrator, removes them');
  end;
end;

function RelaunchAfterUpdate(): Boolean;
begin
  Result := WizardSilent() and (ExpandConstant('{param:RELAUNCH|0}') = '1');
  if Result then
    Log('Started by the program''s own updater: starting it again');
end;
