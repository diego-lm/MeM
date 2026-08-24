@echo off
REM Doble clic para instalar o actualizar MeM. Es todo lo que hace un .exe de
REM instalador, sin el .exe: no hay que compilar nada en cada cambio y Windows
REM no muestra el aviso de SmartScreen que sale con un ejecutable sin firmar.
REM
REM -ExecutionPolicy Bypass vale SOLO para este proceso: no cambia nada del
REM sistema, que es lo que un doble clic no debería poder hacer a tus espaldas.
setlocal
if not exist "%~dp0install.ps1" (
  echo.
  echo   No encuentro install.ps1 al lado de este archivo.
  echo   Los dos tienen que estar en la misma carpeta.
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
