"""API HTTP — capa fina sobre el motor. Un solo usuario, token Bearer opcional.

Cubre las cuatro pantallas de la spec: Input (capture/inbox), Think (sessions),
Memory (memory/*) y Settings (health, provider/test).
"""
import json
import queue
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (agentes, chat, compartir, config, crear, exportar, fallas, indice, lint, llm,
               mcp, memoria, modos, procesar, recursos, servicios, sesiones, sintesis, triaje)

cfg = config.cargar()


@asynccontextmanager
async def lifespan(_app):
    # Solo cuando el server corre de verdad: importar mem.api (tests, CLI) no
    # tiene por qué levantar hilos que sondean servicios locales.
    servicios.vigilar_ocioso(cfg)
    # lo privado pasó a ser un campo de la memoria; las que ya existían lo
    # heredaban del proyecto y se quedarían sin marca. Idempotente y barata.
    try:
        memoria.migrar_privadas(cfg["hamuq"])
    except Exception as e:                          # noqa: BLE001
        memoria.log_evento(cfg["hamuq"], "privado", f"migración no corrió: {e}")
    yield


app = FastAPI(title="MeM", lifespan=lifespan)
ESTATICO = Path(__file__).parent / "static"


@app.middleware("http")
async def no_cache(request: Request, call_next):
    # Sin esto, StaticFiles no manda Cache-Control y el navegador cachea JS/CSS
    # por heurística (basada en Last-Modified) sin siquiera revalidar contra el
    # server — un reload normal no alcanza para ver una edición nueva. no-cache
    # (no no-store) sigue permitiendo 304 rápido cuando el archivo no cambió.
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def auth(authorization: str | None = Header(default=None)):
    if cfg.get("token") and authorization != f"Bearer {cfg['token']}":
        raise HTTPException(401, "token inválido")


def parado_en(x_proyecto: str | None = Header(default=None)) -> str | None:
    """Desde qué proyecto se hace esta consulta (pedido 2026-08-12). Lo manda la
    app en TODAS sus llamadas (api.js lo pone en las cabeceras, así que ninguna
    pantalla tiene que acordarse) y decide una sola cosa: lo privado de otro
    proyecto no se ve. Cabecera y no query param porque no es un filtro de la
    consulta, es el contexto de quien pregunta — y así vale igual para las 8
    lecturas que lo necesitan.

    Ausente = no está parado en ninguno (MCP, curl, un shell viejo cacheado):
    memoria.accesible no filtra. `X-Proyecto: -` es "Sin proyecto", que SÍ es un
    proyecto: una cabecera vacía no viaja distinto de una ausente."""
    if x_proyecto is None:
        return None
    return "" if x_proyecto.strip() == "-" else x_proyecto.strip()


class SesionIn(BaseModel):
    subjects: list[str] = []
    titulo: str = "sesion"
    modo: str = "chat"
    proyecto: str = ""            # a qué proyecto pertenece lo que se hable acá
    temporal: bool = False        # chat del Main: la promueve el primer turno


class SesionPatch(BaseModel):
    titulo: str | None = None
    modo: str | None = None       # cambiar la vista no pierde nada (spec §5.2)
    subjects: list[str] | None = None
    proyecto: str | None = None   # "" = sacarla de todo proyecto


class MensajeIn(BaseModel):
    texto: str
    adjunto: str = ""             # una sola ruta: lo que manda un shell viejo cacheado
    adjuntos: list[str] = []      # paths de /attach: se leen y viajan con el turno
    agente: str = ""              # forzar agente para ESTE turno (reintento con otro)


class ConfirmIn(BaseModel):
    ok: bool = False              # respuesta al diálogo de una tool que gasta créditos
    params: dict[str, str] = {}   # lo que se eligió ahí mismo (duración, resolución…)
    local: bool = False           # "no, hacelo en local": un no que además dice qué hacer
    modelo: str = ""              # job_type elegido ahí mismo, si Ajustes no fija uno


class CancelIn(BaseModel):
    comfy: bool = False           # además del turno, cortar la generación en curso


class CapturaIn(BaseModel):
    contenido: str
    tipo: str = "nota"            # nota | link | tarea | audio | imagen | video
    contexto: str = ""
    tags: list[str] = []
    subjects: list[str] = []
    adjunto: str = ""
    origen: str = "movil"
    coords: str = ""              # "lat,lon" del browser al capturar; "" si no hay permiso
    proyecto: str = ""            # dónde estaba parado: la memoria nace ahí dentro


class CarpetaIn(BaseModel):
    carpeta: str                  # ruta absoluta de la máquina: destino del backup u origen


class InboxPatch(BaseModel):
    texto: str | None = None
    tags: list[str] | None = None
    subjects: list[str] | None = None


class EntradaPatch(BaseModel):
    titulo: str | None = None
    tags: list[str] | None = None
    subjects: list[str] | None = None
    nota: str = ""
    resumen: str | None = None
    privada: bool | None = None    # la casilla de la ficha (None = no se tocó)


class EntradaIn(BaseModel):
    titulo: str
    contenido: str
    subjects: list[str] = []
    tags: list[str] = []


class ConfigPatch(BaseModel):
    proveedor: str | None = None
    modelo: str | None = None
    base_url: str | None = None
    hamuq: str | None = None
    comfy_url: str | None = None
    comfy_cmd: str | None = None
    comfy_workflow: str | None = None
    media_imagen: str | None = None   # backend preferido por tipo: comfyui | higgsfield | preguntar
    media_video: str | None = None
    media_audio: str | None = None
    higgs_imagen: str | None = None   # job_type de Higgsfield por defecto
    higgs_video: str | None = None
    higgs_audio: str | None = None
    idle_minutos: int | None = None   # minutos sin uso antes de soltar la VRAM (0 = nunca)
    # hasta 3 {nombre, palette, themePref, burbuja, radio, borde, fuente} — CatLook
    # siempre manda la lista completa (reemplaza, no mergea por índice)
    estilos_guardados: list[dict] | None = None


class AgentesIn(BaseModel):
    agentes: list[dict]
    asignaciones: dict[str, str] = {}


