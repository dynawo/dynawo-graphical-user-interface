@echo off
rem
rem Copyright (c) 2026, RTE (http://www.rte-france.com)
rem See AUTHORS.txt
rem All rights reserved.
rem This Source Code Form is subject to the terms of the Mozilla Public
rem License, v. 2.0. If a copy of the MPL was not distributed with this
rem file, you can obtain one at http://mozilla.org/MPL/2.0/.
rem SPDX-License-Identifier: MPL-2.0
rem
rem start-dynawo-gui.bat - build if needed and start the Dynawo GUI in Docker.
rem
rem Double-click this file, or run it from PowerShell. Everything the
rem application needs - Python, Node, Dynawo, DynaFlow Launcher - is inside the
rem container image, so nothing has to be installed on Windows except Docker.
rem
rem Stop it again with stop-dynawo-gui.bat
rem
rem This is the Windows counterpart of docker/start.sh.

setlocal enabledelayedexpansion

rem A double-clicked script starts in C:\Windows\System32, where there is no
rem compose file. Move to this script's own directory first.
cd /d "%~dp0"

echo.
echo  ============================================================
echo   Dynawo GUI - starting
echo  ============================================================
echo.

rem --- is Docker installed? ---------------------------------------------------

where docker >nul 2>&1
if errorlevel 1 (
    echo  [X] Docker was not found on this machine.
    echo.
    echo      Install one of:
    echo        Docker Desktop   https://www.docker.com/products/docker-desktop/
    echo        Rancher Desktop  https://rancherdesktop.io/
    echo.
    echo      Docker Desktop needs a paid subscription in larger
    echo      organisations; Rancher Desktop and Podman Desktop do not.
    echo.
    goto :fail
)

rem --- is the engine actually running? ---------------------------------------
rem "docker --version" answers from the client alone and succeeds even when the
rem engine is down, so ask the daemon something instead.

docker info >nul 2>&1
if errorlevel 1 (
    echo  [X] Docker is installed but the engine is not responding.
    echo.
    echo      Start Docker Desktop and wait until the whale icon in the
    echo      system tray stops animating, then run this again.
    echo.
    goto :fail
)
echo  [OK] Docker engine is running

rem --- Compose v2, or fall back to v1 ----------------------------------------

set "COMPOSE=docker compose"
docker compose version >nul 2>&1
if errorlevel 1 (
    where docker-compose >nul 2>&1
    if errorlevel 1 (
        echo  [X] Docker Compose was not found.
        echo.
        echo      It ships with Docker Desktop and Rancher Desktop. See
        echo      https://docs.docker.com/compose/install/
        echo.
        goto :fail
    )
    set "COMPOSE=docker-compose"
)
echo  [OK] Compose available

rem --- which port? ------------------------------------------------------------
rem Match how Compose resolves it: environment first, then .env, else 8080.

set "PORT=%GUI_PORT%"
if "%PORT%"=="" (
    if exist ".env" (
        for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
            if /i "%%A"=="GUI_PORT" set "PORT=%%B"
        )
    )
)
if "%PORT%"=="" set "PORT=8080"
set "URL=http://localhost:%PORT%"

rem --- build and start --------------------------------------------------------

echo.
echo  Starting containers. The first run builds the images and can take
echo  10-25 minutes; later runs take seconds.
echo.

%COMPOSE% up -d --build
if errorlevel 1 (
    echo.
    echo  [X] Startup failed. Look at the output above, or run:
    echo        %COMPOSE% logs backend
    echo        %COMPOSE% logs web
    echo.
    echo      If it failed while downloading, note that a corporate proxy
    echo      has to be set in two separate places:
    echo        - image downloads: Docker Desktop, Settings,
    echo          Resources, Proxies
    echo        - build and application: set HTTP_PROXY and HTTPS_PROXY
    echo          in your shell before running this, or put them in a
    echo          .env file next to this script
    echo.
    goto :fail
)

rem --- wait until it answers --------------------------------------------------

echo.
echo  Waiting for the application to come up...

set "READY=0"
where curl.exe >nul 2>&1
if errorlevel 1 (
    rem No curl (Windows older than 10 build 1803) - just give it a moment.
    timeout /t 20 /nobreak >nul
    set "READY=1"
) else (
    for /l %%i in (1,1,180) do (
        if "!READY!"=="0" (
            curl.exe -fsS --max-time 2 -o NUL "%URL%" >nul 2>&1
            if not errorlevel 1 (
                set "READY=1"
            ) else (
                timeout /t 1 /nobreak >nul
            )
        )
    )
)

echo.
if "%READY%"=="1" (
    echo  ============================================================
    echo   Dynawo GUI is running
    echo.
    echo     Open  %URL%
    echo.
    echo     Stop it       stop-dynawo-gui.bat
    echo     View logs     %COMPOSE% logs -f
    echo  ============================================================
    echo.
    start "" "%URL%"
) else (
    echo  [!] The application did not answer on port %PORT% within 3 minutes.
    echo      The containers may still be starting. Check with:
    echo        %COMPOSE% ps
    echo        %COMPOSE% logs backend
    echo.
    goto :fail
)

rem Leave the window open so the address stays readable after a double-click.
pause
endlocal
exit /b 0

:fail
echo  Press any key to close this window.
pause >nul
endlocal
exit /b 1
