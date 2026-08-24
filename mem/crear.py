"""Crear medios y catalogarlos: el espejo de media.py (texto → medio).

Dos caminos, una sola catalogación:
- `crear_imagen` / `crear_video`: ComfyUI local (workflows JSON en
  09_Sistema/Workflows/, datos como los modos). Genera, guarda en adjuntos y
  cataloga solo. Mismo motor para los dos: lo único que cambia es qué JSON corre
  y cuánto se espera (`_timeout` lo declara el propio workflow).
- `guardar_creacion`: cataloga un medio generado por CUALQUIER backend — la URL
  que devuelve una herramienta de Higgsfield o una ruta local. Baja el archivo,
  lo describe con visión (media.extraer), lo titula y etiqueta (triaje.evaluar)
  y lo guarda como entrada bajo Creaciones/ con el prompt y la descripción en el
  cuerpo: así una creación se encuentra después por su contenido, igual que
  cualquier otra memoria.
"""
import json
import random
import re
import shutil
import subprocess
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import httpx

from . import agentes, media, memoria, servicios, triaje

TIMEOUT = 900           # tope por defecto (una imagen es un minuto); cada workflow puede pedir más con "_timeout"
FPS = 24                # los workflows de video corren a 24 fps: segundos → fotogramas
FRAMES = 124            # ~5.2 s. MiniMax H3 usa la grilla 17n+5 y el nodo redondea solo hacia arriba
PASO = 2.0              # segundos entre polls a /history
ADJUNTOS = "07_Inbox/_adjuntos"
WORKFLOWS = "09_Sistema/Workflows"

# Solo se bajan URLs de estos hosts (lo que devuelven las tools de Higgsfield),
# no cualquier URL que al modelo se le ocurra. Sufijos; ampliar cuando un
# resultado real muestre otro CDN.
HOSTS = ("higgsfield.ai", "amazonaws.com", "cloudfront.net", "googleapis.com")

EXT_POR_TIPO = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                "image/gif": ".gif", "video/mp4": ".mp4", "video/webm": ".webm",
                "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/ogg": ".ogg",
                "audio/mp4": ".m4a"}

TOOLS = [
    {"name": "crear_imagen",
     "description": ("Genera una imagen con el modelo LOCAL (ComfyUI), gratis y privado. "
                     "El prompt debe ser visual, explícito y EN INGLÉS. Con `referencia` "
                     "(ruta de una imagen de la base) EDITA esa imagen según el prompt. "
                     "La imagen queda catalogada sola en la memoria. Devuelve markdown "
                     "![...](...): inclúyelo tal cual en tu respuesta para que Diego la vea."),
     "parameters": {"type": "object", "properties": {
         "prompt": {"type": "string", "description": "descripción visual en inglés"},
         "workflow": {"type": "string", "description": "workflow a usar (por defecto 'imagen')"},
         "referencia": {"type": "string", "description": "ruta de una imagen de la base para editar/variar"},
         "semilla": {"type": "integer"}},
         "required": ["prompt"]}},
    {"name": "crear_video",
     "description": ("Genera un VIDEO CON AUDIO con el modelo LOCAL (ComfyUI + MiniMax H3), "
                     "gratis y privado. Tarda ~12 min por cada 5 s de clip (medido) y ocupa la "
                     "GPU entera: avísale a Diego del tiempo en la misma respuesta, pero NO le "
                     "pidas permiso — ya está autorizado y no hay dónde contestarte. "
                     "El prompt va EN INGLÉS y describe también el AUDIO (diálogo, efectos, "
                     "música): el modelo genera imagen y sonido en la misma pasada, así que lo "
                     "que no pidas no suena. Con `referencia` (ruta de una imagen de la base) "
                     "esa imagen es el primer fotograma. Queda catalogado solo. Devuelve "
                     "markdown ![...](...): inclúyelo tal cual en tu respuesta."),
     "parameters": {"type": "object", "properties": {
         "prompt": {"type": "string", "description": "qué se ve Y qué se oye, en inglés"},
         "segundos": {"type": "number", "description": "duración, de 4 a 15 (por defecto 5)"},
         "referencia": {"type": "string", "description": "ruta de una imagen de la base = primer fotograma"},
         "workflow": {"type": "string", "description": "workflow a usar (por defecto 'video')"},
         "semilla": {"type": "integer"}},
         "required": ["prompt"]}},
    {"name": "modelos_nube",
     "description": ("Lista los modelos de generación disponibles en Higgsfield (nube) con "
                     "sus job_types. Úsala antes de crear_nube si no sabes qué modelo usar. "
                     "Filtro opcional: 'image', 'video', 'audio'."),
     "parameters": {"type": "object", "properties": {
         "tipo": {"type": "string", "enum": ["image", "video", "audio", ""]}}}},
    {"name": "crear_nube",
     "description": ("Genera imagen, video o audio en la NUBE (Higgsfield) con la cuenta de "
                     "Diego. GASTA CRÉDITOS de su plan, así que la APP le muestra un diálogo "
                     "de confirmación antes de ejecutarla: NO se la pidas por escrito, llamala "
                     "y dejá que el diálogo pregunte. Si Diego dice que no, te llega como "
                     "resultado de la tool. `modelo` = job_type de Higgsfield "
                     "(consultar con modelos_nube). El resultado queda catalogado solo en la "
                     "memoria. Devuelve markdown: inclúyelo en tu respuesta."),
     "parameters": {"type": "object", "properties": {
         "modelo": {"type": "string", "description": "job_type de Higgsfield"},
         "prompt": {"type": "string", "description": "prompt en inglés"},
         "params": {"type": "object", "description": (
             "SOLO lo que Diego pidió explícitamente en su mensaje (duration, "
             "aspect_ratio, quality, resolution…, según el job_type). Lo que no pongas "
             "acá se lo pregunta la app en su diálogo: no lo inventes ni se lo "
             "preguntes por escrito")},
         "referencia": {"type": "string", "description": (
             "ruta de un medio de la base para partir de él: en un modelo de video es "
             "el PRIMER FOTOGRAMA (se anima esa imagen), en uno de imagen es la "
             "referencia a editar")}},
         "required": ["modelo", "prompt"]}},
    {"name": "arrancar_servicio",
     "description": ("Arranca un servicio local que no está corriendo (comfyui para generar "
                     "en local, lmstudio para modelos locales). Es la máquina de Diego y no "
                     "cuesta nada: llamala directo, sin pedir permiso. Tarda unos segundos; "
                     "al terminar reintenta lo que estabas haciendo."),
     "parameters": {"type": "object", "properties": {
         "servicio": {"type": "string", "enum": ["comfyui", "lmstudio"]}},
         "required": ["servicio"]}},
    {"name": "guardar_creacion",
     "description": ("Cataloga en la memoria un medio ya generado (imagen, video o audio): "
                     "lo baja si es una URL https, lo describe y crea su entrada bajo "
                     "Creaciones/. Úsala si un medio generado quedó sin catalogar. "
                     "Devuelve markdown del medio: inclúyelo en tu respuesta."),
     "parameters": {"type": "object", "properties": {
         "origen": {"type": "string", "description": "URL https del medio o ruta local"},
         "prompt": {"type": "string", "description": "el prompt con el que se generó"},
         "modelo": {"type": "string", "description": "modelo/servicio usado (ej. higgsfield soul)"},
         "nota": {"type": "string", "description": "contexto extra para la entrada"}},
         "required": ["origen", "prompt"]}},
]
NOMBRES = tuple(t["name"] for t in TOOLS)
# Las que gastan plata de verdad (créditos del plan de Diego, cuentas de nube): la
# app las frena y le muestra un diálogo antes de ejecutarlas. Lo local nunca pregunta
# (pedido 2026-08-06). Un backend de nube nuevo se suma acá y hereda el diálogo.
CONFIRMAR = ("crear_nube",)
# Las dos respuestas posibles a ese diálogo cuando Diego dice que no. Viajan al
# modelo como resultado de la tool, así que dicen QUÉ hacer a continuación: la
# segunda existe porque decir "no" y tener que escribir después "hacelo en local"
# eran dos mensajes para lo mismo (pedido 2026-08-07).
RECHAZO = ("(Diego NO autorizó esta generación en la nube. No la reintentes ni la vuelvas a "
           "llamar: decile qué se canceló y ofrecele la alternativa local.)")