class TriajeIn(BaseModel):
    mensajes: list[dict]          # [{rol: "diego"|"agente", texto}]
    lang: str = "es"
    adjunto: str = ""
    proyecto: str = ""            # proyecto activo del chatbox


class ProyectoIn(BaseModel):
    nombre: str
    ambito: str = "personal"      # personal | trabajo | privado


class ProyectoRen(BaseModel):
    nombre: str                   # el nombre nuevo
    fusionar: bool = False        # ese nombre ya existe y es a propósito: unirlos


class ReprocesarIn(BaseModel):
    slugs: list[str] = []         # vacío = todas las entradas pendientes


class LinkIn(BaseModel):
    destino: str                  # slug de la entrada a vincular con [[destino]]


def _version() -> int:
    """La versión del CÓDIGO QUE CORRE ESTE PROCESO. Los .js se sirven del disco
    (siempre los últimos) pero el Python es el del arranque: cuando no coinciden,
    la app se comporta como una versión vieja y en pantalla no se nota nada. El
    2026-08-07 eso mandó a ComfyUI un video pedido "en higgsfield" — el arreglo
    estaba en el disco y el server tenía cinco horas. Ajustes lo compara."""
    try:
        t = (Path(__file__).parent / "static/js/version.js").read_text(encoding="utf-8")
        return int(re.search(r"n:\s*(\d+)", t).group(1))
    except (OSError, AttributeError, ValueError):
        return 0


VERSION = _version()


@app.get("/health")
def health():
    return {"ok": True, "hamuq": str(cfg["hamuq"]), "proveedor": cfg["proveedor"],
            "modelo": cfg["modelo"], "version": VERSION}


@app.post("/mcp/{secreto}")
async def mcp_http(secreto: str, request: Request):
    """MCP por streamable HTTP sin estado (Cowork y claude.ai vía Funnel).
    La llave es el secreto en el path (patrón webhook) — aparte del Bearer,
    que la PWA no manda. GET/DELETE no existen: solo POST, permitido por spec."""
    if not cfg.get("mcp_secreto") or secreto != cfg["mcp_secreto"]:
        raise HTTPException(401, "secreto inválido")
    r = mcp.despachar(await request.json(), cfg)
    return r if r is not None else Response(status_code=202)


@app.get("/services", dependencies=[Depends(auth)])
def get_services():
    return servicios.estado(cfg)


@app.get("/system", dependencies=[Depends(auth)])
def get_system():
    """CPU/RAM/GPU/VRAM y qué modelos hay cargados, para el panel de Ajustes.
    El % de CPU es el delta contra la llamada anterior, así que el primer valor
    del panel viene en null y el segundo ya es real."""
    return recursos.estado(cfg)


@app.post("/services/{nombre}/start", dependencies=[Depends(auth)])
def start_service(nombre: str):
    """Arranca un servicio local. El permiso es el clic/confirmación de Diego que
    disparó esta llamada — la app nunca la hace sola."""
    ok, mensaje = servicios.arrancar(cfg, nombre)
    if not ok:
        raise HTTPException(502, mensaje)
    return {"ok": True, "mensaje": mensaje}


@app.post("/services/comfyui/interrupt", dependencies=[Depends(auth)])
def interrupt_comfy():
    """Corta la generación en curso y vacía la cola (botón del panel de recursos)."""
    ok, mensaje = servicios.detener_trabajo(cfg)
    if not ok:
        raise HTTPException(502, mensaje)
    return {"ok": True, "mensaje": mensaje}


@app.post("/services/{nombre}/unload", dependencies=[Depends(auth)])
def unload_service(nombre: str):
    """Libera la VRAM que retiene un servicio sin apagarlo (comfyui | lmstudio)."""
    ok, mensaje = servicios.descargar_modelos(cfg, nombre)
    if not ok:
        raise HTTPException(502, mensaje)
    return {"ok": True, "mensaje": mensaje}


@app.get("/services/{nombre}/log", dependencies=[Depends(auth)])
def get_service_log(nombre: str, n: int = 200):
    """La consola que ya no está: cola del log de un servicio lanzado por MeM
    (%LOCALAPPDATA%\\MeM\\<nombre>.log). Vacío si Diego lo arrancó a mano."""
    return {"lineas": servicios.log_tail(nombre, n)}


@app.get("/media/workflows", dependencies=[Depends(auth)])
def get_workflows():
    """Workflows de ComfyUI exportados en la base (formato API): los modelos
    locales entre los que se puede elegir en Ajustes › Media."""
    wfs = crear.catalogo(cfg["hamuq"])
    # `workflows` (solo nombres) se mantiene por compatibilidad; `detalle` trae el
    # tipo, que es lo que deja al desplegable ofrecer solo los que corresponden
    return {"workflows": [w["nombre"] for w in wfs], "detalle": wfs}


@app.get("/media/cloud-models", dependencies=[Depends(auth)])
def get_modelos_nube(tipo: str = ""):
    """Catálogo de Higgsfield para el desplegable de Ajustes › Media. Vacío si el
    CLI no está o no hay sesión — el campo queda como estaba, no rompe la pantalla.
    Sin job_type por defecto, cada generación en la nube le cuesta al modelo tres
    pasos antes de arrancar (pedido 2026-08-07)."""
    return {"modelos": crear.modelos_lista(tipo)}


@app.get("/media/cloud-options", dependencies=[Depends(auth)])
def get_opciones_nube(modelo: str):
    """Lo que ESE job_type deja elegir (aspect_ratio, duration, resolution…). Lo
    pide el diálogo cuando el modelo se elige ahí mismo: cada uno trae las suyas."""
    return {"opciones": crear.opciones_nube(modelo)}


@app.post("/server/restart", dependencies=[Depends(auth)])
def server_restart():
    """Botón de Ajustes (sirve igual desde el móvil): responde, este proceso muere
    en <1 s y un ayudante suelto arranca el relevo con la misma línea de comandos.
    El front hace poll de /health hasta que el nuevo conteste."""
    return {"ok": True, "puerto": servicios.reiniciar_servidor(cfg)}


