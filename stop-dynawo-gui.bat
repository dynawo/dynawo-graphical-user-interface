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
rem stop-dynawo-gui.bat - stop the containers started by start-dynawo-gui.bat.
rem
rem Simulation results and saved settings are kept: they live in a Docker
rem volume that this script does not touch. To delete them as well, run
rem   docker compose down -v
rem
rem This is the Windows counterpart of docker/stop.sh.

setlocal

cd /d "%~dp0"

echo.
echo  ============================================================
echo   Dynawo GUI - stopping
echo  ============================================================
echo.

docker info >nul 2>&1
if errorlevel 1 (
    echo  [X] The Docker engine is not responding, so there is nothing
    echo      to stop. Start Docker Desktop first if you expected the
    echo      application to be running.
    echo.
    goto :done
)

set "COMPOSE=docker compose"
docker compose version >nul 2>&1
if errorlevel 1 (
    where docker-compose >nul 2>&1
    if errorlevel 1 (
        echo  [X] Docker Compose was not found.
        echo.
        goto :done
    )
    set "COMPOSE=docker-compose"
)

%COMPOSE% down
if errorlevel 1 (
    echo.
    echo  [X] Could not stop the containers. Try running:
    echo        %COMPOSE% ps
    echo.
    goto :done
)

echo.
echo  [OK] Stopped. Your data is preserved.
echo       Start again with start-dynawo-gui.bat
echo.

:done
pause
endlocal