RECHAZO_LOCAL = ("(Diego dijo que no a la nube y lo quiere EN LOCAL: llamá YA, en este mismo turno, "
                 "a crear_imagen o crear_video con el mismo prompt y la misma referencia. "
                 "No le preguntes nada ni le pidas permiso.)")
# Y la tercera: el diálogo se abrió y nadie lo contestó (Diego no estaba mirando esa
# pantalla). No es un "no" suyo, y decirle al modelo que lo fue lo mandaba a
# reintentar; decirle la verdad —nadie contestó— le deja ofrecer el local.
SIN_RESPUESTA = ("(Nadie contestó el diálogo de autorización, así que la generación en la nube se "
                 "canceló sola. NO la reintentes: contale a Diego que la pregunta quedó sin "
                 "responder y ofrecele generarlo en local.)")
# Las que aceptan `referencia`: el material real como input (se sube el archivo a
# ComfyUI / se pasa --image-references al CLI), no un prompt que lo describa.
CON_REFERENCIA = ("crear_imagen", "crear_video", "crear_nube")

_BACKEND = {"comfyui": "local (ComfyUI, {tool})", "higgsfield": "nube (Higgsfield, crear_nube)",
            "preguntar": "preguntarle a Diego cuál usar"}
_LOCAL = {"imagen": "crear_imagen", "video": "crear_video"}   # audio local todavía no hay
_TIPO_TOOL = {v: k for k, v in _LOCAL.items()}                # crear_imagen → imagen
_TIPO_NUBE = {"image": "imagen", "video": "video", "audio": "audio"}   # como los nombra Higgsfield
TIPO_API = {v: k for k, v in _TIPO_NUBE.items()}                      # imagen → image (filtro del catálogo)

# Con cuál quiere generar Diego, dicho en su propio mensaje: eso manda por encima de
# Ajustes (y de esta corrección). Sin ninguna de las dos, manda el ajuste.
RX_PIDE_LOCAL = re.compile(r"\b(local\w*|comfy\w*|offline|gratis|sin gastar|en mi (pc|m[aá]quina|compu\w*))\b", re.I)
RX_PIDE_NUBE = re.compile(r"\b(nube|cloud|higgs\w*|online)\b", re.I)


def preferencias(cfg: dict) -> str:
    """Ajustes › Media, en palabras para el system del modo media: con qué backend
    genera cada tipo y con qué job_type de Higgsfield.

    Se redacta como orden y no como sugerencia porque el 2026-08-07 perdió: Diego
    tenía video=nube y el video salió local (12 min de GPU), porque el prompt del
    modo media decía "si Diego no dijo cuál quiere, usá el local" y esa regla era
    más concreta que esta lista. La contradicción se sacó de media.md; acá queda
    dicho quién manda.
    """
    lineas = []
    for tipo in ("imagen", "video", "audio"):
        elegido = str(cfg.get(f"media_{tipo}") or "")
        linea = _BACKEND.get(elegido, _BACKEND["preguntar"]).format(
            tool=_LOCAL.get(tipo, "no hay modelo local para esto"))
        if elegido == "higgsfield":
            # sin job_type por defecto NO se le pide al modelo que elija (era llamar a
            # modelos_nube, leer 27 filas y acertar): lo elige Diego en el diálogo
            linea += (f" con job_type `{jt}`" if (jt := str(cfg.get(f"higgs_{tipo}") or ""))
                      else " — el job_type lo elige Diego en el diálogo de la app, no lo busques vos")
        lineas.append(f"- {tipo}: {linea}")
    return ("Backend que Diego eligió en Ajustes › Media para cada tipo. Lo eligió ÉL y MANDA: "
            "usá esa herramienta aunque otra parte de tus instrucciones prefiera la otra. Solo "
            "cambia si él pide el otro backend en esta conversación, o si dice preguntarle.\n"
            + "\n".join(lineas)
            # ya no depende de que el modelo obedezca (enrutar lo corrige igual), pero
            # decirlo evita el paso perdido de llamar a la tool equivocada primero
            + "\nSi llamás a la otra igual, la app la cambia sola por esta.")