@app.get("/provider/test", dependencies=[Depends(auth)])
def provider_test(agente: str | None = None):
    return llm.probar(agentes.para(cfg, "chat", agente_id=agente) if agente else cfg)


@app.get("/provider/models", dependencies=[Depends(auth)])
def provider_models(agente: str):
    try:
        return {"modelos": llm.modelos(agentes.para(cfg, "chat", agente_id=agente))}
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")


@app.get("/agents", dependencies=[Depends(auth)])
def get_agents():
    # las modalidades van en un mapa aparte y no dentro de cada agente: el
    # frontend devuelve la lista tal cual en el POST, y son dato derivado del
    # modelo — guardadas en agentes.json quedarían viejas al primer cambio.
    return _agentes_payload(agentes.cargar(cfg))


def _agentes_payload(data: dict) -> dict:
    """Lo mismo que devuelve el GET, también al guardar: el frontend se queda con
    la respuesta del POST, y sin `tools` ahí el menú de la cabecera perdía la marca
    de "este agente no puede atender el taller" en cuanto se cambiaba uno."""
    return {**data, "tareas": list(agentes.TAREAS),
            "modalidades": {a["id"]: _modalidades(a) for a in data["agentes"]},
            # quién puede atender un modo que genera (el taller): sin tool-calling
            # por API no puede, y elegirlo ahí deja la generación sin salida
            "tools": {a["id"]: llm.hace_tools(a.get("proveedor", "")) for a in data["agentes"]}}


def _modalidades(a: dict) -> dict:
    ent, sal = llm.modalidades(a.get("modelo", ""))
    return {"in": ent, "out": sal}


@app.get("/agents/activos", dependencies=[Depends(auth)])
def get_agents_activos():
    """Los agentes que están realmente en uso: los asignados a alguna tarea (y el
    de fallback si no hay asignaciones). Sin llamar al proveedor: el estado vivo
    ('trabajando') lo pone el frontend mientras hay una petición en vuelo."""
    data = agentes.cargar(cfg)
    porid = {a["id"]: a for a in data["agentes"]}
    asig = data.get("asignaciones") or {}
    usados: dict[str, list[str]] = {}
    for tarea in agentes.TAREAS:
        ag = porid.get(asig.get(tarea)) or data["agentes"][0]
        usados.setdefault(ag["id"], []).append(tarea)
    return [{"id": i, "nombre": porid[i]["nombre"], "icono": porid[i].get("icono") or "✦",
             "modelo": porid[i].get("modelo", ""), "proveedor": porid[i].get("proveedor", ""),
             "tareas": ts, **_modalidades(porid[i])} for i, ts in usados.items()]


@app.post("/capture/triage", dependencies=[Depends(auth)])
def post_triage(t: TriajeIn):
    if not any(str(m.get("texto") or "").strip() for m in t.mensajes):
        raise HTTPException(400, "no hay nada que triar")
    try:
        return triaje.triar(cfg, t.mensajes, t.lang, t.adjunto, t.proyecto)
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")


@app.post("/agents", dependencies=[Depends(auth)])
def post_agents(a: AgentesIn):
    if not a.agentes or any(not str(ag.get("id") or "") for ag in a.agentes):
        raise HTTPException(400, "se requiere al menos un agente y todos con id")
    try:
        guardado = agentes.guardar({"agentes": a.agentes, "asignaciones": a.asignaciones})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _agentes_payload(guardado)


@app.get("/config", dependencies=[Depends(auth)])
def get_config():
    # busqueda va en GET y PATCH: settings.js hace setCfg(await patch(...)) y
    # si solo estuviera en GET, guardar un ajuste la borraría de la UI
    return {**config.sanitizado(cfg), "busqueda": indice.estado(cfg["hamuq"])}


@app.patch("/config", dependencies=[Depends(auth)])
def patch_config(c: ConfigPatch):
    if c.hamuq and not Path(c.hamuq).is_dir():
        raise HTTPException(400, f"la carpeta no existe: {c.hamuq}")
    config.actualizar(cfg, c.model_dump())
    return {**config.sanitizado(cfg), "busqueda": indice.estado(cfg["hamuq"])}


@app.get("/log", dependencies=[Depends(auth)])
def get_log(horas: float = 1.0, n: int = 500):
    desde = datetime.now() - timedelta(hours=horas)
    return {"lineas": memoria.leer_log(cfg["hamuq"], desde)[-n:], "desde": desde.isoformat()}


# -- procesado del inbox en segundo plano --------------------------------------
# Capturar no puede quedarse esperando al LLM: /capture dispara el procesado y
# vuelve al instante, así se pueden encolar varias cosas seguidas (pedido
# 2026-08-08). `_trabajo` serializa las pasadas —dos a la vez se pisarían los
# mismos .md, que procesar_item mueve de carpeta— y `_estado["otra"]` es el
# despertador: si llega una captura mientras una pasada corre, esa pasada ya
# hizo su glob y no la ve, así que al terminar se da otra vuelta.
# `hechas` es un contador que solo sube: el cliente detecta "algo terminó"
# viéndolo cambiar, sin que el server tenga que recordar qué mandó ya.
_trabajo = threading.Lock()
_estado_lock = threading.Lock()
_estado = {"corriendo": False, "hechas": 0, "errores": 0, "otra": False}


def _pasada():
    r = procesar.procesar_inbox(cfg)
    with _estado_lock:
        _estado["hechas"] += r["procesadas"]
        _estado["errores"] += r["errores"]
    return r


def _bucle_procesado():
    try:
        while True:
            with _trabajo:
                _pasada()
            with _estado_lock:
                # el chequeo de "otra" y el apagado de "corriendo" van bajo el
                # MISMO lock: si no, una captura entre medio vería corriendo=True,
                # se limitaría a marcar otra=True y nadie la levantaría nunca.
                if not _estado["otra"]:
                    _estado["corriendo"] = False
                    return
                _estado["otra"] = False
    except Exception as e:                      # noqa: BLE001 — el hilo no puede morir mudo
        with _estado_lock:
            _estado["corriendo"] = False
        memoria.log_evento(cfg["hamuq"], "procesador", f"procesado en fondo cortado: {e}")


