@echo off
setlocal
rem Source ZIP entry point. Uses the .NET Framework included with Windows.
rem No PowerShell policy changes, downloads, Python install or administrator rights.
set "HVPC_CSC=%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%HVPC_CSC%" set "HVPC_CSC=%SystemRoot%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%HVPC_CSC%" goto missing
:new_temp
set "HVPC_SETUP_TMP=%TEMP%\HVPC-Resolve-MVSEP-setup-%RANDOM%-%RANDOM%"
if exist "%HVPC_SETUP_TMP%" goto new_temp
mkdir "%HVPC_SETUP_TMP%"
if errorlevel 1 goto failed
"%HVPC_CSC%" /nologo /target:winexe /utf8output /optimize+ /out:"%HVPC_SETUP_TMP%\Install.exe" /win32manifest:"%~dp0installer\app.manifest" /win32icon:"%~dp0src\resolve_mvsep\app.ico" /resource:"%~dp0src\resolve_mvsep\app.ico",HVPCSetup.AppIcon /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.Management.dll "%~dp0installer\SetupCore.cs" "%~dp0installer\SetupForm.cs"
if errorlevel 1 goto failed
start "" /wait "%HVPC_SETUP_TMP%\Install.exe" --source "%~dp0."
rem Only remove the files just created, never a recursive directory deletion.
del "%HVPC_SETUP_TMP%\Install.exe"
rd "%HVPC_SETUP_TMP%"
exit /b
:missing
echo The Windows .NET Framework compiler was not found. Use Install.exe from the GitHub release ZIP.
pause
exit /b 1
:failed
echo Cannot prepare setup. Extract the entire ZIP to a local folder and try again.
pause
exit /b 1