def enrutar(cfg: dict, nombre: str, args: dict, pedido: str, forzar: str = "") -> tuple[str, dict]:
    """Con qué backend se genera de verdad: (tool, args).

    El backend por tipo lo elige Diego en Ajustes › Media y hasta hoy era SOLO una
    línea del system prompt: el modelo llamaba igual a la local (2026-08-07, tenía
    video=nube y salieron 12 min de GPU). Acá deja de ser una sugerencia. Si Diego
    dice en su mensaje con cuál quiere, manda él. `forzar` es lo que ya contestó en
    el diálogo de este turno ("no, hacelo en local"): gana sobre todo lo demás.

    ponytail: mira SOLO el mensaje de este turno. "usá local para todo hoy" no se
    recuerda tres turnos después; repetirlo —o cambiar el ajuste— es el arreglo.
    """
    tipo = _TIPO_TOOL.get(nombre) or _TIPO_NUBE.get(modelo_info(str(args.get("modelo") or ""))["tipo"], "")
    # Lo que Diego pide en su mensaje REEMPLAZA la preferencia, no apaga el
    # enrutado: antes se devolvía la tool tal cual y "reintentalo en higgsfield"
    # terminaba generando en local igual, porque el modelo había llamado a
    # crear_video y ya nadie lo corregía (2026-08-07).
    pref = (forzar or
            ("comfyui" if RX_PIDE_LOCAL.search(pedido) else
             "higgsfield" if RX_PIDE_NUBE.search(pedido) else
             str(cfg.get(f"media_{tipo}") or "") if tipo else ""))
    # lo único que sobrevive al cambio de backend: qué generar y a partir de qué
    base = {"prompt": args.get("prompt", ""), "referencia": args.get("referencia", "")}
    if pref == "higgsfield" and nombre in _TIPO_TOOL:
        # El job_type por defecto sale de Ajustes. Vacío NO se adivina —son modelos de
        # pago y el catálogo del CLI arranca por utilidades— pero tampoco se genera
        # gratis en local: va igual a la nube sin modelo y lo elige Diego en el MISMO
        # diálogo que ya autoriza el gasto. Antes se le devolvía un aviso al modelo
        # para que lo eligiera él: tres pasos de un modelo chico para algo que Diego
        # contesta en un toque (2026-08-07: pidió "en higgsfield" y salió por ComfyUI).
        return "crear_nube", {**base, "modelo": str(cfg.get(f"higgs_{tipo}") or ""), "tipo": tipo}
    if pref == "comfyui" and nombre == "crear_nube" and tipo in _LOCAL:
        return _LOCAL[tipo], base
    return nombre, args


def _ruta_base(s: str) -> str:
    """El modelo suele copiar la ruta del markdown previo (/attach/07_...): se
    normaliza a la ruta relativa de la base para que funcione como referencia."""
    return str(s or "").strip().lstrip("/").removeprefix("attach/")


def ejecutar(cfg: dict, nombre: str, args: dict, proyecto: str = "", sesion: str = "",
             cancelar=None) -> str:
    root = cfg["hamuq"]
    prompt = str(args.get("prompt") or "").strip()
    if nombre in ("crear_imagen", "crear_video"):
        referencia = _ruta_base(args.get("referencia"))
        wf = _workflow(root, cfg, nombre, str(args.get("workflow") or ""), bool(referencia))
        segundos = float(args.get("segundos") or 0)
        t0, inicio = time.monotonic(), datetime.now().isoformat(timespec="seconds")
        ruta, falta = generar_comfy(cfg, root, prompt, wf, referencia, args.get("semilla"),
                                    round(segundos * FPS) if segundos else 0, cancelar=cancelar)
        if not ruta:
            return f"(no se pudo generar: {falta})"
        return catalogar(cfg, root, ruta, prompt, backend="comfyui", proyecto=proyecto, sesion=sesion,
                         modelo=wf, inicio=inicio, segundos=time.monotonic() - t0)
    if nombre == "modelos_nube":
        return modelos_nube(str(args.get("tipo") or ""))
    if nombre == "arrancar_servicio":
        ok, msg = servicios.arrancar(cfg, str(args.get("servicio") or ""))
        return msg if ok else f"(no se pudo arrancar: {msg})"
    if nombre == "crear_nube":
        modelo = str(args.get("modelo") or "").strip()
        t0, inicio = time.monotonic(), datetime.now().isoformat(timespec="seconds")
        p = args.get("params")
        url, falta = generar_nube(root, modelo, prompt, _ruta_base(args.get("referencia")),
                                  p if isinstance(p, dict) else None)
        if not url:
            return f"(no se pudo generar en la nube: {falta})"
        ruta, falta = _traer(root, url, confiar=True)
        if not ruta:
            return f"(se generó pero no se pudo bajar: {falta}\nURL: {url})"
        return catalogar(cfg, root, ruta, prompt, backend=f"higgsfield/{modelo}", proyecto=proyecto,
                         sesion=sesion, modelo=modelo, inicio=inicio, segundos=time.monotonic() - t0)
    if nombre == "guardar_creacion":
        ruta, falta = _traer(root, str(args.get("origen") or ""))
        if not ruta:
            return f"(no se pudo guardar: {falta})"
        # generado en otro lado: el modelo lo dice la tool, el tiempo no lo sabe nadie
        return catalogar(cfg, root, ruta, prompt, backend=str(args.get("modelo") or "nube"),
                         nota=str(args.get("nota") or ""), proyecto=proyecto, sesion=sesion,
                         modelo=str(args.get("modelo") or ""))
    return f"(herramienta desconocida: {nombre})"


# ---------------------------------------------------------------- ComfyUI local

def catalogo(root: Path) -> list[dict]:
    """[{nombre, tipo, necesita_imagen}] de los workflows de la base.

    El tipo sale de QUÉ NODO guarda la salida, no del nombre del archivo: un
    workflow que termina en SaveVideo hace video se llame como se llame. Existe
    porque el desplegable de Ajustes fija el workflow por defecto de
    `crear_imagen`, y ofrecer ahí uno de video generaba un fallo ilegible.
    """
    d = root / WORKFLOWS
    salida = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            crudo = p.read_text(encoding="utf-8")
            clases = {n.get("class_type") for n in json.loads(crudo).values() if isinstance(n, dict)}
        except (OSError, ValueError, AttributeError):
            continue                        # JSON roto: no se ofrece, tampoco rompe la lista
        tipo = ("video" if clases & {"SaveVideo", "SaveAnimatedWEBP", "SaveAnimatedPNG", "CreateVideo"}
                else "audio" if clases & {"SaveAudio", "SaveAudioMP3"} else "imagen")
        salida.append({"nombre": p.stem, "tipo": tipo, "necesita_imagen": "%imagen%" in crudo})
    return salida