def _arrancar_procesado():
    with _estado_lock:
        if _estado["corriendo"]:
            _estado["otra"] = True
            return False
        _estado["corriendo"] = True
        _estado["otra"] = False
    threading.Thread(target=_bucle_procesado, daemon=True).start()
    return True


@app.post("/process", dependencies=[Depends(auth)])
def post_process():
    # el botón explícito sigue siendo síncrono y devolviendo el recuento de SU
    # pasada; solo espera su turno si el procesado de fondo está a mitad de una
    with _trabajo:
        return _pasada()


@app.get("/process/status", dependencies=[Depends(auth)])
def get_process_status():
    with _estado_lock:
        return {"corriendo": _estado["corriendo"], "hechas": _estado["hechas"],
                "errores": _estado["errores"]}


# ------------------------------------------------------------------ Input / Inbox

@app.post("/capture", status_code=201, dependencies=[Depends(auth)])
def post_capture(c: CapturaIn):
    path = memoria.capturar(cfg["hamuq"], c.contenido, c.tipo, c.contexto,
                            c.tags, c.subjects, c.adjunto, c.origen, coords=c.coords,
                            proyecto=c.proyecto)
    _arrancar_procesado()
    return {"path": path}


@app.post("/attach", status_code=201, dependencies=[Depends(auth)])
async def post_attach(request: Request, nombre: str = "adjunto"):
    """Sube un adjunto crudo (sin python-multipart) al inbox. Devuelve el path
    relativo para pasarlo como `adjunto` en /capture o /memory/entry."""
    cuerpo = await request.body()
    if not cuerpo:
        raise HTTPException(400, "cuerpo vacío")
    destino = cfg["hamuq"] / "07_Inbox/_adjuntos"
    destino.mkdir(parents=True, exist_ok=True)
    nombre_seguro = memoria.slugificar(Path(nombre).stem) + Path(nombre).suffix
    rel = f"07_Inbox/_adjuntos/{datetime.now():%Y-%m-%d_%H%M%S}_{nombre_seguro}"
    (cfg["hamuq"] / rel).write_bytes(cuerpo)
    return {"path": rel, "nombre": nombre, "kb": round(len(cuerpo) / 1024, 1)}


@app.post("/share")
async def post_share(request: Request):
    """Web Share Target: lo que el móvil comparte CON MeM desde otra app.

    SIN auth a propósito y es el único endpoint así: el POST lo hace el navegador
    desde la hoja de compartir del sistema y no puede llevar cabeceras. Solo deja
    lo recibido en un buzón en memoria; recogerlo (GET de abajo) sí pasa por auth.
    La app no está publicada por el Funnel —solo /mcp lo está—, así que esto vive
    dentro del tailnet.
    """
    ident = compartir.recibir(cfg["hamuq"], await request.body(),
                              request.headers.get("content-type", ""))
    # 303: la navegación que sigue es un GET, si no el back reenviaría el POST
    return RedirectResponse(f"/#share/{ident}", status_code=303)


@app.get("/share/{ident}", dependencies=[Depends(auth)])
def get_share(ident: str):
    return compartir.tomar(ident)


@app.get("/attach/{ruta:path}", dependencies=[Depends(auth)])
def get_attach(ruta: str):
    p = (cfg["hamuq"] / ruta).resolve()
    if cfg["hamuq"].resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "adjunto no encontrado")
    return FileResponse(p)


@app.get("/inbox", dependencies=[Depends(auth)])
def get_inbox():
    return memoria.inbox_listar(cfg["hamuq"])


@app.patch("/inbox/{iid}", dependencies=[Depends(auth)])
def patch_inbox(iid: str, c: InboxPatch):
    try:
        return memoria.inbox_editar(cfg["hamuq"], iid, c.texto, c.tags, c.subjects)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@app.delete("/inbox/{iid}", dependencies=[Depends(auth)])
def delete_inbox(iid: str):
    try:
        return {"papelera": memoria.inbox_eliminar(cfg["hamuq"], iid)}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


# ------------------------------------------------------------------ Think / sesiones

@app.get("/modes", dependencies=[Depends(auth)])
def get_modes():
    # `agente` y `crea`: con qué tarea de Ajustes › Agentes se responde este modo y
    # si genera medios. La cabecera del chat los necesita para decir QUIÉN va a
    # contestar — decía siempre el agente de "chat" y en el taller contesta otro
    # (2026-08-07: el chip decía Smart y cargaba el qwen local).
    return [{"nombre": m["nombre"], "descripcion": m["descripcion"], "glyph": m["glyph"],
             "color": m["color"], "agente": str(m.get("agente") or "chat"),
             "crea": "crear" in (m.get("herramientas") or [])}
            for m in modos.listar(cfg["hamuq"])]


@app.get("/sessions", dependencies=[Depends(auth)])
def get_sessions(modo: str | None = None, archivadas: bool = False):
    return sesiones.listar(cfg["hamuq"], modo, archivadas)


@app.post("/sessions", status_code=201, dependencies=[Depends(auth)])
def post_session(s: SesionIn):
    return {"id": sesiones.crear(cfg["hamuq"], s.subjects, s.titulo, s.modo,
                                 estado="temporal" if s.temporal else "activa",
                                 proyecto=s.proyecto)}


@app.get("/sessions/{sid}", dependencies=[Depends(auth)])
def get_session(sid: str):
    try:
        meta, mensajes = sesiones.cargar(cfg["hamuq"], sid)
    except FileNotFoundError:
        raise HTTPException(404, "sesión no encontrada")
    root = cfg["hamuq"]
    # memorias: las entradas que nacieron acá — la app las marca en el chat y las
    # ofrece al borrar la sesión, sin pedir otra ronda al servidor.
    # adjuntos: mismo propósito para lo que la sesión subió o generó y quedó
    # pegado a los mensajes sin volverse una entrada — sin esto, borrar una
    # sesión con fotos nunca podía ofrecer llevárselas (pedido 2026-08-09).
    rutas = dict.fromkeys(r for m in mensajes for r in chat.RX_ATTACH.findall(m["texto"]))
    adjuntos = [r for r in rutas if (root / r).is_file()]
    return {"meta": meta, "mensajes": mensajes,
            "memorias": memoria.entradas_de_sesion(root, sid), "adjuntos": adjuntos,
            "inbox_estado": memoria.estado_sesion_inbox(root, sid)}


