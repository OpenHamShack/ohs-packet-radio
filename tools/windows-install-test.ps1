<#
.SYNOPSIS
Install a Packet Radio installer on this Windows machine, prove the
installed program, and uninstall it again (tools/release-windows.sh).

.DESCRIPTION
    powershell -ExecutionPolicy Bypass -File tools\windows-install-test.ps1 -Setup <setup.exe> -Version <v> -Scratch <dir>

What 0.1.1 and 0.1.2 were checked with by hand on the build VM, written
down: a silent per-user install (/CURRENTUSER), into
%LOCALAPPDATA%\Programs\OpenHamShack\Packet Radio; on the INSTALLED copy,
every executable's signature (360 Technology, timestamped) and the
console twin's --version, --self-test, --library-check (every library from
the bundle's own folders) and --diagnostics-test (the Help menu's bundle,
written as a zip by the real Windows build); then a silent uninstall,
which must leave no program folder behind.

The firewall task is left out (/MERGETASKS="!firewall"): it changes the
machine's firewall, and has a test of its own
(tools\windows-firewall-test.ps1). Refuses to run where Packet Radio is
already installed, so it never uninstalls somebody's own copy. Settings
and data go to -Scratch, which is removed at the end.

Windows PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Scratch
)
$ErrorActionPreference = "Stop"
$Dir = Join-Path $env:LOCALAPPDATA "Programs\OpenHamShack\Packet Radio"
$Console = Join-Path $Dir "shack-packet-console.exe"

function Ok([string]$what) { Write-Host "ok $what" }

# The program, its output and exit code. Windows PowerShell 5.1 turns a
# native program's stderr into an error record under 2>&1, and "Stop"
# makes that fatal (Qt's harmless DPI warning on an SSH session did it),
# so the program runs with "Continue" and is judged by its exit code.
function Native([string[]]$argv) {
    $ErrorActionPreference = "Continue"
    $text = & $Console @argv 2>&1 | ForEach-Object { "$_" } | Out-String
    return @($text, $LASTEXITCODE)
}
function Fail([string]$what) { Write-Host "FAIL $what"; throw $what }

function AssertSigned([string]$path) {
    $s = Get-AuthenticodeSignature -LiteralPath $path
    $name = Split-Path -Leaf $path
    if ($s.Status -ne "Valid") { Fail "$name signature: $($s.Status)" }
    if ($s.SignerCertificate.Subject -notmatch '(^|, )O=360 Technology(,|$)') { Fail "$name is signed by $($s.SignerCertificate.Subject)" }
    if (-not $s.TimeStamperCertificate) { Fail "$name has no timestamp" }
    Ok "$name signed by 360 Technology, timestamped"
}

if (Test-Path $Dir) { Fail "Packet Radio is already installed in $Dir; not touching it" }
New-Item -ItemType Directory -Force -Path $Scratch | Out-Null
AssertSigned $Setup

$log = Join-Path $Scratch "install.log"
$p = Start-Process $Setup -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /MERGETASKS=`"!firewall`" /LOG=`"$log`"" -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "the installer exited $($p.ExitCode) (log $log)" }
if (-not (Test-Path $Console)) { Fail "no $Console after the install" }
Ok "installed silently into $Dir"

$passed = $false
try {
foreach ($exe in @("shack-packet.exe", "shack-packet-console.exe", "unins000.exe")) { AssertSigned (Join-Path $Dir $exe) }

$env:SHACK_PACKET_CONF = Join-Path $Scratch "conf"
$env:SHACK_PACKET_DATA = Join-Path $Scratch "data"
try {
    $v = (Native @("--version"))[0].Trim()
    if ($v -notmatch [regex]::Escape($Version)) { Fail "--version says '$v', not $Version" }
    Ok "--version: $v"

    $t, $code = Native @("--self-test")
    if ($code -ne 0 -or $t -notmatch "self-test: PASS") { Write-Host $t; Fail "--self-test (exit $code)" }
    Ok "--self-test: PASS"

    $l, $code = Native @("--library-check")
    Write-Host $l
    if ($code -ne 0 -or $l -notmatch "library-check: PASS") { Fail "--library-check (exit $code)" }
    # Regular expressions: "\\" is one backslash.
    foreach ($want in @("libiio 0\.26 from .*Packet Radio\\_internal\\libiio", "Packet Radio\\_internal\\rtlsdr\\rtlsdr\.dll",
                        "Packet Radio\\_internal\\hackrf\\hackrf-0\.dll")) {
        if ($l -notmatch $want) { Fail "--library-check did not load the installed bundle's copy ($want)" }
    }
    Ok "--library-check: PASS, every library from the installed bundle"

    $zip = Join-Path $Scratch "diagnostics.zip"
    $d, $code = Native @("--diagnostics-test", $zip)
    if ($code -ne 0 -or $d -notmatch "diagnostics-test: PASS" -or -not (Test-Path $zip)) { Write-Host $d; Fail "--diagnostics-test (exit $code)" }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $z = [IO.Compression.ZipFile]::OpenRead($zip)
    $names = ($z.Entries | ForEach-Object { $_.FullName }) -join ", "
    $z.Dispose()
    Ok "--diagnostics-test: PASS ($names)"
} finally {
    Remove-Item Env:SHACK_PACKET_CONF, Env:SHACK_PACKET_DATA
}

# The uninstaller copies itself to %TEMP% and returns at once, so wait for
# the folder to go rather than for the process.
$u = Start-Process (Join-Path $Dir "unins000.exe") -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART" -Wait -PassThru
$deadline = (Get-Date).AddSeconds(120)
while ((Test-Path $Console) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
if (Test-Path $Console) { Fail "the program is still there after uninstalling" }
Start-Sleep -Seconds 3
if (Test-Path $Dir) {
    $left = (Get-ChildItem -Recurse $Dir | ForEach-Object { $_.FullName }) -join ", "
    if ($left) { Fail "the uninstall left $left" }
    Remove-Item -Force $Dir
}
$parent = Split-Path -Parent $Dir
if ((Test-Path $parent) -and -not (Get-ChildItem $parent)) { Remove-Item -Force $parent }
Ok "uninstalled; nothing left in $Dir"
$passed = $true
} finally {
    # A failed check must not leave the program installed: the next run
    # would refuse, as it refuses anybody's own copy (found on 0.2.0's run).
    if (-not $passed -and (Test-Path (Join-Path $Dir "unins000.exe"))) {
        Start-Process (Join-Path $Dir "unins000.exe") -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART" -Wait | Out-Null
        $deadline = (Get-Date).AddSeconds(120)
        while ((Test-Path $Console) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
        Write-Host "note: uninstalled after the failure"
    }
}
Remove-Item -Recurse -Force $Scratch
Write-Host "install-test: PASS"
exit 0
