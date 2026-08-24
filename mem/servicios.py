"""Servicios locales que la app necesita cuando los necesita: ComfyUI (modo
crear) y LM Studio (modelos locales). Saber si están corriendo y arrancarlos.

Arrancar NUNCA es automático: siempre media el permiso de Diego — el botón en la
UI o su "sí" en la conversación (la herramienta arrancar_servicio lo exige). El
proceso se lanza suelto (detached) para que siga vivo aunque MeM se reinicie.
"""
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from . import memoria, recursos

ESPERA = 90         # ponytail: ComfyUI tarda ~15 s en frío; margen por si carga pesos
PASO = 2.0
OCIOSO_PASO = 60    # cada cuánto mira el reloj el vigilante de inactividad


def _defs(cfg: dict) -> dict:
    return {
        "comfyui": {
            "sonda": str(cfg.get("comfy_url") or "http://127.0.0.1:8188").rstrip("/") + "/system_stats",
            "cmd": ["cmd", "/c", str(cfg.get("comfy_cmd") or r"W:\ComfyUI\arrancar.bat")],
            "existe": Path(str(cfg.get("comfy_cmd") or r"W:\ComfyUI\arrancar.bat")).is_file(),
        },
        "lmstudio": {
            "sonda": str(cfg.get("base_url") or "http://localhost:1234/v1").rstrip("/") + "/models",
            "cmd": [shutil.which("lms") or "lms", "server", "start"],
            "existe": bool(shutil.which("lms")),
        },
    }