@app.patch("/sessions/{sid}", dependencies=[Depends(auth)])
def patch_session(sid: str, s: SesionPatch):
    campos = {k: v for k, v in s.model_dump().items() if v is not None}
    if not campos:
        raise HTTPException(400, "nada que cambiar")
    try:
        sesiones.actualizar_meta(cfg["hamuq"], sid, **campos)
        meta, _ = sesiones.cargar(cfg["hamuq"], sid)
    except FileNotFoundError:
        raise HTTPException(404, "sesión no encontrada")
    return meta


@app.delete("/sessions/{sid}", dependencies=[Depends(auth)])
def delete_session(sid: str, memorias: bool = False, contenido: bool = False):
    """contenido=1 (memorias=1 como alias por compat): también van a la papelera
    las entradas que la sesión generó y los adjuntos que ninguna otra página viva
    referencie. Todo a papelera, nada destructivo."""
    quiere = contenido or memorias
    root = cfg["hamuq"]
    borradas = memoria.entradas_de_sesion(root, sid) if quiere else []
    adjuntos = []
    if quiere:
        try:
            _, mensajes = sesiones.cargar(root, sid)  # antes de eliminar: mueve el archivo
        except FileNotFoundError:
            mensajes = []
        rutas = dict.fromkeys(r for m in mensajes for r in chat.RX_ATTACH.findall(m["texto"]))
        for r in memoria.adjuntos_sueltos_de(root, sid, list(rutas)):
            try:
                adjuntos.append(memoria.papelera_adjunto_suelto(root, r))
            except (FileNotFoundError, ValueError):
                pass
    try:
        papelera = sesiones.eliminar(root, sid)
    except FileNotFoundError:
        raise HTTPException(404, "sesión no encontrada")
    for e in borradas:
        try:
            memoria.eliminar_entrada(root, e["slug"])
        except FileNotFoundError:
            pass
    return {"papelera": papelera, "memorias": [e["slug"] for e in borradas], "adjuntos": adjuntos}


@app.post("/sessions/{sid}/distill", dependencies=[Depends(auth)])
def post_distill(sid: str):
    """'Pasar a memoria' sin archivar: sincroniza al inbox y compacta el contexto."""
    return chat.destilar(cfg, sid)


@app.post("/sessions/{sid}/archive", dependencies=[Depends(auth)])
def post_archive(sid: str):
    r = chat.destilar(cfg, sid)
    _arrancar_procesado()   # archivar = "terminé con esto", igual que /capture
    return {**r, "archivada": sesiones.archivar(cfg["hamuq"], sid)}


# Confirmaciones pendientes por sesión: el turno corre en su propio hilo, así que
# puede quedarse esperando el diálogo sin frenar al server (el SSE lo atiende el
# generador, que sigue vivo). Si nadie contesta —pestaña cerrada, celular
# bloqueado— el wait vence y se toma como "no": nunca se gasta plata por timeout.
_CONFIRMACIONES: dict[str, tuple[threading.Event, dict]] = {}
CONFIRM_TIMEOUT = 180

# El turno vive en el server, no en la pestaña. Este registro es lo que permite
# tres cosas que antes no se podían: cancelar (el hilo no es abortable, así que se
# le avisa con el Event y corta donde pueda), rechazar un segundo turno sobre la
# misma sesión (dos POST simultáneos escribían dos respuestas y pisaban el contador
# de turnos — pasó 2026-08-06 al recargar la PWA a mitad de una generación) y
# RECUPERAR el turno al volver: salir del chat mientras se genera un video de 30
# min ya no pierde nada, GET /turn lo cuenta y el resultado llega igual.
# ponytail: una entrada por sesión y no se limpian — son bytes, y el registro
# muere con el proceso igual que el hilo que describe.
_TURNOS: dict[str, dict] = {}
_ms = lambda: int(time.time() * 1000)   # noqa: E731  (mismo reloj que Date.now())


@app.get("/sessions/{sid}/turn", dependencies=[Depends(auth)])
def get_turn(sid: str):
    """Estado del último turno de la sesión, para reengancharse tras volver."""
    t = _TURNOS.get(sid)
    return {"estado": "idle"} if not t else {k: v for k, v in t.items() if k != "cancelar"}


