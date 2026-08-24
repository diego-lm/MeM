"""Instalar y desinstalar los componentes opcionales de la máquina desde la app.

El motor es **winget**, el gestor de paquetes que ya trae Windows: instalar,
desinstalar y "¿está instalado?" son suyos. Acá no se descarga, no se
desempaqueta y no se lleva registro de versiones — eso ya lo hace el sistema
operativo mejor de lo que lo haría este archivo.

El catálogo (qué se ofrece y con qué id de winget) es DATO: `componentes.json`
en la raíz, que lee también `install.ps1`. La lista de la app y la del
instalador son la misma o se separan al primer cambio.

Instalar y desinstalar NUNCA son automáticos: los dispara un clic explícito, y
desinstalar además pide confirmación en la UI. Es el mismo criterio que
`servicios.arrancar`.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import servicios

CATALOGO = Path(__file__).resolve().parent.parent / "componentes.json"
ESPERA_LISTA = 25       # `winget list` es local, pero la primera del día actualiza fuentes
CACHE_SEG = 30          # cuánto vale una lectura de winget antes de volver a preguntar
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # 0 fuera de Windows

# id -> winget corriendo. Se pierde si el server se reinicia a mitad de una
# instalación: no pasa nada, el estado real lo sigue diciendo `winget list`
# cuando termine — esto solo alimenta el "instalando…" de la pantalla.
_trabajos: dict[str, subprocess.Popen] = {}
_cache: dict = {"t": 0.0, "datos": {}}


def winget() -> str | None:
    """Ruta a winget.exe, o None si no está.

    `shutil.which` no alcanza: el alias de App Installer vive en
    %LOCALAPPDATA%\\Microsoft\\WindowsApps, que NO siempre está en el PATH del
    proceso — comprobado en esta máquina (winget instalado y funcionando, y
    `where winget` vacío). Sin este fallback la pantalla diría "winget no está"
    con winget instalado.
    """
    if exe := shutil.which("winget"):
        return exe
    alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "winget.exe"
    return str(alias) if alias.is_file() else None


def catalogo() -> dict:
    """componentes.json sin las claves de comentario. {} si falta o está roto:
    la pantalla se queda sin componentes, no sin Ajustes."""
    try:
        datos = json.loads(CATALOGO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in datos.items() if not k.startswith("_") and isinstance(v, dict)}


def _log_id(id_: str) -> str:
    return f"comp-{id_}"


def _winget_tiene(exe: str, wid: str) -> bool:
    """`winget list --id X --exact`: exit 0 = instalado. Cuando no está devuelve
    -1978335212 (NO_APPLICATIONS_FOUND), así que alcanza con mirar el código."""
    try:
        r = subprocess.run([exe, "list", "--id", wid, "--exact",
                            "--accept-source-agreements", "--disable-interactivity"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=ESPERA_LISTA, creationflags=SIN_VENTANA)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _a_mano(cfg: dict, comp: dict) -> bool:
    """¿Está en la máquina sin haber pasado por winget? Dos señales, las dos
    declaradas en el catálogo: un ejecutable en el PATH (`lms`, `ollama`) o el
    archivo al que apunta un ajuste (`comfy_cmd` → el .bat de ComfyUI).

    Importa porque desinstalar por winget algo que winget no instaló falla con
    un error ilegible: si está a mano, no se ofrece el botón.
    """
    if (exe := comp.get("exe")) and shutil.which(exe):
        return True
    clave = comp.get("cfg_archivo")
    return bool(clave and str(cfg.get(clave) or "") and Path(str(cfg[clave])).is_file())


def estado(cfg: dict, refrescar: bool = False) -> dict:
    """Catálogo + en qué anda cada componente, para `GET /components`.

    `instalacion` es "winget" (se puede desinstalar), "manual" (está, pero no lo
    puso winget: no se toca) o "no". Las consultas a winget se cachean CACHE_SEG:
    la pantalla hace poll y cada lectura es un proceso nuevo por componente.
    """
    exe = winget()
    comps = catalogo()
    ahora = time.monotonic()
    frescas = not refrescar and (ahora - _cache["t"]) < CACHE_SEG
    consultado = dict(_cache["datos"]) if frescas else {}

    salida = {}
    for id_, comp in comps.items():
        if exe and id_ not in consultado:
            consultado[id_] = _winget_tiene(exe, comp["winget"])
        por_winget = bool(consultado.get(id_))
        trabajo = _trabajos.get(id_)
        activo = bool(trabajo and trabajo.poll() is None)
        salida[id_] = {
            "id": id_,
            "nombre": comp.get("nombre", id_),
            "que_es": comp.get("que_es", {}),
            "winget": comp["winget"],       # install.ps1 instala con esto; la UI lo muestra de pista
            "instalacion": "winget" if por_winget else ("manual" if _a_mano(cfg, comp) else "no"),
            # si está corriendo lo dice /services, que la pantalla ya sondea: acá
            # la pregunta es si ESTÁ, no si está prendido
            # el log solo mientras hay (o hubo) algo que mirar: winget escribe
            # progreso, y un fallo sin la salida a la vista no se puede diagnosticar
            "trabajo": {"activo": activo, "log": servicios.log_tail(_log_id(id_), 12)} if trabajo else None,
        }
    _cache.update(t=ahora, datos=consultado)
    return {"winget": bool(exe), "componentes": salida}


def _correr(cfg: dict, id_: str, accion: str) -> tuple[bool, str]:
    """Lanza winget suelto y vuelve enseguida: instalar LM Studio son cientos de
    MB y varios minutos — una petición HTTP esperando eso muere por timeout antes
    de terminar. La pantalla mira `estado()` hasta que el trabajo se apaga."""
    comp = catalogo().get(id_)
    if not comp:
        return False, f"componente desconocido: {id_} (hay: {', '.join(catalogo()) or 'ninguno'})"
    exe = winget()
    if not exe:
        return False, ("no encuentro winget (App Installer). Se instala desde la Microsoft Store "
                       "como 'Instalador de aplicaciones'.")
    if (t := _trabajos.get(id_)) and t.poll() is None:
        return False, f"{comp['nombre']}: ya hay una operación en curso"
    if accion == "uninstall" and estado(cfg, refrescar=True)["componentes"][id_]["instalacion"] != "winget":
        return False, (f"{comp['nombre']} no lo instaló winget — si está en la máquina, "
                       "se saca por donde se puso")

    comun = ["--id", comp["winget"], "--exact", "--silent",
             "--accept-source-agreements", "--disable-interactivity"]
    cmd = ([exe, "install", *comun, "--accept-package-agreements"] if accion == "install"
           else [exe, "uninstall", *comun])
    _trabajos[id_] = subprocess.Popen(cmd, stdout=servicios._log(_log_id(id_)),
                                      stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      creationflags=SIN_VENTANA)
    _cache["t"] = 0.0        # el próximo estado() vuelve a preguntarle a winget
    verbo = "Instalando" if accion == "install" else "Desinstalando"
    return True, f"{verbo} {comp['nombre']} — puede tardar unos minutos"


def instalar(cfg: dict, id_: str) -> tuple[bool, str]:
    return _correr(cfg, id_, "install")


def desinstalar(cfg: dict, id_: str) -> tuple[bool, str]:
    return _correr(cfg, id_, "uninstall")


def demo():
    """Sin instalar ni desinstalar nada: el catálogo, la detección y los rechazos,
    que es lo que decide si un botón aparece y si borra algo que no debía."""
    cat = catalogo()
    assert set(cat) >= {"lmstudio", "ollama", "comfyui"}, cat
    assert all("winget" in c and "nombre" in c for c in cat.values()), cat
    assert not any(k.startswith("_") for k in cat), "el comentario del JSON no es un componente"

    # detección "a mano": las dos señales del catálogo, y ninguna inventada
    assert _a_mano({}, {"exe": "cmd"}) is True                     # cmd siempre está
    assert _a_mano({}, {"exe": "no-existe-jamas-xyz"}) is False
    assert _a_mano({}, {}) is False                                # sin señales: no está
    assert _a_mano({"comfy_cmd": r"C:\no\existe.bat"}, {"cfg_archivo": "comfy_cmd"}) is False
    assert _a_mano({"comfy_cmd": ""}, {"cfg_archivo": "comfy_cmd"}) is False   # vacío no es un archivo
    yo = str(Path(__file__).resolve())
    assert _a_mano({"comfy_cmd": yo}, {"cfg_archivo": "comfy_cmd"}) is True

    # un id que no existe no lanza nada
    ok, msg = instalar({}, "fantasma")
    assert not ok and "desconocido" in msg, msg
    assert not _trabajos, "un componente inexistente no puede haber dejado un proceso"

    # desinstalar algo que winget no puso se rechaza ANTES de correr winget: es
    # la barrera que evita un `winget uninstall` condenado al error ilegible
    cfg = {"comfy_cmd": yo}          # ComfyUI "a mano" (este archivo existe)
    ok, msg = desinstalar(cfg, "comfyui")
    assert not ok and ("winget" in msg or "App Installer" in msg), msg
    assert not _trabajos, msg

    e = estado(cfg)
    assert set(e) == {"winget", "componentes"} and set(e["componentes"]) == set(cat)
    comfy = e["componentes"]["comfyui"]
    assert comfy["instalacion"] in ("winget", "manual") and comfy["trabajo"] is None
    assert all(c["instalacion"] in ("winget", "manual", "no") for c in e["componentes"].values())
    assert "corriendo" not in comfy, "si está prendido lo dice /services, no esto"

    # El comando exacto que se le pasa a winget, SIN correrlo: son las banderas
    # que hacen la diferencia entre una instalación silenciosa y una que se
    # queda esperando un "¿acepta los términos?" que nadie va a ver.
    # Ojo: subprocess.run usa Popen por dentro, así que acá cae también el
    # `winget list` de la detección — por eso se filtra por verbo.
    if e["winget"]:
        lanzados, real = [], subprocess.Popen
        subprocess.Popen = lambda cmd, **kw: lanzados.append(list(cmd)) or real(
            [sys.executable, "-c", ""], **kw)      # un proceso inofensivo que termina solo
        try:
            _trabajos.clear()
            assert instalar({}, "ollama")[0]
            desinstalar({}, "lmstudio")   # pasa o no según lo que diga winget; el comando es lo que importa
        finally:
            subprocess.Popen = real
            _trabajos.clear()
        inst = next(c for c in lanzados if c[1] == "install")
        assert inst[2:5] == ["--id", "Ollama.Ollama", "--exact"], inst
        assert {"--silent", "--accept-package-agreements", "--accept-source-agreements",
                "--disable-interactivity"} <= set(inst), inst
        # desinstalar NO lleva --accept-package-agreements: winget la rechaza ahí
        for cmd in [c for c in lanzados if c[1] == "uninstall"]:
            assert "--accept-package-agreements" not in cmd, cmd
            assert {"--silent", "--disable-interactivity"} <= set(cmd), cmd
        assert not any(c[1] == "install" and "Ollama" not in " ".join(c) for c in lanzados), lanzados
    print(f"componentes ok (winget={'sí' if e['winget'] else 'no'})")


if __name__ == "__main__":
    demo()