def _workflow(root: Path, cfg: dict, tool: str, pedido: str, con_imagen: bool) -> str:
    """Qué JSON corre. Con referencia el que la USA gana, aunque el modelo nombre
    otro: Diego pidió animar una imagen, el modelo mandó `workflow: "video"` junto
    con la referencia y el archivo se subía a ComfyUI para tirarlo —el video salía
    del prompt solo, con otro cohete (2026-08-07). Mismo criterio si el tipo no es
    el de la tool. Un nombre que no existe se respeta: el error de generar_comfy
    dice qué pasó, mejor que corregirlo en silencio."""
    tipo = "video" if tool == "crear_video" else "imagen"
    defecto = (("video-imagen" if con_imagen else "video") if tipo == "video"
               else ("editar" if con_imagen else str(cfg.get("comfy_workflow") or "imagen")))
    if not pedido:
        return defecto
    w = next((w for w in catalogo(root) if w["nombre"] == pedido), None)
    if w and (w["tipo"] != tipo or (con_imagen and not w["necesita_imagen"])):
        return defecto
    return pedido


def _sustituir(obj, marcas: dict):
    """Reemplaza %marcadores% en todos los strings del workflow. Si el string ES
    el marcador se devuelve el valor con su tipo (una semilla debe quedar int)."""
    if isinstance(obj, dict):
        return {k: _sustituir(v, marcas) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sustituir(v, marcas) for v in obj]
    if isinstance(obj, str):
        if obj in marcas:
            return marcas[obj]
        for k, v in marcas.items():
            obj = obj.replace(k, str(v))
        return obj
    return obj


def _salida_de(hist: dict) -> tuple[str, str, str]:
    """(filename, subfolder, type) del primer medio en los outputs de /history."""
    for nodo in (hist.get("outputs") or {}).values():
        for clave in ("images", "gifs", "videos", "audio"):
            for f in nodo.get(clave) or []:
                if f.get("filename"):
                    return f["filename"], f.get("subfolder", ""), f.get("type", "output")
    return "", "", ""