@app.post("/sessions/{sid}/messages", dependencies=[Depends(auth)])
def post_message(sid: str, m: MensajeIn):
    """SSE: eventos 'tool' por cada llamada a herramienta y 'tool_fin' cuando esa
    llamada devuelve, 'confirmar' cuando una tool de nube necesita el ok de Diego,
    'done' con la respuesta final, 'cancelado' si Diego lo detuvo."""
    if (previo := _TURNOS.get(sid)) and previo["estado"] in ("working", "confirmando"):
        raise HTTPException(409, "ya hay un turno en curso en esta sesión")
    cancelar = threading.Event()
    reg = _TURNOS[sid] = {"estado": "working", "tools": [], "t0": _ms(), "cancelar": cancelar,
                          "pedido": None, "resultado": None, "detalle": ""}
    q: queue.Queue = queue.Queue()

    def anotar(evento: str, data: dict):
        """Todo lo que va por SSE queda también en el registro: quien se reengancha
        más tarde ve exactamente lo mismo que quien nunca se fue."""
        q.put((evento, data))
        if evento == "tool":
            reg["tools"].append({**data, "t0": _ms()})
        elif evento == "tool_fin":
            i = next((k for k in range(len(reg["tools"]) - 1, -1, -1)
                      if reg["tools"][k]["tool"] == data["tool"] and not reg["tools"][k].get("fin")), -1)
            if i >= 0:
                reg["tools"][i]["fin"] = _ms()

    def confirmar(nombre: str, args: dict) -> dict | str:
        """Los args con los que generar de verdad, o el texto del "no" que el modelo
        recibe como resultado de la tool. `opciones` = lo que ESTE job_type deja
        elegir y nadie fijó (resolución, duración, fps): se pregunta en el mismo
        diálogo, que es el único momento en que Diego está mirando — por chat se
        perdía (pedido 2026-08-07). Que no conteste nadie vale como "no": nunca se
        gasta por timeout. `modelos` = el catálogo de Higgsfield cuando Ajustes no
        fija un job_type por defecto: elegirlo es parte de la misma pregunta, en vez
        de pedirle al modelo que salga a buscarlo (2026-08-07)."""
        ev = threading.Event()
        _CONFIRMACIONES[sid] = (ev, {"ok": False, "params": {}, "local": False, "modelo": ""})
        modelo = str(args.get("modelo") or "")
        pedido = {"tool": nombre, "args": args,
                  "opciones": crear.opciones_nube(modelo, args.get("params") or {}) if modelo else [],
                  "modelos": [] if modelo else
                  crear.modelos_lista(crear.TIPO_API.get(str(args.get("tipo") or ""), ""))}
        reg["estado"], reg["pedido"] = "confirmando", pedido
        try:
            anotar("confirmar", pedido)
            if not ev.wait(CONFIRM_TIMEOUT):
                # nadie contestó ≠ Diego dijo que no: el turno y el modelo tienen
                # que poder distinguirlos (reportado 2026-08-23)
                return crear.SIN_RESPUESTA
            estado = _CONFIRMACIONES.get(sid, (None, {}))[1]
            if not estado.get("ok"):
                # "no, pero hacelo en local" es la salida que en el chat costaba dos
                # mensajes más: se contesta una vez y el turno sigue (pedido 2026-08-07)
                return crear.RECHAZO_LOCAL if estado.get("local") else crear.RECHAZO
            return {**args, "modelo": modelo or str(estado.get("modelo") or ""),
                    "params": {**(args.get("params") or {}), **(estado.get("params") or {})}}
        finally:
            _CONFIRMACIONES.pop(sid, None)
            reg["estado"], reg["pedido"] = "working", None

    def correr():
        try:
            adjuntos = m.adjuntos or ([m.adjunto] if m.adjunto else [])
            texto, paginas, tokens = chat.turno(cfg, sid, m.texto, adjuntos=adjuntos, agente=m.agente,
                                                on_event=lambda n, a: anotar("tool", {"tool": n, "args": a}),
                                                on_result=lambda n, a, s: anotar("tool_fin", {"tool": n}),
                                                on_confirm=confirmar, cancelar=cancelar)
            if cancelar.is_set():
                reg["estado"] = "cancelado"
                q.put(("cancelado", {}))
            else:
                reg["estado"] = "done"
                reg["resultado"] = {"texto": texto, "paginas": paginas, "tokens_entrada": tokens}
                q.put(("done", reg["resultado"]))
        except Exception as e:  # el error viaja al cliente; el server sigue vivo
            # traducido: qué pasó y qué puede hacer Diego. El crudo va como
            # `detalle` y acotado — el str() pelado de un TimeoutExpired es la línea
            # de comandos entera con el system prompt adentro (visto 2026-08-07).
            d = fallas.explicar(e)
            reg["estado"], reg["falla"], reg["detalle"] = "error", d, d["problema"]
            q.put(("error", d))
        q.put(None)

    threading.Thread(target=correr, daemon=True).start()

    def eventos():
        while (item := q.get()) is not None:
            evento, data = item
            yield f"event: {evento}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(eventos(), media_type="text/event-stream")


@app.post("/sessions/{sid}/cancel", dependencies=[Depends(auth)])
def post_cancel(sid: str, c: CancelIn):
    """Botón de detener del chat. Nada queda colgado: el Event lo lee el turno
    entre pasos, y `comfy` corta además lo que se esté generando ahora mismo (si
    no, seguiría hasta terminar aunque ya nadie lo espere)."""
    t = _TURNOS.get(sid)
    if not t or t["estado"] not in ("working", "confirmando"):
        raise HTTPException(404, "no hay ningún turno en curso en esta sesión")
    t["cancelar"].set()
    # un turno frenado en el diálogo de la nube no mira el Event: se le contesta
    # que no, que es lo mismo que hace el timeout — cancelar nunca gasta créditos
    if pendiente := _CONFIRMACIONES.get(sid):
        pendiente[1]["ok"] = False
        pendiente[0].set()
    detenido = servicios.detener_trabajo(cfg)[1] if c.comfy else ""
    return {"ok": True, "comfy": detenido}


@app.post("/sessions/{sid}/confirm", dependencies=[Depends(auth)])
def post_confirm(sid: str, c: ConfirmIn):
    """Respuesta al diálogo del evento 'confirmar': destraba el turno que espera."""
    pendiente = _CONFIRMACIONES.get(sid)
    if not pendiente:
        raise HTTPException(404, "no hay nada esperando confirmación en esta sesión")
    evento, estado = pendiente
    estado["ok"], estado["params"], estado["local"] = c.ok, dict(c.params), bool(c.local)
    estado["modelo"] = c.modelo
    evento.set()
    return {"ok": c.ok}


# ------------------------------------------------------------------ Memory

@app.get("/memory/tree", dependencies=[Depends(auth)])
def get_tree():
    return memoria.arbol_subjects(cfg["hamuq"])


class SintesisIn(BaseModel):
    subject: str


@app.post("/memory/synthesize", dependencies=[Depends(auth)])
def post_synthesize(s: SintesisIn):
    """Crea o regenera la página de síntesis de un tema. A partir de acá el
    procesador de inbox la mantiene al día sola."""
    try:
        return sintesis.sintetizar(cfg, s.subject)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"el modelo no respondió: {e}")


@app.get("/projects", dependencies=[Depends(auth)])
def get_projects():
    return memoria.proyectos_listar(cfg["hamuq"])


