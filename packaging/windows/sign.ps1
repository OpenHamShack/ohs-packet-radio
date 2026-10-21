<#
.SYNOPSIS
Authenticode-sign files as 360 Technology through Azure Artifact Signing.

.DESCRIPTION
    powershell -ExecutionPolicy Bypass -File packaging\windows\sign.ps1 -Config <work>\signing\signing.json <file> [<file> ...]

Run by tools\build-windows.ps1 for shack-packet.exe and
shack-packet-console.exe, and by Inno Setup (its SignTool, "ohssign") for
the uninstaller and the installer. Nothing else is signed: Python's and
Qt's binaries are other people's code, and a signature says who published
a file, so putting 360 Technology's name on theirs would misattribute it.

Copied word for word, apart from this paragraph, from OpenHamShack DATV's
packaging/windows/sign.ps1 (GPLv3), itself copied from OpenHamShack NB's,
which has signed NB, HAM and DATV on the same build machine with the same
credential since 06/10/2026. Keep every copy the same: a fix found in one
belongs in all.

How the signature is made. Microsoft's signtool.exe (Windows SDK build
tools) with Microsoft's Artifact Signing "dlib" plugin
(Azure.CodeSigning.Dlib.dll, NuGet Microsoft.ArtifactSigning.Client - the
renamed Microsoft.Trusted.Signing.Client): signtool hashes the file, the
dlib sends only the digest to the service, and the service signs it with a
certificate it issues for that request (CN=360 Technology, from 360
Technology's Artifact Signing account - see "The account" below -
certificate profile `Default`, West Europe). Those
certificates live three days, so every signature carries an RFC 3161
timestamp from Microsoft's TSA; without one the files would stop verifying
three days after the build. The dlib is .NET 8 code; build-windows.ps1
unpacks a private .NET 8 runtime and points DOTNET_ROOT at it, so nothing
is installed machine-wide. All three are pinned by version and SHA-256 in
build-windows.ps1. signing.json (written by build-windows.ps1) names them.

The credential. The signer app registration: an Entra app registration
in 360 Technology's tenant holding only the "Artifact Signing Certificate
Profile Signer" role on that one certificate profile: it can sign, and do
nothing else in Azure. Its tenant id, client id and client secret are User
environment variables of the build account on the Windows build machine:
AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET. The
dlib reads them itself (Azure.Identity's EnvironmentCredential; every
other credential type is switched off in metadata.json, so a build never
stops to open a browser or quietly signs with someone's Azure CLI login).
They are in no file and in no repository, and nothing here prints them.

The account. build-windows.ps1 writes the Artifact Signing account's name
into metadata.json from a fourth User environment variable of the build
account, OPENHAMSHACK_SIGNING_ACCOUNT, and stops at once if it is unset, as it
does for the other three. The name is not a secret, but it names an
internal Azure resource, so it lives beside the credential rather than in
the repository.

Rotating the secret (it lasts 12 months): in the Entra admin centre, App
registrations -> the signer app registration -> Certificates & secrets -> New client
secret; then on the build machine, as the build account,
    [Environment]::SetEnvironmentVariable("AZURE_CLIENT_SECRET", "<new value>", "User")
(typed there, not pasted into anything that keeps history), open a new
session, build once to prove it, and delete the old secret in Entra. An
expired secret fails the build at its first signature, with Azure.Identity's
message - never an unsigned installer.

Why a scheduled task, sometimes. signtool needs the Windows data
protection API (DPAPI) for the signing user, and DPAPI needs the user's
password-derived keys. A session started by SSH key authentication (how
every build on the Windows build machine is run) has none: Windows logs
the key holder on without a password (S4U), DPAPI answers "Access is
denied", and so does signtool, before the dlib even starts. So when this
session cannot use DPAPI, the signing alone runs as a one-shot scheduled
task under SYSTEM (which has machine keys), started and removed here,
while this script waits for it and relays its output. SYSTEM does not
have the build account's environment, so that task loads the three
AZURE_* values from the build account's own registry environment
(HKEY_USERS\<sid>\Environment, the same place Windows reads them from at
logon) into its own process, and nowhere else. It needs an elevated
session, which an administrator's SSH session is. In a desktop or
password session DPAPI works and signtool runs directly, as itself.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)][string]$Config,
    # Internal: the SYSTEM task's side. The request file names the files,
    # the log and the result; Sid is the build account whose environment
    # holds the credential.
    [string]$Request = "",
    [string]$Sid = "",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Files
)

$ErrorActionPreference = "Stop"
$cfg = Get-Content $Config -Raw | ConvertFrom-Json
$CredNames = @("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")

