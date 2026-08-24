"""Qué está usando la máquina AHORA: CPU, RAM, GPU/VRAM y qué modelos hay cargados.

Existe por un problema real (2026-08-05): generar video en local necesita casi toda
la VRAM, y LM Studio deja su modelo residente ~12 GB de los 16 aunque esté ocioso.
Sin ver eso desde el app, el único síntoma es un OOM 15 minutos después.

Sin dependencias nuevas a propósito: `nvidia-smi` para la GPU (funciona con ComfyUI
apagado, que es justo cuando hace falta mirar) y ctypes de la stdlib para CPU y RAM.
psutil haría lo mismo, pero son 30 líneas contra una dependencia más.
"""
import ctypes
import subprocess
import sys
import time

import httpx

SONDA = 2.0             # timeout de las sondas a servicios locales; esto se pinta en vivo
NVIDIA_SMI = 4.0        # arranca lento en frío


# ------------------------------------------------------------------ CPU y RAM

class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def ram() -> dict:
    """{total, libre} en bytes. En lo que no es Windows queda vacío y la UI lo oculta."""
    if sys.platform != "win32":
        return {}
    m = _MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return {}
    return {"total": m.ullTotalPhys, "libre": m.ullAvailPhys}


_previo = None      # (ocioso, total) de la lectura anterior: el % es un delta entre llamadas


def cpu() -> float | None:
    """Uso de CPU en % desde la llamada ANTERIOR a esta función.

    GetSystemTimes da contadores acumulados desde el arranque, así que un solo
    valor no dice nada — hay que restar dos lecturas. En vez de dormir 100 ms
    dentro del request (bloquearía el server), se guarda la anterior: como la UI
    sondea cada pocos segundos, la ventana sale gratis. La primera llamada
    devuelve None porque todavía no hay con qué comparar.
    """
    global _previo
    if sys.platform != "win32":
        return None
    ocio, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(ocio), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    # kernel YA incluye el tiempo ocioso: el total es kernel+user y lo ocupado es total-ocio
    actual = (ocio.value, kernel.value + user.value)
    anterior, _previo = _previo, actual
    if not anterior:
        return None
    d_ocio, d_total = actual[0] - anterior[0], actual[1] - anterior[1]
    if d_total <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * (1 - d_ocio / d_total))), 1)


# ---------------------------------------------------------------------- GPU

def gpus() -> list[dict]:
    """Una entrada por GPU NVIDIA. Lista vacía si no hay nvidia-smi (AMD, portátil
    sin GPU dedicada): la UI muestra el resto igual, no es un error."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=NVIDIA_SMI,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))   # sin parpadeo de consola
    except (OSError, subprocess.SubprocessError):
        return []
    salida = []
    for linea in (r.stdout or "").strip().splitlines():
        campos = [c.strip() for c in linea.split(",")]
        if len(campos) < 4:
            continue
        try:
            # nvidia-smi reporta en MiB
            salida.append({"nombre": campos[0], "uso": float(campos[1]),
                           "vram_usada": int(campos[2]) * 1024**2,
                           "vram_total": int(campos[3]) * 1024**2,
                           "temp": float(campos[4]) if len(campos) > 4 and campos[4].isdigit() else None})
        except ValueError:
            continue        # "[N/A]" en alguna columna (pasa en portátiles con Optimus)
    return salida


# ------------------------------------------------------------ modelos cargados

def modelos(cfg: dict) -> list[dict]:
    """Qué hay cargado ahora mismo, por servicio.

    Solo LM Studio dice qué modelo tiene, con nombre y apellido. ComfyUI NO lo
    expone: los tiene en `current_loaded_models` pero no hay ruta HTTP, y
    `torch_vram_*` de /system_stats no sirve de proxy — con el allocator
    cudaMallocAsync reporta 0.03 GB mientras el proceso retiene 9.35 GB reales
    (medido 2026-08-05). Tampoco se puede repartir la VRAM por proceso: en
    Windows/WDDM `nvidia-smi --query-compute-apps` devuelve [N/A] en la columna
    de memoria. Así que de ComfyUI se informa lo único que es dato duro y además
    es lo que de verdad importa antes de disparar algo: si está trabajando.
    El total de VRAM ocupada sale de la barra de GPU, que sí es exacta.
    """
    salida = []
    base = str(cfg.get("base_url") or "http://localhost:1234/v1").rstrip("/")
    try:
        # /api/v0/ es el REST propio de LM Studio: el /v1/models de OpenAI lista los
        # DISPONIBLES, que no es lo mismo que los cargados. Acá viene `state`.
        r = httpx.get(base.removesuffix("/v1") + "/api/v0/models", timeout=SONDA)
        for m in (r.json().get("data") or []) if r.status_code == 200 else []:
            if m.get("state") and m["state"] != "not-loaded":
                ctx = m.get("max_context_length")
                salida.append({"servicio": "LM Studio", "nombre": m.get("id") or "?",
                               "detalle": " · ".join(filter(None, [
                                   m.get("type"), m.get("quantization"),
                                   f"{ctx // 1024}k ctx" if ctx else ""]))})
    except (httpx.HTTPError, ValueError):
        pass
    comfy = str(cfg.get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")
    try:
        r = httpx.get(comfy + "/queue", timeout=SONDA)
        q = r.json() if r.status_code == 200 else {}
        corriendo, esperando = len(q.get("queue_running") or []), len(q.get("queue_pending") or [])
        if corriendo or esperando:
            salida.append({"servicio": "ComfyUI", "nombre": "generando",
                           "detalle": f"{corriendo} en curso" + (f" · {esperando} en cola" if esperando else "")})
    except (httpx.HTTPError, ValueError):
        pass
    return salida


def estado(cfg: dict) -> dict:
    return {"cpu": cpu(), "ram": ram(), "gpus": gpus(), "modelos": modelos(cfg)}


def demo():
    """Sin red: lo frágil es el delta de CPU (dos lecturas) y el parseo de nvidia-smi."""
    m = ram()
    if sys.platform == "win32":
        assert m["total"] > 0 and 0 < m["libre"] <= m["total"], m
    assert cpu() is None                     # primera lectura: no hay con qué comparar
    # girar de verdad: GetSystemTimes avanza en saltos de ~15.6 ms, así que con
    # menos que eso el delta da 0 y el % sale None aunque todo esté bien
    fin = time.monotonic() + 0.08
    while time.monotonic() < fin:
        pass
    u = cpu()
    if sys.platform == "win32":
        assert u is not None and 0 <= u <= 100, u

    g = gpus()
    for d in g:                              # si no hay NVIDIA la lista es vacía, y está bien
        assert d["vram_total"] > 0 and 0 <= d["vram_usada"] <= d["vram_total"], d
        assert 0 <= d["uso"] <= 100, d

    # servicios caídos: se reporta lo que se pueda, nunca explota
    assert modelos({"base_url": "http://127.0.0.1:1/v1", "comfy_url": "http://127.0.0.1:1"}) == []
    e = estado({"base_url": "http://127.0.0.1:1/v1", "comfy_url": "http://127.0.0.1:1"})
    assert set(e) == {"cpu", "ram", "gpus", "modelos"}
    print(f"recursos ok (cpu {u}% · ram {m.get('libre', 0) / 1024**3:.1f} GB libres · {len(g)} gpu)")


if __name__ == "__main__":
    demo()
