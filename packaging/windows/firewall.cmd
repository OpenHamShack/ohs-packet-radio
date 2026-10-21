@echo off
rem Windows Defender Firewall rules for OpenHamShack Packet Radio.
rem
rem     firewall.cmd add      remove every rule for the program's two .exes
rem                           (ours and the ones Windows made itself), then
rem                           allow both inbound on private and public networks
rem     firewall.cmd remove   remove every rule for the two .exes
rem     firewall.cmd check    exit 0 when both of our allow rules are there
rem
rem add and remove need an administrator; check does not. The installer
rem (shack-packet.iss) runs this from the install folder: elevated through
rem one UAC prompt for a per-user install, directly for an all-users one.
rem An operator who said no to that prompt can run "firewall.cmd add" from
rem an administrator command prompt in the install folder later
rem (docs/TESTER-GUIDE.md, "Windows firewall").
rem
rem Why at all (Brian, 18/10/2026, moved here from a cancelled 0.1.3): the
rem program binds UDP sockets that answers arrive on unasked - the IC-9700's
rem LAN audio and CI-V streams, and mDNS when it looks for a controller - so
rem the first time it runs Windows asks whether to allow it. Cancel that, or
rem tick only one network type, and Windows writes a Block rule for the .exe.
rem A Block rule beats any Allow rule, so from then on the radio's audio and
rem the controller's adverts are dropped with no message anywhere, which is
rem exactly what a tester cannot diagnose. Hence: delete every rule that
rem names our .exes first, the Block ones included, then add our own.
rem
rem Rules are matched by program path, not by name, because Windows names
rem its own rules after the .exe's description and a later version may
rem change that. Only this install folder's .exes are touched.
setlocal
set "DIR=%~dp0"
set "NAME=OpenHamShack Packet Radio"

if /i "%~1"=="add" goto add
if /i "%~1"=="remove" goto remove
if /i "%~1"=="check" goto check
echo usage: firewall.cmd add^|remove^|check
exit /b 2

:remove
call :clear
exit /b 0

:add
call :clear
set "FAIL=0"
for %%E in (shack-packet.exe shack-packet-console.exe) do (
  netsh advfirewall firewall add rule name="%NAME% (%%E)" dir=in action=allow program="%DIR%%%E" enable=yes profile=private,public description="Lets %NAME% hear its radio and find a controller on this network. Added by its installer; removed when it is uninstalled." >nul || set "FAIL=1"
)
if "%FAIL%"=="1" (
  echo Could not add the firewall rules: run this as an administrator.
  exit /b 1
)
exit /b 0

:check
for %%E in (shack-packet.exe shack-packet-console.exe) do (
  netsh advfirewall firewall show rule name="%NAME% (%%E)" >nul 2>&1 || exit /b 1
)
exit /b 0

:clear
rem "No rules match" is an error to netsh and not to us.
for %%E in (shack-packet.exe shack-packet-console.exe) do (
  netsh advfirewall firewall delete rule name=all program="%DIR%%%E" >nul 2>&1
)
exit /b 0