def activo(cfg: dict, nombre: str) -> bool:
    svc = _defs(cfg).get(nombre)
    if not svc:
        return False
    try:
        return httpx.get(svc["sonda"], timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def estado(cfg: dict) -> dict:
    # higgsfield no es arrancable (es un CLI de nube): solo se reporta si está
    return {**{n: activo(cfg, n) for n in _defs(cfg)},
            "higgsfield": bool(shutil.which("higgsfield"))}


def detener_trabajo(cfg: dict) -> tuple[bool, str]:
    """Corta lo que ComfyUI esté generando AHORA y vacía la cola. Un clip de video
    son 12-40 min: sin esto la única salida era matar el proceso y perder el
    modelo cargado (otros minutos para volver a leer 42 GB de disco)."""
    base = str(cfg.get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")
    try:
        # primero la cola (si no, al interrumpir arranca el siguiente), después lo actual
        httpx.post(base + "/queue", json={"clear": True}, timeout=5).raise_for_status()
        httpx.post(base + "/interrupt", timeout=5).raise_for_status()
    except httpx.HTTPError as e:
        return False, f"ComfyUI no respondió en {base} ({type(e).__name__})"
    return True, "generación detenida"


def descargar_modelos(cfg: dict, nombre: str) -> tuple[bool, str]:
    """Suelta la VRAM que retiene un servicio, sin apagarlo.

    ComfyUI deja el modelo residente después de generar y LM Studio deja el suyo
    aunque esté ocioso (~12 de 16 GB, medido): con una sola GPU eso es la
    diferencia entre poder generar y un OOM. Los dos vuelven a cargar solos
    cuando les toca, así que descargar no rompe nada — solo cuesta el tiempo de
    releer el modelo.
    """
    if nombre == "comfyui":
        base = str(cfg.get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")
        try:
            httpx.post(base + "/free", json={"unload_models": True, "free_memory": True},
                       timeout=10).raise_for_status()
        except httpx.HTTPError as e:
            return False, f"ComfyUI no respondió en {base} ({type(e).__name__})"
        # ponytail: /free es un flag que la cola atiende entre trabajos, no un
        # comando síncrono — la VRAM baja un instante después, y el panel (que
        # sondea cada 3 s) lo muestra solo.
        return True, "ComfyUI va a soltar sus modelos"
    if nombre == "lmstudio":
        exe = shutil.which("lms")
        if not exe:
            return False, "el CLI de LM Studio (lms) no está en el PATH"
        r = subprocess.run([exe, "unload", "--all"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        if r.returncode != 0:
            return False, f"lms unload falló: {(r.stderr or r.stdout or '')[-200:]}"
        return True, "modelos de LM Studio descargados"
    return False, f"servicio desconocido: {nombre}"


# ------------------------------------------- descarga automática por inactividad
# Descargar a mano ya se podía (los botones del panel), pero había que acordarse:
# quedaba LM Studio con ~12 de 16 GB tomados horas después del último mensaje, y
# el OOM aparecía recién al disparar la generación siguiente (pedido 2026-08-07).
_ultimo_uso = time.monotonic()
_ya_descargado = False     # una sola descarga por tramo ocioso, no una por minuto


def marcar_uso() -> None:
    """Algo acaba de usar un modelo local: el reloj de inactividad vuelve a cero."""
    global _ultimo_uso, _ya_descargado
    _ultimo_uso, _ya_descargado = time.monotonic(), False


def revisar_ocioso(cfg: dict) -> str:
    """Una ronda del vigilante. Devuelve qué descargó ("" = no tocó nada).

    ponytail: solo ve el uso que pasa POR MeM (más ComfyUI generando, que se
    consulta acá). Si Diego le habla a LM Studio desde su propia ventana, esto no
    se entera y le puede soltar el modelo; el costo es que LM Studio lo relee en
    el siguiente request (JIT), no perder nada. Si algún día molesta, el arreglo
    es que LM Studio exponga un "último uso" — hoy no lo tiene.
    """
    global _ya_descargado
    minutos = float(cfg.get("idle_minutos") or 0)
    if minutos <= 0:
        return ""
    cargados = recursos.modelos(cfg)
    if any(m["servicio"] == "ComfyUI" for m in cargados):
        marcar_uso()        # generando: es uso, aunque no lo haya pedido MeM
        return ""
    if _ya_descargado or time.monotonic() - _ultimo_uso < minutos * 60:
        return ""
    hechos = []
    if any(m["servicio"] == "LM Studio" for m in cargados) and descargar_modelos(cfg, "lmstudio")[0]:
        hechos.append("LM Studio")
    # de ComfyUI no se sabe si retiene algo (recursos.modelos lo explica), así que
    # con que esté vivo se le pide igual: /free sobre un ComfyUI vacío no hace nada
    if activo(cfg, "comfyui") and descargar_modelos(cfg, "comfyui")[0]:
        hechos.append("ComfyUI")
    if hechos:
        _ya_descargado = True
    return ", ".join(hechos)


def vigilar_ocioso(cfg: dict) -> threading.Thread:
    """Hilo de fondo que corre `revisar_ocioso` cada OCIOSO_PASO. Lo arranca el
    lifespan del server (no el import: en los tests no hace falta un hilo)."""
    def bucle():
        while True:
            time.sleep(OCIOSO_PASO)
            try:
                hecho = revisar_ocioso(cfg)
            except Exception:           # un servicio a medio morir no mata el vigilante
                continue
            if hecho:
                memoria.log_evento(cfg["hamuq"], "sistema",
                                   f"VRAM liberada por inactividad ({cfg.get('idle_minutos')} min): {hecho}")
    t = threading.Thread(target=bucle, daemon=True, name="mem-ocioso")
    t.start()
    return t


def cargado(base_url: str, modelo: str) -> bool:
    """¿LM Studio ya tiene ESE modelo en memoria?

    /api/v0/models es el REST propio de LM Studio y trae `state`; el /v1/models de
    OpenAI lista los disponibles, que no es lo mismo. Ante la duda (no contesta, no
    es LM Studio, el modelo no está en su catálogo) devuelve True: mejor no avisar
    nada que avisar de una carga que no va a pasar.
    """
    base = str(base_url or "").rstrip("/")
    if not base or not modelo:
        return True
    try:
        r = httpx.get(base.removesuffix("/v1") + "/api/v0/models", timeout=2)
        datos = r.json().get("data") or []
    except (httpx.HTTPError, ValueError):
        return True
    return next((m.get("state", "loaded") != "not-loaded" for m in datos if m.get("id") == modelo), True)


def preparar(cfg: dict, ag: dict, on_event=None) -> None:
    """Antes de mandarle un turno a un modelo local: avisar y hacerle sitio.

    LM Studio carga el modelo recién con el primer request (JIT) y eso son minutos
    en silencio — desde el chat parecía que no pasaba nada (pedido 2026-08-05). Y
    con una sola GPU, si ComfyUI sigue reteniendo su modelo, la carga puede no
    entrar: se le pide que suelte la VRAM primero. /free lo atiende la cola ENTRE
    trabajos, así que no corta nada que esté generando.

    No carga el modelo: de eso se encarga LM Studio sola al recibir el request.
    """
    if ag.get("proveedor") != "openai":
        return
    marcar_uso()        # este turno lo atiende un modelo local: reloj de ocioso a cero
    if cargado(ag.get("base_url", ""), str(ag.get("modelo") or "")):
        return
    if activo(cfg, "comfyui"):
        if on_event:
            on_event("liberando_vram", {"servicio": "ComfyUI"})
        descargar_modelos(cfg, "comfyui")
    if on_event:
        on_event("cargando_modelo", {"modelo": str(ag.get("modelo") or "")})


def _log_path(nombre: str) -> Path:
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "MeM" / f"{nombre}.log"


def _log(nombre: str):
    p = _log_path(nombre)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p.open("ab")


def log_tail(nombre: str, n: int = 200) -> list[str]:
    """Últimas líneas del log del servicio (lo que iría a la consola que ya no hay)."""
    p = _log_path(nombre)
    if not p.is_file():
        return []
    return p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]


def arrancar(cfg: dict, nombre: str) -> tuple[bool, str]:
    """Lanza el servicio suelto y espera a que responda. (ok, mensaje)."""
    svc = _defs(cfg).get(nombre)
    if not svc:
        return False, f"servicio desconocido: {nombre} (hay: {', '.join(_defs(cfg))})"
    if activo(cfg, nombre):
        return True, f"{nombre} ya está corriendo"
    if not svc["existe"]:
        return False, (f"no encuentro cómo arrancar {nombre}: falta {svc['cmd'][-1]}"
                       if nombre == "comfyui" else
                       "no encuentro el CLI `lms` de LM Studio en el PATH")
    # CREATE_NO_WINDOW (no DETACHED): con DETACHED el cmd quedaba sin consola,
    # pero el python que lanza el .bat se alocaba una consola NUEVA Y VISIBLE y
    # el stdout se iba ahí (ventana en pantalla, log en 0 bytes — visto 2026-08).
    # NO_WINDOW crea una consola invisible que los hijos heredan: sin ventana y
    # el log en %LOCALAPPDATA%\MeM\ ahora sí junta la salida. Sobrevive a MeM igual.
    subprocess.Popen(svc["cmd"], stdout=_log(nombre), stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    fin = time.monotonic() + ESPERA
    while time.monotonic() < fin:
        if activo(cfg, nombre):
            return True, f"{nombre} arrancado"
        time.sleep(PASO)
    return False, (f"{nombre} no respondió en {ESPERA:.0f}s — mirar "
                   f"{Path(os.environ.get('LOCALAPPDATA', '.')) / 'MeM' / (nombre + '.log')}")


# --------------------------------------------- reinicio remoto del servidor

# Ayudante suelto: espera a que el proceso viejo suelte el puerto y arranca la
# misma línea de comandos. Va por fuera porque un proceso no puede esperarse a
# sí mismo, y el tray (scripts/mem_tray.ps1) adopta al nuevo por puerto.
AYUDANTE = """
import socket, subprocess, sys, time
puerto, cwd = int(sys.argv[1]), sys.argv[2]
fin = time.time() + 20
while time.time() < fin:
    s = socket.socket(); s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", puerto)); s.close(); time.sleep(0.2)
    except OSError:
        s.close(); break
subprocess.Popen(sys.argv[3:], cwd=cwd,
                 creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
"""


def _cmd_servidor(cfg: dict, argv: list[str]) -> tuple[int, list[str]]:
    """(puerto, comando) para relanzar ESTE servidor. argv[1:] se reusa tal cual
    si vino de `-m uvicorn` (conserva puerto y flags); si el server se lanzó por
    otra vía (mem serve) se reconstruye la forma canónica."""
    puerto = (int(argv[argv.index("--port") + 1]) if "--port" in argv
              else int(cfg.get("puerto") or 8765))
    args = (argv[1:] if any("mem.api:app" in a for a in argv[1:])
            else ["mem.api:app", "--port", str(puerto)])
    return puerto, [sys.executable, "-m", "uvicorn", *args]


def reiniciar_servidor(cfg: dict) -> int:
    """Programa el relevo y devuelve el puerto. El proceso actual muere en <1 s
    (después de responder la petición); el ayudante arranca al sucesor."""
    puerto, cmd = _cmd_servidor(cfg, list(sys.argv))
    repo = Path(__file__).resolve().parent.parent
    subprocess.Popen([sys.executable, "-c", AYUDANTE, str(puerto), str(repo), *cmd],
                     stdout=_log("servidor"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    threading.Timer(0.8, os._exit, [0]).start()   # ya respondimos; salida dura a propósito
    return puerto


def demo():
    """Sin arrancar nada: la tabla y la sonda, que es lo que se rompe callado."""
    cfg = {"comfy_url": "http://127.0.0.1:1", "base_url": "http://127.0.0.1:1/v1",
           "comfy_cmd": r"C:\no\existe.bat"}
    assert set(_defs(cfg)) == {"comfyui", "lmstudio"}
    assert set(estado(cfg)) == {"comfyui", "lmstudio", "higgsfield"}
    assert activo(cfg, "comfyui") is False          # puerto 1: nadie escucha
    assert activo(cfg, "fantasma") is False
    assert log_tail("servicio-que-no-existe") == []
    ok, msg = arrancar(cfg, "fantasma")
    assert not ok and "desconocido" in msg
    ok, msg = arrancar(cfg, "comfyui")              # cmd inexistente: no lanza nada
    assert not ok and "falta" in msg
    assert _defs(cfg)["comfyui"]["sonda"] == "http://127.0.0.1:1/system_stats"

    # detener/descargar contra un servicio caído: reportan, no explotan
    ok, msg = detener_trabajo(cfg)
    assert not ok and "no respondió" in msg, msg
    ok, msg = descargar_modelos(cfg, "comfyui")
    assert not ok and "no respondió" in msg, msg
    ok, msg = descargar_modelos(cfg, "fantasma")
    assert not ok and "desconocido" in msg, msg

    # preparar: sin LM Studio del otro lado no inventa avisos ni toca ComfyUI
    assert cargado("", "x") is True and cargado("http://127.0.0.1:1/v1", "x") is True
    avisos = []
    preparar(cfg, {"proveedor": "claude_code", "modelo": "sonnet"}, lambda n, a: avisos.append(n))
    preparar(cfg, {"proveedor": "openai", "base_url": "http://127.0.0.1:1/v1", "modelo": "x"},
             lambda n, a: avisos.append(n))
    assert avisos == [], avisos

    # vigilante de inactividad, sin esperar minutos: el reloj es una variable
    global _ultimo_uso
    marcar_uso()
    assert revisar_ocioso({**cfg, "idle_minutos": 15}) == ""      # recién usado
    _ultimo_uso -= 16 * 60                                        # 16 minutos atrás
    assert revisar_ocioso({**cfg, "idle_minutos": 0}) == ""       # apagado: no toca nada
    assert revisar_ocioso({**cfg, "idle_minutos": 15}) == ""      # ocioso, pero no hay servicios vivos
    assert not _ya_descargado                                     # nada que descargar ≠ tramo cerrado
    marcar_uso()
    assert time.monotonic() - _ultimo_uso < 1

    # reconstrucción del comando de relanzamiento, según cómo se arrancó
    p, cmd = _cmd_servidor({"puerto": 8765}, ["...uvicorn", "mem.api:app", "--port", "8766"])
    assert p == 8766 and cmd[-3:] == ["mem.api:app", "--port", "8766"]  # conserva flags reales
    p, cmd = _cmd_servidor({"puerto": 8765}, ["...cli.py", "serve"])    # mem serve: forma canónica
    assert p == 8765 and cmd[-3:] == ["mem.api:app", "--port", "8765"]
    assert cmd[1:3] == ["-m", "uvicorn"]
    print("servicios ok")


if __name__ == "__main__":
    demo()
