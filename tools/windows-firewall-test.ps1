<#
.SYNOPSIS
Test the installer's Windows Defender Firewall step (packaging\windows\
firewall.cmd and the "firewall" task in shack-packet.iss) on a Windows
machine, over an upgrade from an earlier release.

.DESCRIPTION
    powershell -ExecutionPolicy Bypass -File tools\windows-firewall-test.ps1 -Setup <new setup.exe> -Previous <older setup.exe> -Scratch <dir>

Run by tools\release-windows.sh --firewall-test, over SSH as an
administrator. Each step reads the firewall itself (Get-NetFirewallRule,
Get-NetFirewallApplicationFilter) and the installer's own log:

 1. The previous release, which has no firewall step, installed silently
    for the current user; then the rules Windows writes itself when its
    first-run prompt is cancelled or half-answered - a Block rule for each
    of the two programs on TCP and UDP - put in by hand, as Windows would.
 2. The new release installed over it, silently, task ticked (its
    default). An administrator's SSH session is elevated, so the step runs
    firewall.cmd directly: every Block rule must be gone and exactly our
    two Allow rules there, inbound, enabled, private and public, one per
    .exe, by its path in the install folder.
 3. The same installer again, silently, as the program's own updater runs
    it: the rules are there, so the log must say it is not asking again,
    and the rules must be unchanged.
 4. Uninstall: every rule naming the two programs must be gone.
 5. A refused prompt: the new release installed with a non-administrator
    token (runas /trustlevel:0x20000, which needs no password). Now the
    step has to ask through UAC. In a session with no desktop (SSH) no
    consent can be given: on 0.2.0's run Windows started firewall.cmd
    without elevation and it failed (exit 1), which the installer treats as
    a refusal. The program must still install, without rules, the log must
    say how to add them later, and the installer must have asked exactly
    once (one "Firewall add" in its log). Then uninstalled again, which
    must not ask at all (no rules, no marker).

What this cannot show: the UAC dialog itself on a desktop. Seeing it
appear once, and choosing Yes on it, needs a person at an interactive
session (TESTER-GUIDE "Windows firewall").

Refuses to run where Packet Radio is already installed. Leaves no rule and
no program behind, whatever happens. Windows PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [Parameter(Mandatory = $true)][string]$Previous,
    [Parameter(Mandatory = $true)][string]$Scratch
)
$ErrorActionPreference = "Stop"
$Dir = Join-Path $env:LOCALAPPDATA "Programs\OpenHamShack\Packet Radio"
$Exes = @("shack-packet.exe", "shack-packet-console.exe")
$Ours = "OpenHamShack Packet Radio"

function Ok([string]$what) { Write-Host "ok $what" }
function Fail([string]$what) { Write-Host "FAIL $what"; throw $what }
function Note([string]$what) { Write-Host "note $what" }

# Every inbound or outbound rule whose application filter is one of our
# two programs, matched by path as firewall.cmd matches them.
function RulesFor() {
    $paths = $Exes | ForEach-Object { (Join-Path $Dir $_).ToLower() }
    $out = @()
    foreach ($f in Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue) {
        $prog = [Environment]::ExpandEnvironmentVariables([string]$f.Program).ToLower()
        if ($paths -contains $prog) { $out += ($f | Get-NetFirewallRule) }
    }
    return $out
}

function Install([string]$exe, [string]$log, [string]$extra = "") {
    $p = Start-Process $exe -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER $extra /LOG=`"$log`"" -Wait -PassThru
    if ($p.ExitCode -ne 0) { Fail "$(Split-Path -Leaf $exe) exited $($p.ExitCode) (log $log)" }
}

