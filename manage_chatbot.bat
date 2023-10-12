@echo off
setlocal EnableDelayedExpansion

set OPENVPN_PATH="C:\Program Files\OpenVPN\bin\openvpn.exe"
set REAL_IP="73.43.187.7"
set CONNECTING_TO_VPN=0
set /a "counter=0"

:loop
if !counter! geq 10 (
    cls
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
    REM Clear the log file before connecting again
    echo. > "C:\Users\itsne\Desktop\arma_chatbot_config\openvpn.log"
    taskkill /F /FI "WINDOWTITLE eq OpenVPNConnection" 2>NUL
    set CONNECTING_TO_VPN=1
    REM Kill any previous OpenVPN processes
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
    REM Using a unique title "OpenVPNConnection" for the command prompt window
    start "OpenVPNConnection" cmd /c "%OPENVPN_PATH% --config "!randFile!" > "C:\Users\itsne\Desktop\arma_chatbot_config\openvpn.log" 2>&1"

    REM Wait for VPN to connect by monitoring the log file
    set VPN_CONNECTED=0
    for /L %%x in (1,1,6) do (  REM Try for up to 6 times (with a 5-second interval, that's up to 30 seconds)
        timeout /t 5 > NUL
        findstr /C:"Initialization Sequence Completed" "C:\Users\itsne\Desktop\arma_chatbot_config\openvpn.log" > NUL
        if !errorlevel! == 0 (
            set VPN_CONNECTED=1
            goto VPNConnected
        )
        REM Check for authentication failure
        findstr /C:"AUTH: Received control message: AUTH_FAILED" "C:\Users\itsne\Desktop\arma_chatbot_config\openvpn.log" > NUL
        if !errorlevel! == 0 (
            echo Authentication failure detected. Retrying...
            taskkill /F /FI "WINDOWTITLE eq OpenVPNConnection" 2>NUL
            taskkill /F /IM openvpn.exe 2>NUL
            REM Try to reconnect (You may want to limit the number of retries to avoid infinite loops)
            goto ConnectVPN
        )
    )

:VPNConnected
    if !VPN_CONNECTED! == 0 (
        echo VPN connection failed or took too long. Please check.
        REM You can add any additional handling for VPN connection failures here.
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
        echo Connected to a VPN.
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
