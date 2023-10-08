@echo off
setlocal EnableDelayedExpansion

set /a "counter=0"

REM UPDATE
call :Update

:loop
REM clear console every 10 iterations
if !counter! geq 10 (
    cls
    set /a "counter=0"
)

tasklist /FI "IMAGENAME eq armagetronad.exe" 2>NUL | find /I /N "armagetronad.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo Armagetronad is running.
) else (
    echo Armagetronad is not running.
    call :Update
)

REM wait 5 seconds before next check
timeout /t 5 /nobreak > NUL

set /a "counter+=1"

goto loop

:Update

REM BANNED?
echo Checking if banned..
REM file has contents = banned
for /f %%i in (C:\Users\itsne\AppData\Roaming\Armagetron\var\banned.txt) do (
    nircmd.exe win activate title "Proton VPN"
    nircmd.exe win max title "Proton VPN"

    nircmd.exe setcursor 260 445
    timeout /t 1 > NUL
    nircmd.exe sendmouse left click    
    timeout /t 1 > NUL
   
    goto IPChecked
)

:IPChecked

echo Killing old process if any
taskkill /F /IM "armagetronad.exe" 2>NUL

REM NETWORK LOC ACCESSABLE?
pushd "\\tsclient\C\" 2>NUL
if ERRORLEVEL 1 (
    echo The network location is not accessible!
    goto StartGame
)

popd

echo Deleting old dist folder
rmdir /s /q "C:\Users\itsne\Desktop\dist" 2>NUL

echo Copying new dist folder
xcopy "\\tsclient\C\Games\ArmagetronProject2.0\dist" "C:\Users\itsne\Desktop\dist" /E /I /Y

:StartGame
echo Running new Armagetronad
start "" "C:\Users\itsne\Desktop\dist\armagetronad.exe"