@app.post("/projects", status_code=201, dependencies=[Depends(auth)])
def post_project(p: ProyectoIn):
    try:
        return memoria.proyecto_guardar(cfg["hamuq"], p.nombre, p.ambito)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.patch("/projects/{nombre}", dependencies=[Depends(auth)])
def patch_project(nombre: str, p: ProyectoRen):
    """Renombrar: mueve el subject en las memorias y el campo en las sesiones.
    `fusionar` = el nombre nuevo ya existe a propósito: unir los dos proyectos."""
    sids = sesiones.de_proyecto(cfg["hamuq"], nombre)
    try:
        out = memoria.proyecto_renombrar(cfg["hamuq"], nombre, p.nombre, p.fusionar)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    for sid in sids:
        sesiones.actualizar_meta(cfg["hamuq"], sid, proyecto=out["nombre"])
    return out


@app.delete("/projects/{nombre}", dependencies=[Depends(auth)])
def delete_project(nombre: str, contenido: bool = False, sesiones_: str = Query("mover", alias="sesiones"),
                   memorias: str = "mover", destino: str = ""):
    """Qué pasa con lo que colgaba del proyecto, por clase: "papelera" lo manda
    a la basura, "mover" lo deja vivo bajo `destino` (vacío = sin proyecto).
    Nada destructivo (spec §8.3). `contenido=1` es el contrato viejo — un shell
    cacheado todavía lo manda — y equivale a papelera para ambas clases."""
    if contenido:
        sesiones_ = memorias = "papelera"
    sids = sesiones.de_proyecto(cfg["hamuq"], nombre)
    try:
        out = memoria.proyecto_eliminar(cfg["hamuq"], nombre, con_contenido=memorias == "papelera",
                                        destino=destino)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    for sid in sids:
        if sesiones_ == "papelera":
            sesiones.eliminar(cfg["hamuq"], sid)
        else:
            sesiones.actualizar_meta(cfg["hamuq"], sid, proyecto=destino)
    return {**out, "sesiones": sids}


@app.post("/memory/reprocess", dependencies=[Depends(auth)])
def post_reprocess(r: ReprocesarIn):
    """Reprocesa las memorias que quedaron pendientes — el caso real es cambiar
    el LLM por uno con visión/audio y recuperar el adjunto sin recapturar nada."""
    return procesar.reprocesar(cfg, r.slugs)


@app.post("/memory/reindex", dependencies=[Depends(auth)])
def post_reindex():
    """Reconstruye el índice derivado de búsqueda (mem.db). Es la única operación
    que descarga el modelo de embeddings si todavía no está en el disco."""
    return indice.reindexar(cfg["hamuq"])


@app.post("/export", dependencies=[Depends(auth)])
def post_export(c: CarpetaIn):
    """Backup a una carpeta de la máquina: lo capturado, sin lo que dedujo el
    procesador. El server escribe donde le digan — por eso la app pide huella
    antes en el celular, y por eso exportar.py rechaza rutas relativas o
    adentro del vault (un backup ahí sería material nuevo para procesar)."""
    try:
        return exportar.exportar(cfg["hamuq"], Path(c.carpeta))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(400, f"no se pudo escribir ahí: {e}")


@app.post("/import", dependencies=[Depends(auth)])
def post_import(c: CarpetaIn):
    """Mete un backup: lo conocido se saltea, lo casi idéntico se fusiona con su
    entrada y lo nuevo entra al inbox para que se procese como cualquier captura."""
    try:
        return exportar.importar(cfg["hamuq"], Path(c.carpeta))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(400, f"no se pudo leer de ahí: {e}")


@app.get("/memory/graph", dependencies=[Depends(auth)])
def get_graph(slugs: str | None = None, proyecto: str | None = Depends(parado_en)):
    """Grafo: memorias + subjects; aristas por subject, wikilink y similitud
    semántica. Sin `slugs`, la base entera; con `slugs` (coma), solo esas
    memorias y su vecindario — el mapa de una sesión. `slugs=` (vacío) es un
    grafo vacío, no la base: por eso el default es None y no ""."""
    return indice.grafo(cfg["hamuq"],
                        None if slugs is None else [s for s in slugs.split(",") if s],
                        proyecto=proyecto)


@app.get("/memory/suggest", dependencies=[Depends(auth)])
def get_suggest(texto: str = "", proyecto: str | None = Depends(parado_en)):
    """"Recordar mientras uno escribe": las memorias MUY cercanas a lo que se
    está tecleando en una sesión. Devuelve [] con cualquier duda — el compositor
    no puede llenarse de sugerencias lejanas."""
    return indice.sugerencias(cfg["hamuq"], texto, proyecto=proyecto)


@app.get("/memory/semantic", dependencies=[Depends(auth)])
def get_semantic(proyecto: str | None = Depends(parado_en)):
    """Mapa semántico: proyección 2D (PCA) de los embeddings, cacheada."""
    return {"puntos": indice.proyeccion(cfg["hamuq"], proyecto=proyecto)}


@app.get("/memory/map", dependencies=[Depends(auth)])
def get_map(proyecto: str | None = Depends(parado_en)):
    """Memorias agrupadas por lugar con lat/lon (geocache Nominatim; a lo sumo
    5 lugares nuevos se geocodifican por llamada para no colgar la UI)."""
    return indice.mapa(cfg["hamuq"], proyecto=proyecto)


@app.get("/memory/search", dependencies=[Depends(auth)])
def get_search(texto: str = "", tag: str = "", subject: str = "", desde: str = "", hasta: str = "",
               lugar: str = "", orden: str = "fecha", sesion: str = "", sin_proyecto: bool = False,
               proyecto: str | None = Depends(parado_en)):
    return memoria.buscar_memorias(cfg["hamuq"], texto, tag, subject, desde, hasta, lugar, orden,
                                   sesion, sin_proyecto, proyecto=proyecto)


