@echo off
setlocal EnableDelayedExpansion
@echo off
:: Check for admin privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Running as administrator.
) else (
    echo Not running as administrator. Trying to elevate...
    goto UACPrompt
)

:: If the script has admin rights, continue with the rest of the script
goto startScript

:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "%~s0", "", "", "runas", 1 >> "%temp%\getadmin.vbs"

    "%temp%\getadmin.vbs"
    exit /B

:startScript

set OPENVPN_PATH="C:\Program Files\OpenVPN\bin\openvpn.exe"
set REAL_IP=""
set CONNECTING_TO_VPN=0
set /a "counter=0"

if !counter! geq 10 (
    echo.
    echo ------------------ LOOP WRAP ------------------
    for /L %%i in (1,1,20) do echo.  REM adds 20 blank lines
    set /a "counter=0"
)

tasklist /FI "IMAGENAME eq armagetronad.exe" 2>NUL | find /I /N "armagetronad.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo Armagetronad is running.
) else (
    echo Armagetronad is not running.
    timeout /t 1 > NUL
    call :Update
)

timeout /t 5 /nobreak > NUL
set /a "counter+=1"
goto loop

:ConnectVPN
    echo. > "C:\Users\itsne\Desktop\arma_chatbot_config\openvpn.log"
    taskkill /F /FI "WINDOWTITLE eq OpenVPNConnection" 2>NUL
    set CONNECTING_TO_VPN=1

    taskkill /F /IM openvpn.exe 2>NUL
    timeout /t 2 > NUL

    set OVPN_DIR=C:\Users\itsne\Desktop\arma_chatbot_config\VPN\OVPN
    set /a "count=0"
    for %%f in (%OVPN_DIR%\*.ovpn) do (
        set /a "count+=1"
        set "file[!count!]=%%f"
    )

    set /a "randIndex=(%random% %% count) + 1"
    set "randFile=!file[%randIndex%]!"

    echo Using VPN configuration file: !randFile!

    start "OpenVPNConnection" cmd /c "%OPENVPN_PATH% --config "!randFile!" > "C:\Users\itsne\Desktop\arma_chatbot_config\openvpn.log" 2>&1"

    REM wait for VPN to connect
    set VPN_CONNECTED=0
    for /L %%x in (1,1,6) do (  REM try for up to 6 times with a 5 sec interval to 30 seconds
        timeout /t 5 > NUL
        findstr /C:"Initialization Sequence Completed" "C:\Users\itsne\Desktop\arma_chatbot_config\openvpn.log" > NUL
        if !errorlevel! == 0 (
            set VPN_CONNECTED=1
            goto VPNConnected
        )
        REM check for auth fail
        findstr /C:"AUTH: Received control message: AUTH_FAILED" "C:\Users\itsne\Desktop\arma_chatbot_config\openvpn.log" > NUL
        if !errorlevel! == 0 (
            echo Authentication failure detected. Retrying...
            taskkill /F /FI "WINDOWTITLE eq OpenVPNConnection" 2>NUL
            taskkill /F /IM openvpn.exe 2>NUL
            REM try to reconnect 
            goto ConnectVPN
        )
    )

:VPNConnected
    if !VPN_CONNECTED! == 0 (
        echo VPN connection failed or took too long.
    )

    set CONNECTING_TO_VPN=0
exit /b


:CheckVPN
    if "%CONNECTING_TO_VPN%"=="1" (
        exit /b
    )

    for /f "tokens=2 delims=: " %%i in ('curl -s https://httpbin.org/ip') do (
        set CURRENT_IP=%%i
    )

    ECHO COMPARING %CURRENT_IP% VS %REAL_IP%
    if "%CURRENT_IP%"=="%REAL_IP%" (
        echo Not connected to a VPN. Connecting...
        call :ConnectVPN
    ) else (
        echo Connected to a VPN. (%CURRENT_IP%)
    )
exit /b

:Update
    call :CheckVPN
    echo Checking if banned..
    copy /Y "C:\Users\itsne\AppData\Roaming\Armagetron\var\banned.txt" "C:\temp\banned_copy.txt" >NUL 2>&1
    for /f %%i in (C:\temp\banned_copy.txt) do (
        echo Banned! Changing IP...
        call :ConnectVPN
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

:GenerateTimestamp
for /f "delims=" %%a in ('wmic OS Get localdatetime ^| find "."') do set datetime=%%a
set timestamp=%datetime:~0,4%-%datetime:~4,2%-%datetime:~6,2%-%datetime:~8,2%%datetime:~10,2%%datetime:~12,2%
exit /b

call :GenerateTimestamp

echo Creating backup of stats.db...
copy "C:\Users\itsne\AppData\Roaming\Armagetron\var\stats.db" "C:\Users\itsne\Desktop\arma_chatbot_config\Backups\stats-%timestamp%.db"

:StartGame
echo Running new Armagetronad
start "" "C:\Users\itsne\Desktop\dist\armagetronad.exe"