function Uninstall() {
    $u = Join-Path $Dir "unins000.exe"
    if (-not (Test-Path $u)) { return }
    Start-Process $u -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART" -Wait | Out-Null
    $deadline = (Get-Date).AddSeconds(120)
    while ((Test-Path (Join-Path $Dir $Exes[0])) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
    Start-Sleep -Seconds 3
}

function LogLines([string]$log, [string]$pattern) {
    return @(Get-Content $log | Where-Object { $_ -match $pattern })
}

if (Test-Path $Dir) { Fail "Packet Radio is already installed in $Dir; not touching it" }
New-Item -ItemType Directory -Force -Path $Scratch | Out-Null
try {
    # 1. The previous release, and Windows' own Block rules.
    Install $Previous (Join-Path $Scratch "1-previous.log")
    Ok "previous release installed: $(Split-Path -Leaf $Previous)"
    foreach ($e in $Exes) {
        foreach ($proto in @("TCP", "UDP")) {
            New-NetFirewallRule -DisplayName $e -Direction Inbound -Action Block -Protocol $proto -Profile Public `
                -Program (Join-Path $Dir $e) | Out-Null
        }
    }
    $blocks = @(RulesFor | Where-Object { $_.Action -eq "Block" })
    if ($blocks.Count -ne 4) { Fail "expected 4 Block rules planted, found $($blocks.Count)" }
    Ok "4 Block rules planted, as Windows writes them when its prompt is cancelled"

    # 2. The upgrade, elevated: firewall.cmd runs directly.
    $log2 = Join-Path $Scratch "2-upgrade.log"
    Install $Setup $log2
    $rules = @(RulesFor)
    $blocks = @($rules | Where-Object { $_.Action -eq "Block" })
    if ($blocks.Count -ne 0) { Fail "$($blocks.Count) Block rule(s) survived the upgrade" }
    Ok "every Block rule for the two programs removed by the upgrade"
    if ($rules.Count -ne 2) { Fail "expected exactly our 2 rules, found $($rules.Count): $(($rules | ForEach-Object DisplayName) -join ', ')" }
    foreach ($e in $Exes) {
        $r = @($rules | Where-Object { $_.DisplayName -eq "$Ours ($e)" })
        if ($r.Count -ne 1) { Fail "no rule '$Ours ($e)'" }
        $r = $r[0]
        $prof = [string]$r.Profile
        if ($r.Action -ne "Allow" -or $r.Direction -ne "Inbound" -or [string]$r.Enabled -ne "True" -or
            $prof -notmatch "Private" -or $prof -notmatch "Public" -or $prof -match "Domain") {
            Fail "'$($r.DisplayName)' is $($r.Direction) $($r.Action), enabled $($r.Enabled), profiles $prof"
        }
        Ok "'$($r.DisplayName)': inbound allow, enabled, profiles $prof"
    }
    if (-not (Test-Path (Join-Path $Dir "firewall-rules-added"))) { Fail "no firewall-rules-added marker" }
    $adds = @(LogLines $log2 "Firewall add: exit code 0")
    if ($adds.Count -ne 1) { Fail "the upgrade ran firewall.cmd add $($adds.Count) times" }
    Ok "upgrade over the previous release: firewall.cmd add ran once, exit 0"

    # 3. An unattended update with the rules in place asks nothing.
    $log3 = Join-Path $Scratch "3-update.log"
    Install $Setup $log3 "/RELAUNCH=0"
    if ((LogLines $log3 "rules already there; unattended, so not asking again").Count -ne 1) { Fail "the silent update did not skip the step" }
    if ((LogLines $log3 "Firewall add").Count -ne 0) { Fail "the silent update ran firewall.cmd add" }
    if (@(RulesFor).Count -ne 2) { Fail "the silent update changed the rules" }
    Ok "silent update with the rules in place: not asked again, rules unchanged"

    # 4. Uninstall removes them.
    Uninstall
    if (Test-Path (Join-Path $Dir $Exes[0])) { Fail "the uninstall left the program" }
    $left = @(RulesFor)
    if ($left.Count -ne 0) { Fail "the uninstall left $($left.Count) rule(s)" }
    Ok "uninstall removed both rules"

    # 5. A refused prompt.
    $log5 = Join-Path $Scratch "5-refused.log"
    $args5 = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /LOG=`"$log5`""
    & runas.exe /trustlevel:0x20000 "`"$Setup`" $args5" | Out-Null
    # runas returns at once; the installer (and the .tmp it unpacks) run on.
    $deadline = (Get-Date).AddSeconds(240)
    Start-Sleep -Seconds 5
    while ((Get-Date) -lt $deadline) {
        $busy = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ($_.Path -like "*OpenHamShack-Packet-Radio*" -or $_.Path -like "*\is-*\*.tmp") })
        if ($busy.Count -eq 0 -and (Test-Path $log5) -and (LogLines $log5 "Log closed|Installation process succeeded").Count -gt 0) { break }
        Start-Sleep -Seconds 3
    }
    if (-not (Test-Path (Join-Path $Dir $Exes[0]))) { Fail "with the prompt refused, the program was not installed (log $log5)" }
    Ok "with the prompt refused, the program installed all the same"
    $asked = @(LogLines $log5 "Firewall add")   # @(): one line would come back as a bare string
    if ($asked.Count -ne 1) { Fail "the installer asked $($asked.Count) times (expected once): $($asked -join ' | ')" }
    Ok "the installer asked for elevation exactly once: $(($asked[0] -replace '^\S+\s+\S+\s+', '').Trim())"
    if ((LogLines $log5 "rules not added; the program is installed without them").Count -ne 1) { Fail "the log does not say the rules were not added" }
    if (@(RulesFor).Count -ne 0) { Fail "rules exist although the prompt was refused" }
    if (Test-Path (Join-Path $Dir "firewall-rules-added")) { Fail "the marker was written although no rules were added" }
    Ok "no rules and no marker; the log says how to add them later"
    Uninstall
    if (Test-Path (Join-Path $Dir $Exes[0])) { Fail "the uninstall left the program" }
    Ok "uninstalled again (no rules to remove, so nothing asked)"
    Write-Host "firewall-test: PASS"
} finally {
    # The installers' own words about the firewall, for the record.
    foreach ($f in Get-ChildItem $Scratch -Filter *.log -ErrorAction SilentlyContinue) {
        foreach ($line in (Get-Content $f.FullName | Where-Object { $_ -match "Firewall|Exit code|error" })) {
            Write-Host "log $($f.BaseName): $(($line -replace '^\S+\s+\S+\s+', '').Trim())"
        }
    }
    # Whatever happened: no installer left running, no program, no rule for it.
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ($_.Path -like "*OpenHamShack-Packet-Radio*" -or $_.Path -like "*\is-*\*.tmp") } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Uninstall
    foreach ($r in @(RulesFor)) { Remove-NetFirewallRule -Name $r.Name -ErrorAction SilentlyContinue }
    if (Test-Path $Dir) { Remove-Item -Recurse -Force $Dir -ErrorAction SilentlyContinue }
    $parent = Split-Path -Parent $Dir
    if ((Test-Path $parent) -and -not (Get-ChildItem $parent)) { Remove-Item -Force $parent }
    Remove-Item -Recurse -Force $Scratch -ErrorAction SilentlyContinue
}
exit 0
