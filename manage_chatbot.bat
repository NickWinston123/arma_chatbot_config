@echo off
setlocal EnableDelayedExpansion

set OPENVPN_PATH="C:\Program Files\OpenVPN\bin\openvpn.exe"
set REAL_IP=""
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
    timeout /t 10 > NUL
    call :Update
)

timeout /t 5 /nobreak > NUL
set /a "counter+=1"
goto loop

:ConnectVPN
    set CONNECTING_TO_VPN=1
    taskkill /F /IM openvpn.exe 2>NUL
    timeout /t 5 > NUL

    set OVPN_DIR=C:\Users\itsne\Desktop\arma_chatbot_config\VPN\OVPN
    set /a "count=0"
    for %%f in (%OVPN_DIR%\*.ovpn) do (
        set /a "count+=1"
        set "file[!count!]=%%f"
    )

    set /a "randIndex=(%random% %% count) + 1"
    set "randFile=!file[%randIndex%]!"
    start "" %OPENVPN_PATH% --config "!randFile!" > "C:\Users\itsne\Desktop\arma_chatbot_config\openvpn.log" 2>&1

    timeout /t 30 > NUL
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

:StartGame
echo Running new Armagetronad
start "" "C:\Users\itsne\Desktop\dist\armagetronad.exe"