def generar_comfy(cfg: dict, root: Path, prompt: str, workflow: str = "imagen",
                  referencia: str = "", semilla=None, frames: int = 0,
                  cancelar=None) -> tuple[str, str]:
    """Texto → medio vía ComfyUI. Devuelve (ruta_relativa, motivo_de_falla).

    `cancelar` (threading.Event) es el botón de detener del chat: acá es donde el
    turno pasa el 99% de su tiempo, así que mirarlo en cada vuelta del polling es
    lo que hace que cancelar se sienta inmediato (≤PASO segundos)."""
    servicios.marcar_uso()   # una imagen puede tardar menos que una ronda del vigilante
    base = str(cfg.get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")
    wf_path = root / WORKFLOWS / f"{workflow}.json"
    if not wf_path.is_file():
        return "", f"no existe el workflow {WORKFLOWS}/{workflow}.json (exportarlo desde ComfyUI en formato API)"
    marcas = {"%prompt%": prompt, "%semilla%": int(semilla or random.randrange(2**31)),
              "%frames%": max(5, int(frames or FRAMES))}
    crudo = wf_path.read_text(encoding="utf-8")
    # Un workflow con %imagen% (editar, video-imagen) NECESITA una imagen de
    # partida. Sin ella el marcador viajaba sin sustituir y ComfyUI devolvía
    # "Invalid image file: %imagen%", que no le dice nada a nadie — y al modelo
    # lo llevaba a inventar que el prompt estaba mal y a pedir otro.
    if "%imagen%" in crudo and not referencia:
        return "", (f"el workflow '{workflow}' parte de una imagen y no se le pasó ninguna: "
                    f"usa `referencia` (ruta de una imagen de la base) o un workflow que "
                    f"genere desde cero")
    plantilla = json.loads(crudo)
    # cuánto puede tardar lo declara el workflow, no este módulo: una imagen es un
    # minuto y un video de MiniMax H3 con offload a RAM son 20-40.
    limite = float(plantilla.pop("_timeout", TIMEOUT))
    cancelado = lambda: cancelar is not None and cancelar.is_set()   # noqa: E731
    if cancelado():
        return "", "cancelado por Diego"
    try:
        with httpx.Client(base_url=base, timeout=60) as c:
            if referencia:
                p = root / referencia
                if not p.is_file():
                    return "", f"la referencia {referencia} no está en la base"
                r = c.post("/upload/image", files={"image": (p.name, p.read_bytes())})
                r.raise_for_status()
                marcas["%imagen%"] = r.json()["name"]
            wf = _sustituir(plantilla, marcas)
            r = c.post("/prompt", json={"prompt": wf, "client_id": "mem"})
            if r.status_code >= 400:
                return "", f"ComfyUI rechazó el workflow: {r.text[:300]}"
            pid = r.json()["prompt_id"]
            fin = time.monotonic() + limite
            while time.monotonic() < fin:
                if cancelado():
                    # el /interrupt ya lo mandó el endpoint de cancelar: acá solo
                    # se deja de esperar, sin escribir nada en la base
                    return "", "cancelado por Diego"
                hist = c.get(f"/history/{pid}").json().get(pid)
                if hist:
                    nombre, sub, tipo = _salida_de(hist)
                    if nombre:
                        datos = c.get("/view", params={"filename": nombre, "subfolder": sub,
                                                       "type": tipo}).content
                        return _guardar(root, datos, Path(nombre).suffix or ".png", prompt), ""
                    est = hist.get("status") or {}
                    if est.get("status_str") == "error":
                        return "", f"el workflow falló en ComfyUI: {str(est.get('messages'))[:300]}"
                time.sleep(PASO)
    except httpx.ConnectError:
        return "", (f"ComfyUI no está corriendo en {base}. Pregúntale a Diego si quiere que "
                    "lo arranques; con su permiso, llama a arrancar_servicio('comfyui') y "
                    "vuelve a intentar la generación")
    except httpx.HTTPError as e:
        return "", f"ComfyUI falló en {base} ({type(e).__name__})"
    return "", f"ComfyUI no terminó en {limite // 60:.0f} min"


# ------------------------------------------------------- Higgsfield (nube)
# Vía el CLI oficial (npm i -g @higgsfield/cli), logueado con la cuenta personal
# de Diego (`higgsfield auth login`): mismos créditos que su plan, sin API keys.

RX_URL = re.compile(r"https://[^\s\"'\\]+")
RX_MEDIO = re.compile(r"\.(png|jpe?g|webp|gif|mp4|webm|mov|mp3|wav|ogg|m4a|glb)$", re.I)
TIMEOUT_NUBE = 1500     # ponytail: --wait-timeout 20m del CLI + margen


def _cli() -> str:
    return shutil.which("higgsfield") or ""


RX_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _workspaces() -> list[str]:
    """IDs de los workspaces de la cuenta. Una cuenta personal tiene uno solo
    ('Private'), y aun así el CLI arranca sin elegirlo."""
    salida, _ = _correr_cli("workspace", "list", elegir_ws=False)
    ids = [l.split()[0] for l in salida.splitlines() if l.split()]
    return [i for i in ids if RX_UUID.fullmatch(i)]


def _correr_cli(*args, timeout: float = 120, elegir_ws: bool = True) -> tuple[str, str]:
    """(salida, motivo_de_falla) de un comando del CLI de Higgsfield."""
    exe = _cli()
    if not exe:
        return "", "el CLI de Higgsfield no está instalado (npm i -g @higgsfield/cli)"
    try:
        r = subprocess.run([exe, *args, "--no-color"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", f"Higgsfield no respondió en {timeout:.0f}s"
    salida = f"{r.stdout or ''}\n{r.stderr or ''}".strip()
    if r.returncode != 0:
        if "Not authenticated" in salida:
            return "", "falta iniciar sesión: correr `higgsfield auth login` en una terminal"
        # "No workspace selected": el CLI elige el workspace de facturación y
        # arranca sin ninguno, así que la primera generación de una máquina nueva
        # muere sí o sí (le pasó a Diego el 2026-08-07 con un video ya pagado en
        # tiempo). Si hay uno solo —la cuenta personal— se elige y se reintenta.
        if elegir_ws and "No workspace selected" in salida:
            if len(ws := _workspaces()) != 1:
                return "", ("Higgsfield no tiene workspace elegido y hay "
                            f"{len(ws)} para elegir: correr `higgsfield workspace list` y "
                            "`higgsfield workspace set <id>` en una terminal")
            subprocess.run([exe, "workspace", "set", ws[0], "--no-color"],
                           capture_output=True, timeout=60)
            return _correr_cli(*args, timeout=timeout, elegir_ws=False)
        return "", f"el CLI falló: {salida[-300:]}"
    return salida, ""


@lru_cache(maxsize=64)
def modelo_info(modelo: str) -> dict:
    """Lo que el propio CLI sabe de un job_type: {"tipo": image|video|audio,
    "params": [{name, type, default, required, enum}]}. Cacheado: cada consulta es
    un subprocess de ~1 s y el catálogo no cambia mientras corre el server.
    Vacío si el CLI no está, no hay sesión o el job_type no existe — todo lo que
    lee esto trata "no sé" como "no toco nada"."""
    salida, _ = _correr_cli("model", "get", modelo, "--json", timeout=60)
    try:
        d = json.loads(salida[salida.index("{"):salida.rindex("}") + 1])
    except ValueError:
        return {"tipo": "", "params": []}
    return {"tipo": str(d.get("type") or ""), "params": list(d.get("params") or [])}


def _flag_referencia(modelo: str) -> str:
    """Con qué flag entra el material en ESTE job_type.

    `--start-image` = la imagen ES el primer fotograma: se anima ESA imagen.
    `--image-references` = referencia de estilo/sujeto: el modelo dibuja algo
    parecido, que es exactamente el bug que reportó Diego (2026-08-07) — pidió
    animar su cohete y salió otro cohete. Los dos flags existen y hasta hoy se
    mandaba siempre el segundo.
    Lo decide el propio CLI, así que un job_type nuevo no se agrega acá; los
    modelos de imagen no tienen start_image y caen solos en references, que para
    editar es lo correcto."""
    return ("--start-image" if any(p.get("name") == "start_image" for p in modelo_info(modelo)["params"])
            else "--image-references")


# El prompt lo escribe el modelo y el material entra por `referencia` (flags aparte
# del CLI): de estos no se pregunta nada.
PARAMS_MATERIAL = ("prompt", "start_image", "end_image", "image_references",
                   "video_references", "audio_references", "custom_reference_id",
                   # no son una decisión creativa; batch_size además MULTIPLICA lo que
                   # cuesta la generación, así que se queda en su default (1)
                   "batch_size", "folder_id", "seed", "webhook_url")


def _opciones(params: list[dict], ya: dict) -> list[dict]:
    """Lo que se puede preguntar de este job_type: listas cerradas (aspect_ratio,
    quality) y NÚMEROS con default (la duración de minimax_h3 no es enum, y era
    justo lo que Diego no podía elegir — 2026-08-07). `opciones` vacío = número."""
    out = []
    for p in params:
        if p["name"] in PARAMS_MATERIAL or p["name"] in ya:
            continue
        if p.get("enum"):
            out.append({"nombre": p["name"], "opciones": [str(o) for o in p["enum"]],
                        "valor": str(p["default"] if p.get("default") is not None else p["enum"][0])})
        elif p.get("type") in ("integer", "number") and p.get("default") is not None:
            out.append({"nombre": p["name"], "opciones": [], "valor": str(p["default"])})
    return out


def opciones_nube(modelo: str, ya: dict | None = None) -> list[dict]:
    """Lo que ESTE job_type deja elegir y todavía nadie fijó: [{nombre, opciones,
    valor}]. Es lo que el diálogo de la app pregunta antes de gastar créditos
    (pedido 2026-08-07: resolución, duración, fps). Solo los de lista cerrada —
    un campo libre no se puede ofrecer sin inventarle validación, y los que
    importan (aspect_ratio, duration, quality, resolution) son enums."""
    return _opciones(modelo_info(modelo)["params"], ya or {})


def modelos_nube(tipo: str = "") -> str:
    filtro = [f"--{tipo}"] if tipo in ("image", "video", "audio") else []
    salida, falta = _correr_cli("model", "list", *filtro)
    return salida[:6000] if salida else f"(no se pudo listar: {falta})"


@lru_cache(maxsize=8)
def modelos_lista(tipo: str = "") -> list[dict]:
    """El catálogo como datos: [{job_type, nombre}]. Es lo que llena el desplegable
    de Ajustes › Media — sin él, el job_type por defecto había que escribirlo a mano
    y quedaba vacío, y con la nube elegida y sin job_type cada generación necesitaba
    tres pasos del modelo antes de arrancar (pedido 2026-08-07).
    La tabla del CLI: `JOB_TYPE  NOMBRE...  TIPO`, cabecera incluida."""
    salida, _ = _correr_cli("model", "list", *([f"--{tipo}"] if tipo in ("image", "video", "audio") else []))
    out = []
    for linea in salida.splitlines():
        partes = linea.split()
        # la cabecera dice "JOB TYPE": no es un job_type, y ningún job_type real
        # tiene mayúsculas ni espacios
        if len(partes) < 2 or partes[-1] not in ("image", "video", "audio") or not partes[0].islower():
            continue
        out.append({"job_type": partes[0], "nombre": " ".join(partes[1:-1]) or partes[0],
                    "tipo": partes[-1]})
    return out


def generar_nube(root: Path, modelo: str, prompt: str, referencia: str = "",
                 params: dict | None = None) -> tuple[str, str]:
    """Genera con la cuenta de Diego y devuelve (url_del_resultado, motivo).
    `params` = lo que se eligió en el diálogo (duración, resolución…): el CLI los
    toma como `--nombre valor`. Se filtran contra los del job_type — el nombre lo
    propone el modelo y de acá sale una línea de comandos."""
    if not modelo:
        return "", "falta el modelo (job_type); consultar modelos_nube"
    args = ["generate", "create", modelo, "--prompt", prompt,
            "--wait", "--wait-timeout", "20m"]
    validos = {p.get("name") for p in modelo_info(modelo)["params"]}
    for k, v in (params or {}).items():
        if k in validos and str(v).strip():
            args += [f"--{k}", str(v)]
    if referencia:
        p = root / referencia
        if not p.is_file():
            return "", f"la referencia {referencia} no está en la base"
        args += [_flag_referencia(modelo), str(p)]
    salida, falta = _correr_cli(*args, timeout=TIMEOUT_NUBE)
    if not salida:
        return "", falta
    urls = [u.rstrip(".,)]}\"'") for u in RX_URL.findall(salida)]
    medios = [u for u in urls if RX_MEDIO.search(u.split("?")[0])] or urls
    if not medios:
        return "", f"no devolvió URL de resultado: {salida[-300:]}"
    return medios[-1], ""       # la última: el CLI imprime el resultado al final


# --------------------------------------------------------- traer y catalogar

def _guardar(root: Path, datos: bytes, ext: str, prompt: str) -> str:
    rel = f"{ADJUNTOS}/{datetime.now():%Y-%m-%d_%H%M%S}_crear-{memoria.slugificar(prompt[:40]) or 'medio'}{ext}"
    destino = root / rel
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(datos)
    return rel


def _host_permitido(url: str) -> bool:
    host = url.split("//", 1)[-1].split("/", 1)[0].split("@")[-1].lower()
    return any(host == h or host.endswith("." + h) for h in HOSTS)


def _traer(root: Path, origen: str, confiar: bool = False) -> tuple[str, str]:
    """URL o ruta local → ruta relativa dentro de adjuntos. `confiar` salta el
    filtro de hosts: solo para URLs que imprimió el CLI autenticado, nunca para
    las que propone el modelo."""
    if origen.startswith("https://"):
        if not confiar and not _host_permitido(origen):
            return "", f"host no permitido para descargas: {origen.split('/')[2]}"
        try:
            r = httpx.get(origen, timeout=120, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as e:
            return "", f"no se pudo bajar {origen[:80]} ({type(e).__name__})"
        ext = (EXT_POR_TIPO.get(r.headers.get("content-type", "").split(";")[0].strip())
               or Path(origen.split("?")[0]).suffix or ".bin")
        return _guardar(root, r.content, ext, Path(origen.split("?")[0]).stem), ""
    if origen.startswith("http://"):
        return "", "solo se bajan URLs https"
    origen = _ruta_base(origen)   # acepta la forma /attach/... que imprime el markdown
    p = root / origen
    if not p.is_file():
        return "", f"{origen}: no es una URL ni un archivo de la base"
    if origen.replace("\\", "/").startswith(ADJUNTOS):
        return origen, ""                     # ya está donde va (lo dejó crear_imagen)
    return _guardar(root, p.read_bytes(), p.suffix, p.stem), ""


# Con qué se hizo, en una línea, para que viaje pegada al medio (pedido
# 2026-08-07: "un texto abajo diciendo con qué modelo y en qué servicio").
SERVICIO = {"comfyui": "ComfyUI (local)", "higgsfield": "Higgsfield (nube)"}


def ficha_corta(backend: str, modelo: str) -> str:
    servicio = str(backend or "").split("/")[0]
    return " · ".join(x for x in (modelo, SERVICIO.get(servicio, servicio)) if x)


def _tipo(ext: str) -> str:
    ext = ext.lower()
    return ("Imagen" if ext in media.IMAGENES else
            "Video" if ext in media.VIDEO else
            "Audio" if ext in media.AUDIO else "Medio")


def catalogar(cfg: dict, root: Path, ruta: str, prompt: str, backend: str = "",
              nota: str = "", proyecto: str = "", sesion: str = "", modelo: str = "",
              inicio: str = "", segundos: float = 0.0) -> str:
    """Un medio generado → entrada de la Biblioteca bajo Creaciones/, con el
    prompt y la descripción por visión en el cuerpo (la parte buscable).
    `sesion` = dónde nació: queda en el frontmatter (chip en la app) y como
    línea del cuerpo (legible para cualquier agente sin la app).
    `modelo`/`inicio`/`segundos` = la ficha técnica de la generación (con qué,
    cuándo arrancó, cuánto tardó): frontmatter para poder buscarla y línea del
    cuerpo para poder leerla. Se graba SIEMPRE — pedido 2026-08-06, sin esto una
    creación no decía ni con qué workflow salió ni cuánto costó de tiempo."""
    tipo = _tipo(Path(ruta).suffix)
    descripcion, faltante, _ = media.extraer(agentes.para(cfg, "crear"), root, ruta)
    texto_ev = f"Creación generada por IA ({tipo.lower()}).\nPrompt: {prompt}\n{nota}\n\n{descripcion}"
    ev = triaje.evaluar(cfg, texto_ev)
    titulo = ev.get("titulo") or f"{tipo} generada: {prompt[:48]}"
    subjects = list(dict.fromkeys([f"Creaciones/{tipo}", *ev.get("subjects", [])]))
    if proyecto:
        subjects = list(dict.fromkeys([*subjects, memoria.subject_proyecto(proyecto)]))
    con = f"{backend or 'IA'}" + (f" ({modelo})" if modelo else "")
    resumen = f"{tipo} generada con {con}. Prompt: {prompt}" + (f"\n{nota}" if nota else "")
    ficha = " · ".join(x for x in (f"Modelo: {modelo}" if modelo else "",
                                   f"Backend: {backend}" if backend else "",
                                   f"Inicio: {inicio}" if inicio else "",
                                   f"Duración: {segundos:.0f} s" if segundos else "") if x)
    cuerpo = "\n\n".join(x for x in (f"Prompt: {prompt}", ficha, descripcion,
                                     f"Sesión de origen: 10_Sesiones/{sesion}.md" if sesion else "") if x)
    # slug = nombre del archivo generado (ya único: lleva fecha y hora), no del
    # título: dos generaciones con títulos parecidos ("cohete...", "cohete...
    # editado") slugificaban igual y la segunda se fusionaba con la primera —
    # guardar_entrada() no pisa `adjunto` al actualizar, así que la imagen
    # editada quedaba catalogada pero invisible (buscar_memorias seguía
    # apuntando a la vieja). Un slug por archivo = una entrada por generación.
    resultado = memoria.guardar_entrada(
        root, titulo, resumen, subjects, origen="crear", tags=ev.get("tags"),
        adjunto=ruta, transcripcion=cuerpo, slug=Path(ruta).stem,
        meta={"generado": True, "backend": backend, "prompt_generacion": prompt, "sesion": sesion,
              "modelo": modelo, "generado_inicio": inicio, "generado_segundos": round(segundos, 1)})
    aviso = f"\n(no se pudo describir el contenido: {faltante})" if faltante else ""
    # el título del markdown (lo de después de la ruta, entre comillas) es la ficha:
    # el chat la pinta debajo del medio y viaja copiada aunque el modelo solo
    # repita el ![...] — no hace falta que se acuerde de escribirla
    return f'![{titulo}](/attach/{ruta} "{ficha_corta(backend, modelo)}")\n{resultado}{aviso}'


# ------------------------------------------------------------------- autocheck

def demo():
    """Sin red ni GPU: lo frágil es la sustitución, el parseo de /history y el
    filtro de orígenes."""
    wf = {"3": {"inputs": {"text": "%prompt%", "seed": "%semilla%", "extra": "x %prompt% y"}}}
    s = _sustituir(wf, {"%prompt%": "un gato", "%semilla%": 7})
    assert s["3"]["inputs"] == {"text": "un gato", "seed": 7, "extra": "x un gato y"}, s

    # el tipo sale del nodo de salida, no del nombre: es lo que evita que el
    # desplegable de Ajustes ofrezca un workflow de video como default de imagen
    wf_video = {"9": {"class_type": "SaveVideo", "inputs": {}},
                "0": {"class_type": "LoadImage", "inputs": {"image": "%imagen%"}}}
    wf_img = {"7": {"class_type": "SaveImage", "inputs": {}}}
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / WORKFLOWS
        d.mkdir(parents=True)
        (d / "v.json").write_text(json.dumps(wf_video), encoding="utf-8")
        (d / "i.json").write_text(json.dumps(wf_img), encoding="utf-8")
        (d / "roto.json").write_text("{no es json", encoding="utf-8")
        cat = {w["nombre"]: w for w in catalogo(Path(tmp))}
        assert cat["v.json".removesuffix(".json")]["tipo"] == "video", cat
        assert cat["v"]["necesita_imagen"] and not cat["i"]["necesita_imagen"]
        assert cat["i"]["tipo"] == "imagen"
        assert "roto" not in cat, "un JSON roto no puede tumbar la lista"

    # los workflows de la base tienen que ser JSON válido, y el "_timeout" que
    # declara un video no puede llegar a ComfyUI: lo tomaría por un nodo más
    from . import config
    base = Path(config.cargar()["hamuq"])
    d = base / WORKFLOWS
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        g = json.loads(p.read_text(encoding="utf-8"))
        limite = float(g.pop("_timeout", TIMEOUT))
        assert all("class_type" in n and "inputs" in n for n in g.values()), p.name
        assert limite >= TIMEOUT and "_timeout" not in g, p.name
        if p.stem.startswith("video"):
            assert limite >= 1800, f"{p.name}: un video no entra en {limite / 60:.0f} min"
            assert json.dumps(g).count("%frames%") == 1, p.name
        if p.stem == "video-imagen":
            # el lienzo lo dicta la imagen de partida (ImageScaleToTotalPixels +
            # GetImageSize), no un 832x480 fijo: el nodo ESTIRA el first_frame al
            # lienzo ("plain stretch to canvas"), así que la imagen cuadrada del
            # cohete salía aplastada a 16:9
            n = next(x for x in g.values() if x["class_type"] == "MiniMaxH3ImageToVideo")
            assert all(isinstance(n["inputs"][k], list) for k in ("width", "height", "first_frame")), p.name

    # el workflow que nombra el modelo no puede contradecir el pedido: con imagen de
    # partida gana el que la usa (2026-08-07: `workflow: "video"` + referencia = la
    # imagen se subía y se tiraba). Corre contra los workflows reales de la base.
    c = {"comfy_workflow": "imagen"}
    assert _workflow(base, c, "crear_video", "video", True) == "video-imagen"
    assert _workflow(base, c, "crear_video", "", True) == "video-imagen"
    assert _workflow(base, c, "crear_video", "", False) == "video"
    assert _workflow(base, c, "crear_imagen", "video", False) == "imagen"   # tipos cruzados
    assert _workflow(base, c, "crear_imagen", "", True) == "editar"
    # inventado: se respeta y generar_comfy dice que no existe, en vez de correr otro
    assert _workflow(base, c, "crear_imagen", "no-existe", False) == "no-existe"

    # Ajustes › Media manda cuando Diego no dijo con cuál generar (pedido
    # 2026-08-07): antes era solo una línea del prompt y el modelo llamaba igual a
    # la local, con el ajuste en nube.
    nube = {"media_imagen": "higgsfield", "higgs_imagen": "nano_banana_pro"}
    assert enrutar(nube, "crear_imagen", {"prompt": "x"}, "hazme una imagen") == (
        "crear_nube", {"prompt": "x", "referencia": "", "modelo": "nano_banana_pro", "tipo": "imagen"})
    # lo que Diego pide en SU mensaje gana sobre el ajuste — en los dos sentidos, y
    # sin dejar de enrutar: "reintentalo en higgsfield" + el modelo llamando a la
    # local tiene que TERMINAR en la nube, no volver a generar gratis (2026-08-07)
    assert enrutar(nube, "crear_imagen", {"prompt": "x"}, "hacela en local")[0] == "crear_imagen"
    assert enrutar({"media_imagen": "comfyui"}, "crear_imagen", {"prompt": "x"}, "")[0] == "crear_imagen"
    local_con_job = {"media_imagen": "comfyui", "higgs_imagen": "nano_banana_pro"}
    assert enrutar(local_con_job, "crear_imagen", {"prompt": "x"}, "reintentalo en higgsfield")[0] == "crear_nube"
    # nube elegida y sin job_type: va igual a la nube con el modelo sin elegir (lo
    # elige Diego en el diálogo). Generar gratis en local NO es la salida: era el
    # bug del 2026-08-07 — "generá un video en higgsfield" y salió por ComfyUI.
    n, a = enrutar({"media_imagen": "higgsfield"}, "crear_imagen", {"prompt": "x"}, "")
    assert (n, a["modelo"], a["tipo"]) == ("crear_nube", "", "imagen"), (n, a)
    assert enrutar({"media_imagen": "comfyui"}, "crear_imagen", {"prompt": "x"}, "hacelo en la nube")[0] == "crear_nube"
    # y lo que Diego ya contestó en el diálogo de ESTE turno gana sobre el ajuste:
    # sin esto, "no, hacelo en local" volvía a la nube y el turno hacía ping-pong
    assert enrutar(nube, "crear_imagen", {"prompt": "x"}, "", forzar="comfyui")[0] == "crear_imagen"
    assert enrutar({}, "crear_imagen", {"prompt": "x"}, "") == ("crear_imagen", {"prompt": "x"})

    # lo que el diálogo pregunta antes de gastar: los de lista cerrada que nadie fijó
    ps = [{"name": "prompt", "type": "string"},
          {"name": "aspect_ratio", "default": "16:9", "enum": ["16:9", "9:16"]},
          {"name": "duration", "default": 8, "enum": ["4", "6", "8"]},
          {"name": "start_image", "type": "object|null"},          # material: entra por referencia
          {"name": "custom_reference_id", "type": "string|null"}]   # libre: no se ofrece
    assert [o["nombre"] for o in _opciones(ps, {})] == ["aspect_ratio", "duration"]
    ops = _opciones(ps, {"duration": 6})     # lo que Diego ya pidió no se vuelve a preguntar
    assert [o["nombre"] for o in ops] == ["aspect_ratio"] and ops[0]["valor"] == "16:9"
    assert _opciones([{"name": "quality", "enum": ["1.5k", "2k"]}], {})[0]["valor"] == "1.5k"  # sin default
    # un número con default también se pregunta (la duración de minimax_h3 no es
    # enum: sin esto no había forma de pedir un clip más largo). batch_size no:
    # multiplica el gasto y su default es el correcto.
    num = _opciones([{"name": "duration", "type": "integer", "default": 5},
                     {"name": "batch_size", "type": "integer", "default": 1}], {})
    assert num == [{"nombre": "duration", "opciones": [], "valor": "5"}], num

    hist = {"outputs": {"9": {"images": [{"filename": "f.png", "subfolder": "s", "type": "output"}]}},
            "status": {"status_str": "success"}}
    assert _salida_de(hist) == ("f.png", "s", "output")
    assert _salida_de({"outputs": {"9": {"text": ["hola"]}}}) == ("", "", "")

    assert _host_permitido("https://cdn.higgsfield.ai/x.mp4")
    assert _host_permitido("https://algo.s3.amazonaws.com/y.png")
    assert not _host_permitido("https://malicioso.com/x.png")
    assert not _host_permitido("https://falso-higgsfield.ai.evil.com/x.png")
    assert _traer(Path("."), "http://inseguro.com/x")[1] == "solo se bajan URLs https"
    assert "no es una URL" in _traer(Path("."), "no-existe.png")[1]
    assert _ruta_base("/attach/07_Inbox/_adjuntos/x.png") == "07_Inbox/_adjuntos/x.png"
    assert _ruta_base("07_Inbox/_adjuntos/x.png") == "07_Inbox/_adjuntos/x.png"

    # la ficha que va debajo del medio en el chat: con qué modelo, en qué servicio
    assert ficha_corta("comfyui", "video-imagen") == "video-imagen · ComfyUI (local)"
    assert ficha_corta("higgsfield/minimax_h3", "minimax_h3") == "minimax_h3 · Higgsfield (nube)"
    assert ficha_corta("", "") == ""          # sin datos no se inventa una línea vacía

    assert _tipo(".png") == "Imagen" and _tipo(".mp4") == "Video" and _tipo(".mp3") == "Audio"
    assert EXT_POR_TIPO["image/png"] == ".png"

    p = preferencias({"media_imagen": "comfyui", "media_video": "higgsfield",
                      "media_audio": "rarísimo", "higgs_video": "seedance-pro"})
    assert "imagen: local (ComfyUI" in p and "job_type `seedance-pro`" in p
    assert "audio: preguntarle" in p    # valor desconocido cae a preguntar, no explota
    assert "MANDA" in p                 # es una orden, no una sugerencia: perdía contra media.md
    # nube elegida y sin job_type: el modelo NO tiene que salir a buscarlo (lo elige
    # Diego en el diálogo); si se lo pedimos a él, se pierde por el camino
    assert "lo elige Diego" in preferencias({"media_video": "higgsfield"})

    # la tabla de `workspace list`: el ID es la primera columna, la cabecera no cuenta
    tabla = ("ID                                    NAME     PLAN   CREDITS  SELECTED\n"
             "60e78f75-1759-474a-b214-bccb97be961f  Private  basic  301")
    ids = [i for i in (l.split()[0] for l in tabla.splitlines() if l.split()) if RX_UUID.fullmatch(i)]
    assert ids == ["60e78f75-1759-474a-b214-bccb97be961f"], ids

    # parseo de la salida del CLI de Higgsfield: la URL del medio, no la del docs
    salida = ('{"id":"j1","status":"completed"}\nDocs: https://docs.higgsfield.ai/x\n'
              'Result: https://cdn.higgsfield.ai/out/final.mp4?sig=abc')
    urls = [u.rstrip(".,)]}\"'") for u in RX_URL.findall(salida)]
    medios = [u for u in urls if RX_MEDIO.search(u.split("?")[0])] or urls
    assert medios[-1].startswith("https://cdn.higgsfield.ai/out/final.mp4"), medios
    print("crear ok")


if __name__ == "__main__":
    demo()