function Invoke-SignTool([string[]]$paths, [string]$log) {
    # The dlib's own DLLs (its C++ runtime among them) are found beside it,
    # not in System32: a fresh Windows need not have the VC++ runtime.
    $env:DOTNET_ROOT = $cfg.DotnetRoot
    $env:PATH = "$(Split-Path -Parent $cfg.Dlib);$env:PATH"
    $st = @("sign", "/v", "/fd", "SHA256", "/tr", $cfg.TimestampUrl, "/td", "SHA256",
              "/dlib", $cfg.Dlib, "/dmdf", $cfg.Metadata) + $paths
    # cmd merges signtool's stderr into the log, so PowerShell 5.1 does not
    # turn its error lines into terminating error records mid-run.
    $line = ($st | ForEach-Object { if ($_ -match '[\s"]') { '"' + $_ + '"' } else { $_ } }) -join " "
    cmd /c "`"$($cfg.SignTool)`" $line > `"$log`" 2>&1"
    return $LASTEXITCODE
}

if ($Request) {
    # ---- inside the SYSTEM task
    $req = Get-Content $Request -Raw | ConvertFrom-Json
    $code = 1
    try {
        $envKey = "Registry::HKEY_USERS\$Sid\Environment"
        $userEnv = Get-ItemProperty -Path $envKey
        foreach ($n in $CredNames) {
            # Straight from the registry value into this process's
            # environment, for the dlib to read: never into a variable that
            # is written out, never onto a command line.
            if (-not $userEnv.$n) { throw "$n is not set in the build account's environment" }
            [Environment]::SetEnvironmentVariable($n, $userEnv.$n, "Process")
        }
        $code = Invoke-SignTool @($req.Files) $req.Log
    } catch {
        Add-Content -Path $req.Log -Value "sign.ps1 (SYSTEM task): $($_.Exception.Message)"
        $code = 1
    }
    Set-Content -Path $req.Result -Value $code
    exit $code
}

# ---- the caller's side
if (-not $Files -or $Files.Count -eq 0) { throw "sign.ps1: no files to sign" }
foreach ($f in $Files) { if (-not (Test-Path -LiteralPath $f)) { throw "sign.ps1: no such file $f" } }
$Files = @($Files | ForEach-Object { (Resolve-Path -LiteralPath $_).ProviderPath })
foreach ($n in $CredNames) {
    if (-not [Environment]::GetEnvironmentVariable($n, "User")) {
        throw "$n is not set for this account (a User environment variable; packaging\windows\sign.ps1 says what it is)." +
              " Build with -NoSign for an unsigned local test build."
    }
}

$dir = Split-Path -Parent $Config
$tag = [guid]::NewGuid().ToString("N").Substring(0, 12)
$log = Join-Path $dir "signtool-$tag.log"

Add-Type -AssemblyName System.Security
$dpapi = $true
try { [void][Security.Cryptography.ProtectedData]::Protect([byte[]](1, 2, 3), $null, "CurrentUser") } catch { $dpapi = $false }

if ($dpapi) {
    $code = Invoke-SignTool $Files $log
} else {
    Write-Host "   (no DPAPI in this session - an SSH key logon; signing through a one-shot SYSTEM task)"
    $me = [Security.Principal.WindowsIdentity]::GetCurrent()
    $isAdmin = (New-Object Security.Principal.WindowsPrincipal $me).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) { throw "sign.ps1: this session has no DPAPI and is not elevated, so it can neither sign nor hand signing to SYSTEM" }
    $reqPath = Join-Path $dir "request-$tag.json"
    $result = Join-Path $dir "result-$tag.txt"
    @{ Files = $Files; Log = $log; Result = $result } | ConvertTo-Json | Set-Content -Path $reqPath -Encoding UTF8
    $task = "ohssign-$tag"
    $argline = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Config `"$Config`" -Request `"$reqPath`" -Sid $($me.User.Value)"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argline
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $task -Action $action -Principal $principal -Settings $settings -Force | Out-Null
    try {
        Start-ScheduledTask -TaskName $task
        $deadline = (Get-Date).AddMinutes(30)
        while (-not (Test-Path $result)) {
            if ((Get-Date) -gt $deadline) { throw "sign.ps1: the signing task did not finish in 30 minutes" }
            $state = (Get-ScheduledTask -TaskName $task).State
            if ($state -ne "Running" -and $state -ne "Queued" -and -not (Test-Path $result)) {
                Start-Sleep 2
                if (-not (Test-Path $result)) { throw "sign.ps1: the signing task stopped ($state, result $((Get-ScheduledTaskInfo -TaskName $task).LastTaskResult)) without a result" }
            }
            Start-Sleep 1
        }
        $code = [int](Get-Content $result -Raw).Trim()
    } finally {
        Unregister-ScheduledTask -TaskName $task -Confirm:$false
        Remove-Item -Force -ErrorAction SilentlyContinue $reqPath, $result
    }
}

if (Test-Path $log) {
    Get-Content $log | Where-Object { $_.Trim() } | ForEach-Object { Write-Host "   $_" }
    Remove-Item -Force $log
}
if ($code -ne 0) { throw "signing failed (signtool exit $code); nothing is shipped unsigned - see the lines above" }
exit 0
