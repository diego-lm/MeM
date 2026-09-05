# Icono de bandeja para MeM: arranca el servidor y ofrece Abrir/Reiniciar/Salir.
# Corre bajo pythonw.exe (proceso GUI, sin consola): no existe ninguna ventana
# que cerrar por accidente — reemplaza a mem_tray.ps1, cuyo powershell.exe de
# consola quedaba visible/oculto y al cerrarlo se llevaba el icono.
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "Scripts" / "python.exe"
ICONO = REPO / "mem" / "static" / "assets" / "favicon.ico"
VERSION_JS = REPO / "mem" / "static" / "js" / "version.js"
LOG = REPO / "mem_tray.log"
PUERTO = 8765
URL = f"http://localhost:{PUERTO}"
SIN_VENTANA = subprocess.CREATE_NO_WINDOW

# misma mutex que usaba el tray de PowerShell: nunca dos iconos a la vez,
# ni siquiera durante la transición de uno al otro.
# use_last_error+get_last_error (no ctypes.windll.kernel32.GetLastError() suelto):
# entre dos llamadas windll separadas, la maquinaria de ctypes puede pisar el
# último error de Win32 antes de leerlo — visto en vivo (2026-08-06): dos
# copias del tray coexistiendo, cada una con su propio uvicorn en el :8765.
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW(None, False, "Global\\MeMTrayIcon")
if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit()

proc = None


def escuchando():
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PUERTO)) == 0


def pid_adoptado():
    """PID del uvicorn que tiene el puerto (de un tray anterior que murió sucio).
    Filtra por dirección local porque tailscaled también escucha en 8765 (el
    serve que expone MeM al móvil), y por nombre de proceso antes de matar."""
    salida = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                            text=True, creationflags=SIN_VENTANA).stdout
    for linea in salida.splitlines():
        p = linea.split()
        if len(p) >= 5 and p[3] == "LISTENING" and p[1] in (f"0.0.0.0:{PUERTO}", f"127.0.0.1:{PUERTO}"):
            pid = int(p[4])
            tl = subprocess.run(["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"],
                                capture_output=True, text=True, creationflags=SIN_VENTANA).stdout
            return pid if "python" in tl.lower() else None
    return None


def arrancar(*_):
    global proc
    if proc and proc.poll() is None:
        return
    # Si el tray murió sucio, su uvicorn sobrevive y sigue sirviendo: adoptarlo.
    # Arrancar otro solo daría un proceso que no puede tomar el puerto y muere.
    if escuchando():
        return
    proc = subprocess.Popen([str(PY), "-m", "uvicorn", "mem.api:app", "--port", str(PUERTO)],
                            cwd=REPO, stdout=open(LOG, "w"), stderr=open(f"{LOG}.err", "w"),
                            creationflags=SIN_VENTANA)


def parar():
    global proc
    if proc and proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)
    proc = None
    pid = pid_adoptado()  # el adoptado no está en proc
    if pid:
        subprocess.run(["taskkill", "/f", "/pid", str(pid)],
                       capture_output=True, creationflags=SIN_VENTANA)


def abrir(*_):
    os.startfile(URL)


def reiniciar(icono, _):
    parar()
    # esperar a que el puerto se libere de verdad: con un sleep fijo, arrancar()
    # veía el socket todavía en Listen y se saltaba el arranque
    for _ in range(20):
        if not escuchando():
            break
        time.sleep(0.15)
    arrancar()
    icono.icon = ICONO_BASE
    icono.notify("Servidor reiniciado", "MeM")


def version_disco():
    """`n:` de version.js — el mismo regex que usa mem/api.py para VERSION."""
    try:
        m = re.search(r"n:\s*(\d+)", VERSION_JS.read_text(encoding="utf-8"))
        return int(m.group(1)) if m else None
    except OSError:
        return None


def version_server():
    try:
        with urllib.request.urlopen(f"{URL}/health", timeout=2) as r:
            return json.load(r).get("version")
    except Exception:
        return None


def con_luz(base):
    """Compone un puntito de alerta arriba a la derecha del ícono base."""
    img = base.convert("RGBA").copy()
    w, h = img.size
    r = w // 3
    d = ImageDraw.Draw(img)
    d.ellipse((w - r, 0, w, r), fill=(230, 70, 40, 255), outline=(255, 255, 255, 255), width=max(1, w // 24))
    return img


ICONO_BASE = Image.open(ICONO)
ICONO_LUZ = con_luz(ICONO_BASE)


def vigilar_actualizacion():
    # ponytail: poll simple cada 5 min — no hace falta websocket para un puntito.
    # Compara el proceso VIVO (server) contra el código en disco: si difieren,
    # el .js ya cambió pero el Python del arranque sigue siendo el viejo
    # (mismo chequeo que Ajustes, acá reflejado en el ícono de bandeja).
    while True:
        srv = version_server()
        disco = version_disco()
        if srv is not None and disco is not None:
            tray.icon = ICONO_LUZ if srv != disco else ICONO_BASE
        time.sleep(300)


def vigilar():
    # ponytail: al arrancar Windows, Hyper-V/WinNAT puede tener reservado el 8765 un
    # rato (el rango dinamico de puertos arranca en 1024): uvicorn muere con WinError
    # 10013 y el icono quedaba vivo sin servidor (visto 2026-09-04). Reintenta cada
    # 10 s durante 5 min; despues queda "Reiniciar" del menu. Fix definitivo: reservar
    # el puerto con netsh (MANUAL.md, Solucion de problemas).
    for _ in range(30):
        time.sleep(10)
        if escuchando():
            return
        arrancar()


def salir(icono, _):
    parar()
    icono.stop()


tray = pystray.Icon("MeM", ICONO_BASE, "MeM", pystray.Menu(
    pystray.MenuItem("Abrir MeM", abrir, default=True),  # default = clic izquierdo
    pystray.MenuItem("Reiniciar", reiniciar),
    pystray.MenuItem("Salir", salir)))
arrancar()
threading.Thread(target=vigilar, daemon=True).start()
threading.Thread(target=vigilar_actualizacion, daemon=True).start()
tray.run()