@app.get("/media", dependencies=[Depends(auth)])
def get_media(texto: str = "", desde: str = "", hasta: str = "", sesion: str = "", subject: str = "",
              proyecto: str | None = Depends(parado_en)):
    """Galería: entradas catalogadas + adjuntos sueltos de una sesión sin
    destilar (memoria.medios_todos — /memory/search no ve estos últimos)."""
    return memoria.medios_todos(cfg["hamuq"], texto=texto, desde=desde, hasta=hasta, sesion=sesion,
                                subject=subject, proyecto=proyecto)


@app.post("/memory/entry", status_code=201, dependencies=[Depends(auth)])
def post_entry(e: EntradaIn):
    return {"resultado": memoria.guardar_entrada(cfg["hamuq"], e.titulo, e.contenido, e.subjects,
                                                 origen="app", tags=e.tags)}


@app.get("/memory/entry/{slug}", dependencies=[Depends(auth)])
def get_entry(slug: str, proyecto: str | None = Depends(parado_en)):
    try:
        entrada = memoria.leer_entrada(cfg["hamuq"], slug)
    except FileNotFoundError:
        raise HTTPException(404, "entrada no encontrada")
    # la ficha es la puerta final: si no se puede ver desde acá, tampoco por link
    if not memoria.accesible(entrada, proyecto):
        raise HTTPException(403, "memoria privada: solo se abre desde su propio proyecto")
    entrada["conexiones"] = memoria.conexiones(cfg["hamuq"], slug, proyecto=proyecto)
    return entrada


@app.patch("/memory/entry/{slug}", dependencies=[Depends(auth)])
def patch_entry(slug: str, e: EntradaPatch):
    try:
        return {"resultado": memoria.editar_entrada(cfg["hamuq"], slug, e.titulo, e.tags,
                                                    e.subjects, e.nota, resumen=e.resumen,
                                                    privada=e.privada)}
    except FileNotFoundError:
        raise HTTPException(404, "entrada no encontrada")


@app.post("/memory/entry/{slug}/link", dependencies=[Depends(auth)])
def post_link(slug: str, e: LinkIn):
    """Confirma una sugerencia de enlace: escribe el [[wikilink]] fechado."""
    try:
        return {"resultado": memoria.enlazar_entradas(cfg["hamuq"], slug, e.destino)}
    except FileNotFoundError:
        raise HTTPException(404, "entrada no encontrada")


@app.get("/memory/entry/{slug}/versions", dependencies=[Depends(auth)])
def get_versions(slug: str):
    """Versiones anteriores de una memoria; la viva es GET /memory/entry/{slug}."""
    try:
        return memoria.versiones(cfg["hamuq"], slug)
    except FileNotFoundError:
        raise HTTPException(404, "entrada no encontrada")


@app.get("/memory/entry/{slug}/versions/{n}", dependencies=[Depends(auth)])
def get_version(slug: str, n: int):
    try:
        return memoria.version_leer(cfg["hamuq"], slug, n)
    except FileNotFoundError:
        raise HTTPException(404, "versión no encontrada")


@app.get("/lint", dependencies=[Depends(auth)])
def get_lint():
    return {"problemas": lint.correr(cfg["hamuq"])}


class IgnoreIn(BaseModel):
    tipo: str    # "duplicados" | "medios" | "avisos"
    clave: str   # "slugA|slugB", la ruta del medio o la clave del aviso


@app.post("/lint/ignore", dependencies=[Depends(auth)])
def post_lint_ignore(i: IgnoreIn):
    if i.tipo not in lint.TIPOS_IGNORABLES:
        raise HTTPException(400, f"tipo debe ser uno de: {', '.join(lint.TIPOS_IGNORABLES)}")
    lint.ignorar(cfg["hamuq"], i.tipo, i.clave)
    if i.tipo == "avisos":
        lint.aviso_quitar(cfg["hamuq"], i.clave)
    return {"problemas": lint.correr(cfg["hamuq"])}


class AvisoIn(BaseModel):
    clave: str


@app.post("/lint/annotate", dependencies=[Depends(auth)])
def post_lint_annotate(a: AvisoIn):
    """Acepta un aviso semántico: deja la línea fechada en la memoria afectada
    (slugs[0]) citando a las que la contradicen, y saca el aviso de la lista."""
    root = cfg["hamuq"]
    av = next((x for x in lint.avisos_cargar(root) if x["clave"] == a.clave), None)
    if not av or not av.get("slugs"):
        raise HTTPException(404, "aviso no encontrado o sin memoria a la que anotar")
    otras = " ".join(f"[[{s}]]" for s in av["slugs"][1:])
    texto = f"revisar contra {otras}: {av['detalle']}" if otras else av["detalle"]
    try:
        memoria.anotar_registro(root, av["slugs"][0], texto)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    lint.aviso_quitar(root, a.clave)
    return {"problemas": lint.correr(root)}


@app.post("/lint/semantic", dependencies=[Depends(auth)])
def post_lint_semantic():
    """Pase LLM sobre toda la Biblioteca. Caro y bajo demanda: sus hallazgos
    quedan guardados y los muestra el GET /lint de siempre."""
    try:
        nuevos = lint.semantico(cfg)
    except Exception as e:
        raise HTTPException(502, f"el modelo no respondió: {e}")
    return {"nuevos": len(nuevos), "problemas": lint.correr(cfg["hamuq"])}


class MergeIn(BaseModel):
    absorbe: str
    absorbida: str


@app.post("/memory/merge", dependencies=[Depends(auth)])
def post_merge(m: MergeIn):
    try:
        memoria.fusionar_entradas(cfg["hamuq"], m.absorbe, m.absorbida)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"problemas": lint.correr(cfg["hamuq"])}


class AdjuntoIn(BaseModel):
    ruta: str


@app.post("/lint/discard-media", dependencies=[Depends(auth)])
def post_discard_media(a: AdjuntoIn):
    try:
        memoria.papelera_adjunto_suelto(cfg["hamuq"], a.ruta)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"problemas": lint.correr(cfg["hamuq"])}


# PWA + assets — montado al final para no tapar las rutas de arriba.
# html=True: "/" y cualquier ruta no reconocida caen a index.html (router por hash del SPA).
app.mount("/", StaticFiles(directory=ESTATICO, html=True), name="static")
