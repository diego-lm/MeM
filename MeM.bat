@echo off
REM Relanza el icono de bandeja de MeM y, con el, el servidor en :8765.
REM Doble clic cuando el icono desaparezca de la barra de Windows.
REM Si el tray ya esta vivo, su mutex hace que esta copia salga sola.
REM pythonw = proceso sin consola: no queda NINGUNA ventana abierta ni oculta.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0scripts\mem_tray.py"
