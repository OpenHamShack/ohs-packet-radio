<#
.SYNOPSIS
Build OpenHamShack-Packet-Radio-<version>-windows-x64.exe (and its
.sha256) on Windows.

.DESCRIPTION
Run on a Windows 10/11 x64 machine, from a stage folder copied across:

    powershell -ExecutionPolicy Bypass -File tools\build-windows.ps1 -Stage C:\...\stage [-Work C:\shackpacket-build] [-NoSign]

-Stage is what ohs-libs' packaging kit's `shackpkg.py stage shack-app.toml
<dir>` made on Linux (app.json, src\, launcher.py, requirements.txt,
icons\, LICENSE, app.spec - this app's own packaging\qt-app.spec, staged):
this machine has neither the brand kit nor Python until it fetches one.
Staging needs an ohs-libs checkout with the packet branches
(tools\ohslibs.py's job, Linux-only), so it is not done here.

Everything this script downloads or builds goes under -Work (default
C:\shackpacket-build): a private Python (python.org's NuGet package,
unpacked, not installed), a virtualenv, a download cache, PyInstaller's
work folders, and out\ with the installer. Nothing is installed
machine-wide and PATH is not touched. Inno Setup, if not already
installed, is unpacked into the same folder in its portable mode.

The frozen program is two executables sharing one folder (qt-app.spec adds
the second only when it runs on Windows): shack-packet.exe, windowed, with
no stdout at all, and shack-packet-console.exe, the same program with a
console - the one this script runs --self-test on, since the windowed one
could not show PASS/FAIL. Both, and the installer and its uninstaller, are
Authenticode-signed as 360 Technology through Azure Artifact Signing
(packaging\windows\sign.ps1 says how, where the credential lives and how
to rotate it), the same account and build machine that signs NB, HAM and
DATV. Every signature is checked (signer, chain, timestamp) before the
.sha256 is written. -NoSign makes an unsigned build for local testing;
never release one.

Copied in shape from openhamshack-nb's and openhamshack-datv's
tools\build-windows.ps1 (GPLv3). From 0.2 it has their vendor\ step too,
for two C libraries the SDR monitor needs, each fetched pinned, shipped
unmodified in a folder of its own under _internal\ (src\shack_packet_app\
host.py says why), with its licence in _internal\licences\:
  libiio 0.26     ADI's Windows release (libiio.dll, libxml2.dll,
                  libusb-1.0.dll, libserialport-0.dll) and its iio.py: a Pluto
  librtlsdr       rtl-sdr-blog V1.4.0's x64 rtlsdr.dll (libusb linked in;
                  nothing else it imports is not already in the bundle): an
                  RTL-SDR dongle
  libhackrf       2026.01.3, conda-forge's hackrf-0.dll with the libusb-1.0.dll
                  and libwinpthread-1.dll it imports (ATV's pins, from 0.2.0):
                  a HackRF One
The Python packages cryptography, zeroconf, hidapi and Pillow come in with
the stage's requirements.txt (shack-app.toml [python] requirements). After
--self-test the console twin runs --library-check, which loads every one of
them and fails the build unless every one says ok. ADI's Pluto USB driver
installer is not in this installer yet (NB's is): that and the Inno task
for it are 0.2's SDR work.

Windows PowerShell 5.1 compatible (no ?? and no -NoNewline). Run in the
foreground over SSH: Windows OpenSSH kills a background child when the
session closes.
#>
param(
    [Parameter(Mandatory = $true)][string]$Stage,
    [string]$Work = "C:\shackpacket-build",
    [switch]$NoSign
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = Split-Path -Parent $PSScriptRoot
$Stage = (Resolve-Path $Stage).Path
$App = Get-Content (Join-Path $Stage "app.json") -Raw | ConvertFrom-Json
if (-not $App.spec) { throw "the stage has no app.spec; was it made from this app's shack-app.toml?" }
$Version = $App.version
$Cache = Join-Path $Work "cache"
$Out = Join-Path $Work "out"
New-Item -ItemType Directory -Force -Path $Work, $Cache, $Out | Out-Null

$Pins = @{
    # Python 3.13, the full interpreter as a zip (tools\ inside): venv and
    # pip work, nothing is registered with Windows. NB's and DATV's pin.
    python     = @{ url  = "https://api.nuget.org/v3-flatcontainer/python/3.13.16/python.3.13.16.nupkg"
                    file = "python.3.13.16.zip"
                    sha  = "95fad176338f1d6a799e45946488063f4aa61764e6cff2f132ddeffbf7a04c34" }
    innosetup  = @{ url  = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe"
                    file = "innosetup-6.7.3.exe"
                    sha  = "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732" }
    # Signing tools (packaging\windows\sign.ps1): signtool.exe from
    # Microsoft's Windows SDK build tools NuGet package, and the Artifact
    # Signing plugin for it ("the dlib"). Same pins as NB's and DATV's.
    signtool   = @{ url  = "https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.buildtools/10.0.28000.2705/microsoft.windows.sdk.buildtools.10.0.28000.2705.nupkg"
                    file = "microsoft.windows.sdk.buildtools.10.0.28000.2705.zip"
                    sha  = "8bfdfb6ca2633f531cf80b5fa22512ba61a394d7988f0970db83baadc67929ed" }
    signclient = @{ url = "https://api.nuget.org/v3-flatcontainer/microsoft.artifactsigning.client/1.0.128/microsoft.artifactsigning.client.1.0.128.nupkg"
                    file = "microsoft.artifactsigning.client.1.0.128.zip"
                    sha  = "74bd7d27e6ce1051409c38d9b46bc8df0400ecd643d51ffbf2ac00869061e40b" }
    dotnet8    = @{ url  = "https://builds.dotnet.microsoft.com/dotnet/Runtime/8.0.31/dotnet-runtime-8.0.31-win-x64.zip"
                    file = "dotnet-runtime-8.0.31-win-x64.zip"
                    sha  = "7deabbefdc83e378b343c551fe447b86002680eea91355b1f6f81d0c9ab0dcaa" }
    # ADI's libiio 0.26 Windows binaries, NB's pin: 0.26, not 1.0, because
    # shack_packet.sdr.pluto is written against the 0.2x bindings.
    libiio     = @{ url  = "https://github.com/analogdevicesinc/libiio/releases/download/v0.26/Windows.zip"
                    file = "libiio-0.26-Windows.zip"
                    sha  = "4ad4a8c6b3f7145922c122dcfc51d693f0d3f9a0054fb2228814ce58c538a6ca" }
    # rtl-sdr-blog's Windows release V1.4.0 (the newest on 18/10/2026).
    rtlsdr     = @{ url  = "https://github.com/rtlsdrblog/rtl-sdr-blog/releases/download/V1.4.0/Release.zip"
                    file = "rtlsdr-blog-1.4.0-Release.zip"
                    sha  = "7ef33f1304647f65e5e0fde43637a73d54f076e91e651a3cecc4f55a17fd9815" }
    # libhackrf for Windows, OpenHamShack ATV's pins (openhamshack-atv
    # tools\build-windows.ps1, 08/10/2026), whose source is already on the
    # shared source site. Great Scott Gadgets publish only source for the
    # host library; the Windows build they point to is radioconda's, which
    # is conda-forge's package, taken here directly: libhackrf0
    # (hackrf-0.dll) and the two DLLs it imports beyond Windows and the C
    # runtime, libusb 1.0.29 and mingw-w64's winpthreads. Its other import,
    # VCRUNTIME140.dll, is Python's own and already in the process.
    libhackrf  = @{ url  = "https://api.anaconda.org/download/conda-forge/libhackrf0/2026.01.3/win-64/libhackrf0-2026.01.3-hfd05255_0.conda"
                    file = "libhackrf0-2026.01.3-hfd05255_0.conda"
                    sha  = "fd9a6a718cad7ff61f830e6c3789e26363d7f9e12399e25d9d5a68c06251f8f7" }
    conda_libusb = @{ url = "https://api.anaconda.org/download/conda-forge/libusb/1.0.29/win-64/libusb-1.0.29-h1839187_0.conda"
                    file = "libusb-1.0.29-h1839187_0.conda"
                    sha  = "9837f8e8de20b6c9c033561cd33b4554cd551b217e3b8d2862b353ed2c23d8b8" }
    conda_winpthread = @{ url = "https://api.anaconda.org/download/conda-forge/libwinpthread/12.0.0.r4.gg4f2fc60ca/win-64/libwinpthread-12.0.0.r4.gg4f2fc60ca-h57928b3_10.conda"
                    file = "libwinpthread-12.0.0.r4.gg4f2fc60ca-h57928b3_10.conda"
                    sha  = "0fccf2d17026255b6e10ace1f191d0a2a18f2d65088fd02430be17c701f8ffe0" }
    # Each library's licence, from the tag its binaries were built from
    # (libxml2 2.9.12 and libusb are what ADI's DLLs report; NB's pins).
    libiio_copying = @{ url = "https://raw.githubusercontent.com/analogdevicesinc/libiio/v0.26/COPYING.txt"
                    file = "libiio-COPYING.txt"
                    sha  = "102900208eef27b766380135906d431dba87edaa7ec6aa72e6ebd3dd67f3a97b" }
    libusb_copying = @{ url = "https://raw.githubusercontent.com/libusb/libusb/v1.0.24/COPYING"
                    file = "libusb-COPYING"
                    sha  = "5df07007198989c622f5d41de8d703e7bef3d0e79d62e24332ee739a452af62a" }
    libserialport_copying = @{ url = "https://raw.githubusercontent.com/sigrokproject/libserialport/libserialport-0.1.1/COPYING"
                    file = "libserialport-COPYING"
                    sha  = "da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768" }
    libxml2_copyright = @{ url = "https://raw.githubusercontent.com/GNOME/libxml2/v2.9.12/Copyright"
                    file = "libxml2-Copyright"
                    sha  = "c5c63674f8a83c4d2e385d96d1c670a03cb871ba2927755467017317878574bd" }
    rtlsdr_copying = @{ url = "https://raw.githubusercontent.com/rtlsdrblog/rtl-sdr-blog/V1.4.0/COPYING"
                    file = "rtl-sdr-COPYING"
                    sha  = "ab15fd526bd8dd18a9e77ebc139656bf4d33e97fc7238cd11bf60e2b9b8666c6" }
}

function Step([string]$what) { Write-Host ""; Write-Host "== $what" }

function Fetch([string]$name) {
    $p = $Pins[$name]
    $dest = Join-Path $Cache $p.file
    if (Test-Path $dest) {
        if ((Get-FileHash $dest -Algorithm SHA256).Hash -eq $p.sha.ToUpper()) { return $dest }
        Remove-Item $dest
    }
    Write-Host "   fetching $($p.url)"
    Invoke-WebRequest -Uri $p.url -OutFile $dest -UseBasicParsing -UserAgent "Wget/1.21"
    $got = (Get-FileHash $dest -Algorithm SHA256).Hash
    if ($got -ne $p.sha.ToUpper()) {
        Remove-Item $dest
        throw "$($p.file): SHA-256 $got, expected $($p.sha). Upstream changed the file; check it and update the pin."
    }
    return $dest
}

function Run([string]$exe) {
    & $exe @args
    if ($LASTEXITCODE -ne 0) { throw "$exe $args exited $LASTEXITCODE" }
}

Write-Host "$($App.name) $Version ($($App.id))"

# ---------------------------------------------------------------- signing tools
$SignCfg = $null
if ($NoSign) {
    Step "Signing: OFF (-NoSign). This build is UNSIGNED and must not be released."
} else {
    Step "Signing tools: signtool, the Artifact Signing dlib, .NET 8"
    foreach ($n in @("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "OPENHAMSHACK_SIGNING_ACCOUNT")) {
        if (-not [Environment]::GetEnvironmentVariable($n, "User")) {
            throw "$n is not set for this account, so nothing can be signed (packaging\windows\sign.ps1 says what it is). " +
                  "Use -NoSign for an unsigned local test build."
        }
    }
    $SignDir = Join-Path $Work "signing"
    foreach ($t in @(@("signtool", "sdk-buildtools"), @("signclient", "artifactsigning-client"), @("dotnet8", "dotnet"))) {
        $dest = Join-Path $SignDir $t[1]
        $stamp = Join-Path $dest ".from-$($Pins[$t[0]].file)"
        if (-not (Test-Path $stamp)) {
            if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
            Expand-Archive -Path (Fetch $t[0]) -DestinationPath $dest -Force
            Set-Content -Path $stamp -Value $Pins[$t[0]].sha
        }
    }
    $SignTool = Get-ChildItem (Join-Path $SignDir "sdk-buildtools\bin\*\x64\signtool.exe") | Select-Object -First 1 -ExpandProperty FullName
    $Dlib = Join-Path $SignDir "artifactsigning-client\bin\x64\Azure.CodeSigning.Dlib.dll"
    if (-not $SignTool -or -not (Test-Path $Dlib)) { throw "signtool.exe or Azure.CodeSigning.Dlib.dll is not where the packages put them" }
    $meta = [ordered]@{
        Endpoint = "https://weu.codesigning.azure.net/"
        CodeSigningAccountName = [Environment]::GetEnvironmentVariable("OPENHAMSHACK_SIGNING_ACCOUNT", "User")
        CertificateProfileName = "Default"
        CorrelationId = "shack-packet-$Version"
        ExcludeCredentials = @("ManagedIdentityCredential", "WorkloadIdentityCredential", "SharedTokenCacheCredential",
                               "VisualStudioCredential", "VisualStudioCodeCredential", "AzureCliCredential",
                               "AzurePowerShellCredential", "AzureDeveloperCliCredential", "InteractiveBrowserCredential")
    }
    $Metadata = Join-Path $SignDir "metadata.json"
    [IO.File]::WriteAllText($Metadata, ($meta | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    $SignCfg = Join-Path $SignDir "signing.json"
    $cfg = [ordered]@{ SignTool = $SignTool; Dlib = $Dlib; Metadata = $Metadata
                       DotnetRoot = (Join-Path $SignDir "dotnet"); TimestampUrl = "http://timestamp.acs.microsoft.com" }
    [IO.File]::WriteAllText($SignCfg, ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    Write-Host "   $SignTool"
    Write-Host "   $Dlib"
}
$SignPs1 = Join-Path $Repo "packaging\windows\sign.ps1"

function Sign([string[]]$paths) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SignPs1 -Config $SignCfg @paths
    if ($LASTEXITCODE -ne 0) { throw "signing $($paths -join ', ') failed; not building an unsigned installer" }
}

function AssertSigned([string]$path) {
    # Signed by 360 Technology, chaining to a root Windows trusts (/pa, the
    # Authenticode policy a user's PC applies), and timestamped: the
    # certificate lives three days, and only the timestamp keeps the
    # signature valid after that.
    $signToolForVerify = if ($SignCfg) { (Get-Content $SignCfg -Raw | ConvertFrom-Json).SignTool } else {
        throw "AssertSigned: no signtool (this build is unsigned)"
    }
    $out = (cmd /c "`"$signToolForVerify`" verify /pa /v `"$path`" 2>&1") | Out-String
    $code = $LASTEXITCODE
    $s = Get-AuthenticodeSignature -LiteralPath $path
    $name = Split-Path -Leaf $path
    if ($code -ne 0 -or $s.Status -ne "Valid") {
        throw "$name does not verify (signtool verify exit $code, Authenticode $($s.Status)):`n$out"
    }
    if ($s.SignerCertificate.Subject -notmatch '(^|, )O=360 Technology(,|$)' -or $out -notmatch "Issued to: 360 Technology") {
        throw "$name is signed, but not by 360 Technology ($($s.SignerCertificate.Subject))"
    }
    if (-not $s.TimeStamperCertificate -or $out -notmatch "The signature is timestamped: (.+)") {
        throw "$name is signed without a timestamp; it would stop verifying when the 3-day certificate expires"
    }
    $when = $Matches[1].Trim()
    Write-Host "   $name : $($s.Status), signer $($s.SignerCertificate.Subject)"
    Write-Host "      issued by $($s.SignerCertificate.Issuer)"
    Write-Host "      timestamped $when by $($s.TimeStamperCertificate.Subject)"
}

# ---------------------------------------------------------------- Python
Step "Python"
$PyHome = Join-Path $Work "python"
$Py = Join-Path $PyHome "tools\python.exe"
if (-not (Test-Path $Py)) {
    Expand-Archive -Path (Fetch "python") -DestinationPath $PyHome -Force
}
$Venv = Join-Path $Work "venv"
$VPy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VPy)) { Run $Py -m venv $Venv }
Run $VPy -m pip install --disable-pip-version-check --quiet --cache-dir (Join-Path $Cache "pip") `
    -r (Join-Path $Repo "packaging\windows\build-requirements.txt") -r (Join-Path $Stage "requirements.txt")
Run $VPy -c "import sys, numpy, PyQt6.QtCore, pyaudio, serial; print('   python', sys.version.split()[0], 'numpy', numpy.__version__, 'Qt', PyQt6.QtCore.QT_VERSION_STR)"

# ---------------------------------------------------------------- vendor: libiio, librtlsdr
Step "Vendor: libiio 0.26, librtlsdr and libhackrf, with their licences"
$Vendor = Join-Path $Work "vendor"
$PyDir = Join-Path $Vendor "py"
$IioDir = Join-Path $Vendor "libiio"
$RtlDir = Join-Path $Vendor "rtlsdr"
$Lic = Join-Path $Vendor "licences"
# libiio.dll and exactly what it imports beyond Windows and Python's own
# vcruntime140 (NB's list, read from its import table).
$IioDlls = @("libiio.dll", "libxml2.dll", "libusb-1.0.dll", "libserialport-0.dll")
if (-not (Test-Path (Join-Path $PyDir "iio.py")) -or -not (Test-Path (Join-Path $IioDir "libiio.dll"))) {
    $tmp = Join-Path $Work "tmp-libiio"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Path (Fetch "libiio") -DestinationPath $tmp -Force
    $vs = Join-Path $tmp "Windows-VS-2022-x64"
    # Not pip-installed: its setup.py refuses where libiio.dll is not already
    # installed system-wide. iio.py is the whole binding.
    Run tar -xzf (Join-Path $vs "libiio-py39-amd64.tar.gz") -C $tmp
    New-Item -ItemType Directory -Force -Path $PyDir, $IioDir | Out-Null
    Copy-Item (Join-Path $tmp "pylibiio-0.26\iio.py") $PyDir
    foreach ($dll in $IioDlls) { Copy-Item (Join-Path $vs $dll) $IioDir }
    Remove-Item -Recurse -Force $tmp
}
if (-not (Test-Path (Join-Path $RtlDir "rtlsdr.dll"))) {
    $tmp = Join-Path $Work "tmp-rtlsdr"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Path (Fetch "rtlsdr") -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force -Path $RtlDir | Out-Null
    # rtlsdr.dll alone: it imports only KERNEL32 and the VC runtime the
    # bundle already has (libusb is linked in); the zip's msvcr100 and
    # pthreadVC2 are for its command-line tools.
    Copy-Item (Join-Path $tmp "x64\rtlsdr.dll") $RtlDir
    Remove-Item -Recurse -Force $tmp
}
New-Item -ItemType Directory -Force -Path $Lic | Out-Null
foreach ($l in @("libiio_copying", "libusb_copying", "libserialport_copying", "libxml2_copyright", "rtlsdr_copying")) {
    Copy-Item (Fetch $l) (Join-Path $Lic $Pins[$l].file)
}
# The installer's notice: what it carries and where its source is
# (packaging\THIRD-PARTY.txt, from 0.2.0; packaging\sources.txt is the list
# tools\publish.sh source puts on https://openhamshack.com/source/).
Copy-Item (Join-Path $Repo "packaging\THIRD-PARTY.txt") $Lic
# libhackrf: each .conda is a zip holding pkg-*.tar.zst (the files) and
# info-*.tar.zst (the licence texts); Expand-Archive wants a .zip name, and
# Windows' own tar (by full path: a Git or MSYS tar earlier on PATH may not
# read zstd) unpacks the rest. The three DLLs go side by side into
# vendor\hackrf -> _internal\hackrf, where Windows finds hackrf-0.dll's
# imports first. ATV's CondaPackage, as it is.
$HkDir = Join-Path $Vendor "hackrf"
$Tar = Join-Path $env:SystemRoot "System32\tar.exe"
if (-not (Test-Path (Join-Path $HkDir "hackrf-0.dll"))) {
    New-Item -ItemType Directory -Force -Path $HkDir | Out-Null
    $CondaTmp = Join-Path $Work "tmp-conda"
    function CondaPackage([string]$pin, [string]$dll, [hashtable]$licences) {
        $dir = Join-Path $CondaTmp $pin
        if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $zip = Join-Path $dir "pkg.zip"
        Copy-Item (Fetch $pin) $zip
        Expand-Archive -Path $zip -DestinationPath $dir -Force
        foreach ($t in Get-ChildItem $dir -Filter "*.tar.zst") { Run $Tar -xf $t.FullName -C $dir }
        $src = Join-Path $dir "Library\bin\$dll"
        if (-not (Test-Path $src)) { throw "$($Pins[$pin].file) has no Library\bin\$dll" }
        Copy-Item $src $HkDir
        foreach ($k in $licences.Keys) {
            $l = Join-Path $dir "info\licenses\$k"
            if (-not (Test-Path $l)) { throw "$($Pins[$pin].file) has no info\licenses\$k" }
            Copy-Item $l (Join-Path $Lic $licences[$k])
        }
        Write-Host "   $dll  $((Get-FileHash $src -Algorithm SHA256).Hash.ToLower())"
    }
    New-Item -ItemType Directory -Force -Path $Lic | Out-Null
    CondaPackage "libhackrf" "hackrf-0.dll" @{ "COPYING" = "hackrf-COPYING" }
    CondaPackage "conda_libusb" "libusb-1.0.dll" @{ "COPYING" = "libusb-1.0.29-COPYING" }
    CondaPackage "conda_winpthread" "libwinpthread-1.dll" @{ "mingw-w64-libraries\winpthreads\COPYING" = "winpthreads-COPYING";
                                                              "COPYING.MinGW-w64\COPYING.MinGW-w64.txt" = "mingw-w64-COPYING.txt" }
    Remove-Item -Recurse -Force $CondaTmp
}
# Each set must load on its own, from its own folder, before it is frozen.
$oldPath = $env:PATH
$env:PATH = "$IioDir;$env:PATH"
try {
    Run $VPy -c "import sys, os; sys.path.insert(0, sys.argv[1]); import iio; d = os.path.dirname(iio._lib._name); print('   libiio', '.'.join(map(str, iio.version[:2])), 'from', d); assert iio.version[:2] == (0, 26), iio.version; assert os.path.samefile(d, sys.argv[2]), d" $PyDir $IioDir
} finally { $env:PATH = $oldPath }
Run $VPy -c "import ctypes, sys; lib = ctypes.CDLL(sys.argv[1]); print('   librtlsdr', sys.argv[1], lib.rtlsdr_get_device_count(), 'dongle(s)')" (Join-Path $RtlDir "rtlsdr.dll")
# hackrf_init only starts libusb; nothing is opened (hackrf_device_list
# would open every HackRF attached to read its serial).
Run $VPy -c "import ctypes, sys; lib = ctypes.CDLL(sys.argv[1]); assert lib.hackrf_init() == 0, 'hackrf_init failed'; lib.hackrf_exit(); print('   libhackrf', sys.argv[1])" (Join-Path $HkDir "hackrf-0.dll")

# ---------------------------------------------------------------- freeze
Step "PyInstaller"
$Dist = Join-Path $Work "dist"
$Env:SHACK_STAGE = $Stage
$Env:SHACK_VENDOR = $Vendor
try {
    Run $VPy -m PyInstaller --noconfirm --clean --log-level WARN --distpath $Dist --workpath (Join-Path $Work "pyi") `
        (Join-Path $Stage "app.spec")
} finally { Remove-Item Env:SHACK_STAGE, Env:SHACK_VENDOR }
$Bundle = Join-Path $Dist $App.executable
$Exe = Join-Path $Bundle "$($App.executable).exe"
$ConsoleExe = Join-Path $Bundle "$($App.executable)-console.exe"
foreach ($f in @($Exe, $ConsoleExe)) { if (-not (Test-Path $f)) { throw "PyInstaller made no $f" } }

# ---------------------------------------------------------------- sign the program
if (-not $NoSign) {
    Step "Signing the program's own executables"
    Sign @($Exe, $ConsoleExe)
    AssertSigned $Exe
    AssertSigned $ConsoleExe
}

# ---------------------------------------------------------------- smoke test
Step "Self-test: the frozen console program on the simulator"
# The windowed .exe has no stdout to read PASS/FAIL from; the console twin
# does. A scratch settings/data folder, never whatever an operator of
# this build machine might already have.
$Scratch = Join-Path $Work "scratch-$([guid]::NewGuid().ToString('N').Substring(0,8))"
New-Item -ItemType Directory -Force -Path $Scratch | Out-Null
$Env:SHACK_PACKET_CONF = Join-Path $Scratch "conf"
$Env:SHACK_PACKET_DATA = Join-Path $Scratch "data"
try {
    $selfTest = & $ConsoleExe "--self-test" 2>&1 | Out-String
    $code = $LASTEXITCODE
    Write-Host $selfTest
    if ($code -ne 0 -or $selfTest -notmatch "self-test: PASS") { throw "self-test did not report PASS (exit $code)" }
    # Every optional library, loaded by the frozen program from its own
    # bundle (libcheck.py): the step that would have caught 0.1.x's claim
    # that libiio and librtlsdr were bundled.
    $libCheck = & $ConsoleExe "--library-check" 2>&1 | Out-String
    $code = $LASTEXITCODE
    Write-Host $libCheck
    if ($code -ne 0 -or $libCheck -notmatch "library-check: PASS") { throw "library-check did not report PASS (exit $code)" }
    foreach ($want in @("libiio 0.26 from .*_internal\\libiio", "rtlsdr\\rtlsdr.dll", "hackrf\\hackrf-0.dll")) {
        if ($libCheck -notmatch $want) { throw "library-check passed, but not with the bundle's own copy ($want)" }
    }
} finally {
    Remove-Item Env:SHACK_PACKET_CONF, Env:SHACK_PACKET_DATA
    Remove-Item -Recurse -Force $Scratch
}

# ---------------------------------------------------------------- Inno Setup
Step "Inno Setup"
$Iscc = $null
foreach ($c in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                 "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", (Join-Path $Work "innosetup\ISCC.exe"))) {
    if ($c -and (Test-Path $c)) { $Iscc = $c; break }
}
if (-not $Iscc) {
    $is = Fetch "innosetup"
    $dir = Join-Path $Work "innosetup"
    $p = Start-Process $is -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /PORTABLE=1 /DIR=`"$dir`"" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Inno Setup unpack exited $($p.ExitCode)" }
    $Iscc = Join-Path $dir "ISCC.exe"
}
Write-Host "   $Iscc"
if (-not $NoSign) {
    # "ohssign": Inno Setup's SignTool, wired to sign.ps1 for the
    # uninstaller and the finished setup.exe (packaging\windows\shack-packet.iss).
    $Env:Path = "$(Split-Path -Parent $Iscc);$Env:Path"
}

# ---------------------------------------------------------------- installer
Step "Installer"
$isccArgs = @("/Q", "/DAppVersion=$Version", "/DDistDir=$Bundle",
    "/DIconFile=$(Join-Path $Stage 'icons\app.ico')", "/DLicenseFile=$(Join-Path $Stage 'LICENSE')", "/DOutDir=$Out")
if (-not $NoSign) {
    # $q is Inno's own quote-escape inside a SignTool string (a literal
    # `"` here would end the /S value early); $f is Inno's placeholder
    # for the file being signed. NB's and DATV's exact form.
    $isccArgs += @("/DSign=1", "/Slbsign=powershell.exe -NoProfile -ExecutionPolicy Bypass -File `$q$SignPs1`$q -Config `$q$SignCfg`$q `$f")
}
$isccArgs += (Join-Path $Repo "packaging\windows\shack-packet.iss")
Run $Iscc @isccArgs
$Setup = Join-Path $Out "OpenHamShack-Packet-Radio-$Version-windows-x64.exe"
if (-not $NoSign) { AssertSigned $Setup }
$hash = (Get-FileHash $Setup -Algorithm SHA256).Hash.ToLower()
$name = Split-Path -Leaf $Setup
[IO.File]::WriteAllText("$Setup.sha256", "$hash  $name`n", [Text.Encoding]::ASCII)
Write-Host ""
Write-Host "built $Setup ($([math]::Round((Get-Item $Setup).Length / 1MB, 1)) MB)"
Write-Host "sha256 $hash"
if ($NoSign) { Write-Host "UNSIGNED (-NoSign): never release this one" }
exit 0
