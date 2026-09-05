"""Procesador de inbox: entender, categorizar, etiquetar e indexar cada
captura pendiente (spec §4). Corre bajo demanda ("Procesar ahora" / `mem
process`) o programado externamente (Task Scheduler → `mem process`).

Regla de oro (spec §3.3, §8.2): lo que el usuario fijó a mano (`fijado`)
nunca se sobreescribe; `contexto_usuario` tiene prioridad sobre la
clasificación automática.
"""
import json
import re
import tempfile
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import frontmatter

from . import agentes
from . import llm as llm_mod
from . import lint, media, memoria, sintesis, youtube

RX_URL = re.compile(r"https?://[^\s<>\"'\)\]]+")
MAX_ENLACES = 3          # ponytail: tope de fetches por captura; subir si aparecen capturas con más links útiles

PROMPT_SISTEMA = (
    "Eres el procesador de inbox de hamuQ, la memoria persistente de Diego. "
    "Tu trabajo: entender una captura cruda —texto, páginas enlazadas y adjuntos— y "
    "extraer TODA la información posible. Devuelve SOLO un JSON (sin markdown, "
    "sin explicación) con esta forma exacta:\n"
    '{"titulo": "...", "sintesis": "...", "tipo": "nota|link|tarea", '
    '"tags": ["..."], "subjects": ["Categoria/Subcategoria", ...], '
    '"lugar": "...", "cuando": "..."}\n\n'
    "- titulo: corto y descriptivo.\n"
    "- sintesis: todo lo relevante reescrito en español, denso, sin perder datos "
    "concretos (nombres, cifras, precios, direcciones, pasos, conclusiones). Usa el "
    "contenido de las páginas y adjuntos, no solo el texto suelto.\n"
    "- subjects: TODAS las categorías a las que pertenece la información (una captura "
    "puede caer en varias). Reusa categorías del árbol dado abajo; solo agrega una "
    "nueva si de verdad no encaja en ninguna existente.\n"
    "- lugar: lugar al que se refiere la información (ciudad, sede, dirección, sala, "
    "plataforma). Cadena vacía si la captura no menciona ninguno. No inventes.\n"
    "- cuando: fecha/hora A LA QUE SE REFIERE la información en ISO 8601 "
    "(AAAA-MM-DD o AAAA-MM-DDTHH:MM), resolviendo lo relativo ('mañana', 'el viernes') "
    "contra la fecha de captura que se te da. Cadena vacía si no hay fecha propia; "
    "no uses la de captura como relleno.\n"
    "- Si se te da contexto del usuario o campos ya fijados, respétalos: tienen prioridad."
)
RX_RESULTADO = re.compile(r"^entrada (?:creada|actualizada): (.+)$")

# Lo que el patrón wiki hace y la ingesta de MeM no hacía: al entrar material
# nuevo, contrastarlo con lo que ya se sabía. No edita nada — deja avisos que
# Diego confirma en el Lint, porque el registro histórico es historia (§7.3) y un
# falso positivo del modelo anexado solo no se podría deshacer.
PROMPT_CONTRASTE = (
    "Comparas una memoria NUEVA de Diego con sus vecinas más cercanas de la biblioteca. "
    "Devuelve SOLO un JSON (sin markdown, sin explicación):\n"
    '{"avisos": [{"slug": "...", "tipo": "contradice|actualiza", "detalle": "una línea"}]}\n'
    "- Un aviso SOLO si la memoria nueva contradice un dato CONCRETO de la vecina "
    "(fecha, cifra, nombre, decisión, estado) o la deja claramente desactualizada.\n"
    "- Mismo tema sin conflicto = {\"avisos\": []}. No especules ni rellenes.\n"
    "- slug: el de la vecina afectada, tal cual se te dio.\n"
    "- detalle: qué dato cambió y a qué, en una línea, en español."
)
MAX_VECINAS = 5
TIPO_AVISO = {"contradice": "contradiccion", "actualiza": "obsoleta"}


class _ExtraeTexto(HTMLParser):
    """Texto visible de una página, sin script/style — para categorizar un link
    por su contenido real, no por la URL pelada."""
    def __init__(self):
        super().__init__()
        self.partes = []
        self.titulo = ""
        self._omitir = False
        self._en_titulo = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._omitir = True
        elif tag == "title":
            self._en_titulo = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._omitir = False
        elif tag == "title":
            self._en_titulo = False

    def handle_data(self, data):
        if self._en_titulo and not self.titulo:
            self.titulo = data.strip()
        if not self._omitir and data.strip():
            self.partes.append(data.strip())


def _leer_url(url: str, timeout: float = 8.0) -> str:
    """Trae título + texto visible de una URL. Si falla (red, timeout, no-HTML) no
    revienta el procesado: se cae al link pelado, que es lo que había antes."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (MeM)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            crudo = r.read(500_000)
        parser = _ExtraeTexto()
        parser.feed(crudo.decode("utf-8", errors="replace"))
        cuerpo = " ".join(parser.partes)[:4000]
        return f"{parser.titulo}\n{cuerpo}".strip() if parser.titulo else cuerpo
    except Exception:
        return ""


def _enlaces(post, meta: dict) -> list[str]:
    """Todos los links de la captura (texto + contexto del usuario), sin repetir."""
    crudo = f"{post.content} {meta.get('contexto_usuario') or ''}"
    return list(dict.fromkeys(u.rstrip(".,;:") for u in RX_URL.findall(crudo)))[:MAX_ENLACES]


def _parsear_json(texto: str) -> dict:
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ValueError(f"el modelo no devolvió JSON: {texto[:200]}")
    return json.loads(m.group(0))


def _ficha(root: Path, slug: str) -> dict | None:
    """Título + resumen de una entrada para el contraste. Las síntesis quedan
    fuera: son derivadas de otras memorias, contradecirlas es ruido."""
    p = root / memoria.ENTRADAS / f"{slug}.md"
    if not p.is_file():
        return None
    post = frontmatter.load(p)
    if post.metadata.get("sintesis_de"):
        return None
    return {"slug": slug, "titulo": str(post.metadata.get("titulo", slug)),
            "resumen": memoria.resumen_corto(post.content, 1000)}


def _contrastar(cfg: dict, root: Path, slug: str) -> int:
    """Contrasta la entrada recién guardada con sus vecinas semánticas y deja
    avisos para el Lint. Devuelve cuántos avisos nuevos dejó."""
    from . import indice
    nueva = _ficha(root, slug)
    if not nueva:
        return 0
    vecinas = [f for s, _ in indice.vecinas(root, slug, k=MAX_VECINAS) if (f := _ficha(root, s))]
    if not vecinas:
        return 0   # el caso común (base chica, tema nuevo): sin vecindario no se paga una llamada
    cliente = llm_mod.crear(agentes.para(cfg, "procesar"))
    partes = [f"MEMORIA NUEVA ({nueva['slug']}) — {nueva['titulo']}\n{nueva['resumen']}",
              "VECINAS YA EN LA BIBLIOTECA:"]
    partes += [f"- {v['slug']} — {v['titulo']}\n  {v['resumen']}" for v in vecinas]
    r = cliente.completar(PROMPT_CONTRASTE, [{"role": "user", "content": "\n\n".join(partes)}], None)
    validos = {v["slug"] for v in vecinas}
    n = 0
    for a in _parsear_json(r["texto"]).get("avisos") or []:
        afectada = str(a.get("slug") or "")
        tipo = TIPO_AVISO.get(str(a.get("tipo") or ""))
        if afectada in validos and tipo:
            n += lint.aviso_agregar(root, tipo, [afectada, slug], a.get("detalle") or "", "ingesta")
    return n


def procesar_item(cfg: dict, p: Path) -> dict:
    """Procesa un item pendiente del inbox. Levanta excepción si algo falla
    (el llamador la captura y marca el item con estado: error)."""
    root = cfg["hamuq"]
    post = frontmatter.load(p)
    meta = post.metadata
    fijado = set(meta.get("fijado") or [])

    arbol = root / "08_Categorias/00_CATEGORIAS.md"
    arbol_txt = arbol.read_text(encoding="utf-8") if arbol.exists() else ""

    partes = [f"Tipo declarado: {meta.get('tipo', 'nota')}",
              f"Fecha de captura: {meta.get('capturado', '')}",
              f"Texto:\n{post.content.strip()}"]
    if meta.get("contexto_usuario"):
        partes.append(f"Contexto del usuario (prioridad): {meta['contexto_usuario']}")
    if "tags" in fijado:
        partes.append(f"Tags ya fijados por el usuario (no los cambies): {meta.get('tags')}")
    if "subjects" in fijado:
        partes.append(f"Subjects ya fijados por el usuario (no los cambies): {meta.get('subjects')}")

    ag = agentes.para(cfg, "procesar")

    enlaces = _enlaces(post, meta)
    for url in enlaces:
        # la página de un video de YouTube no dice nada del video: se baja y se mira
        pagina = "" if youtube.es_youtube(url) else _leer_url(url)
        if pagina:
            partes.append(f"Contenido real de la página ({url}):\n{pagina}")

    adj = meta.get("adjunto", "")
    contenido_adj, faltante, derivado = media.extraer(ag, root, adj)
    fuente = adj
    yt = "" if adj else next((u for u in enlaces if youtube.es_youtube(u)), "")
    if yt:
        # el video se mira y se tira: lo que queda en la memoria es esta línea de
        # tiempo (que es lo que se busca) y el link (que es lo que se vuelve a ver)
        fuente = yt
        with tempfile.TemporaryDirectory() as tmp:
            try:
                video, ficha = youtube.bajar(yt, Path(tmp))
                partes.append(ficha)
                contenido_adj, falta_yt = media.describir_video(ag, video)
                derivado = True
                faltante = f"{yt}: {falta_yt}" if falta_yt else ""
            except Exception as e:
                faltante = str(e)[:300]
    if contenido_adj:
        # al prompt va condensado (una hora de video no entra en un pedido), al
        # cuerpo de la entrada va entero: son dos destinos, no el mismo texto
        partes.append(f"Contenido del adjunto ({fuente}):\n{media.condensar(ag, contenido_adj)}")
        if derivado:
            partes.append("Ese bloque es la ÚNICA versión en texto de un adjunto que nadie más puede "
                          "leer (imagen/audio/video/PDF): úsalo entero para titular, sintetizar y categorizar.")
    elif faltante:
        # el modelo igual debe saber que hay material que no se pudo leer, para
        # no afirmar que la captura está completa
        partes.append(f"Adjunto que NO se pudo leer: {faltante}")

    cliente = llm_mod.crear(ag)
    system = f"{PROMPT_SISTEMA}\n\nÁrbol de categorías existente:\n{arbol_txt}"
    if ag.get("system_prompt"):
        system = f"{ag['system_prompt']}\n\n---\n\n{system}"
    r = cliente.completar(system, [{"role": "user", "content": "\n\n".join(partes)}], None)
    datos = _parsear_json(r["texto"])

    tags = list(meta.get("tags") or []) if "tags" in fijado else list(datos.get("tags") or [])
    subjects = list(meta.get("subjects") or []) if "subjects" in fijado else list(datos.get("subjects") or [])
    # la sesión tiene proyecto: lo que salga de ella entra al proyecto sí o sí,
    # sin depender de que el clasificador lo redescubra (mismo criterio que
    # tenía chat._ejecutar antes de que guardar_entrada se retirara del chat)
    if proyecto := str(meta.get("proyecto") or ""):
        subjects = list(dict.fromkeys([*subjects, memoria.subject_proyecto(proyecto)]))
    titulo = datos.get("titulo") or meta.get("id") or p.stem
    sintesis = datos.get("sintesis") or post.content.strip()
    extra = {"lugar": str(datos.get("lugar") or ""), "cuando": str(datos.get("cuando") or ""),
             "enlaces": enlaces, "capturado": meta.get("capturado", "")}
    # dónde estaba Diego al capturar (par espacial de `capturado`). Las coordenadas
    # viajan tal cual desde el inbox; el nombre se resuelve UNA vez acá y queda
    # escrito en el .md — que la memoria diga "Miraflores, Lima" y no dos números.
    if coords := str(meta.get("coords_captura") or ""):
        from . import indice
        extra["coords_captura"] = coords
        extra["lugar_captura"] = str(meta.get("lugar_captura") or "") or indice.lugar_de_coords(coords)
    # de qué sesión salió (chip "sesión de origen" en la app, memoria.entradas_de_sesion)
    if sesion := str(meta.get("sesion") or ""):
        extra["sesion"] = sesion

    # reproceso: escribir en la entrada que este item YA creó, no en la que
    # saldría del título nuevo (el modelo rara vez repite el título exacto).
    reproceso = bool(meta.get("entrada"))
    slug = Path(str(meta.get("entrada") or "")).stem or memoria.slugificar(titulo)
    # reproceso = mismo material leído mejor: el título y el resumen nuevos mandan.
    # Sin esto una entrada que nació "Adjunto no leído" se quedaba con ese nombre
    # para siempre aunque el pase siguiente sí viera la imagen.
    resultado = memoria.guardar_entrada(root, titulo, sintesis, subjects, origen="inbox",
                                        tags=tags, adjunto=adj, meta=extra, slug=slug,
                                        transcripcion=contenido_adj if derivado else "",
                                        reemplazar=reproceso)
    m = RX_RESULTADO.match(resultado)
    entrada_path = m.group(1) if m else resultado
    # se fija aparte (y no vía meta=) porque hay que poder LIMPIARLO: si este pase
    # sí leyó el adjunto, la entrada deja de estar pendiente.
    pendiente = [faltante] if faltante else []
    memoria.fijar_pendiente(root, slug, pendiente)

    destino = root / "07_Inbox/_procesado"
    destino.mkdir(parents=True, exist_ok=True)
    post.metadata.update(estado="procesada", tipo=datos.get("tipo", meta.get("tipo", "nota")),
                         tags=tags, subjects=subjects, entrada=entrada_path,
                         pendiente=pendiente, **extra)
    post.metadata.pop("error", None)
    (destino / p.name).write_text(frontmatter.dumps(post), encoding="utf-8")
    p.unlink()
    # el item ya terminó su viaje: lo que siga es mejora, no puede marcarlo con error
    try:
        _contrastar(cfg, root, slug)
    except Exception as e:
        memoria.log_evento(root, "procesador", f"{p.stem}: contraste falló — {e}")
    return {"id": p.stem, "estado": "procesada", "entrada": entrada_path,
            "pendiente": pendiente, "subjects": subjects}


def procesar_inbox(cfg: dict) -> dict:
    """Procesa todos los pendientes de 07_Inbox/. No toca _procesado/, _papelera/ ni _adjuntos/."""
    return _correr(cfg, sorted((cfg["hamuq"] / "07_Inbox").glob("*.md")))


def reprocesar(cfg: dict, slugs: list[str] | None = None) -> dict:
    """Devuelve al inbox items ya procesados y los vuelve a procesar. El caso de
    uso es cambiar de LLM por uno que sí lea el adjunto que quedó pendiente: no
    hay que volver a capturar nada, la captura cruda sigue en _procesado/.
    slugs vacío = todas las entradas que quedaron pendientes."""
    root = cfg["hamuq"]
    quiere = set(slugs or [])
    devueltos = []
    for p in sorted((root / "07_Inbox/_procesado").glob("*.md")):
        meta = frontmatter.load(p).metadata
        slug = Path(str(meta.get("entrada") or "")).stem
        pedido = slug in quiere if quiere else bool(meta.get("pendiente"))
        if not slug or not pedido:
            continue
        destino = root / "07_Inbox" / p.name
        p.rename(destino)
        devueltos.append(destino)
    memoria.log_evento(root, "procesador", f"reproceso: {len(devueltos)} items al inbox")
    return _correr(cfg, devueltos)


def _correr(cfg: dict, pendientes: list[Path]) -> dict:
    root = cfg["hamuq"]
    procesadas, errores = [], []
    for p in pendientes:
        try:
            item = procesar_item(cfg, p)
            procesadas.append(item)
            memoria.log_evento(root, "procesador", f"{item['id']}: procesada")
        except Exception as e:
            post = frontmatter.load(p)
            post.metadata["estado"] = "error"
            post.metadata["error"] = f"{type(e).__name__}: {e}"
            p.write_text(frontmatter.dumps(post), encoding="utf-8")
            errores.append({"id": p.stem, "error": str(e)})
            memoria.log_evento(root, "procesador", f"{p.stem}: error — {e}")
    memoria.log_evento(root, "procesador", f"{len(procesadas)} procesadas, {len(errores)} con error")
    # las páginas de síntesis de los temas que acaban de recibir material se ponen
    # al día acá y no por captura: procesar el inbox ya es una operación batch
    tocados = list(dict.fromkeys(s for x in procesadas for s in (x.get("subjects") or [])))
    if tocados:
        try:
            sintesis.refrescar(cfg, tocados)   # cada regeneración ya se anota sola en el log
        except Exception as e:
            memoria.log_evento(root, "sintesis", f"refresco falló — {e}")
    return {"procesadas": len(procesadas), "errores": len(errores), "detalle_errores": errores,
            "pendientes": sum(1 for x in procesadas if x.get("pendiente"))}


def demo():
    """Autocheck sin red: extractor de HTML y de enlaces, que es lo frágil."""
    p = _ExtraeTexto()
    p.feed("<html><head><title>La página</title><style>.x{color:red}</style></head><body>"
           "<script>var x=1;</script><h1>Hola Mundo</h1><p>Texto real.</p></body></html>")
    texto = " ".join(p.partes)
    assert "Hola Mundo" in texto and "Texto real." in texto
    assert "color:red" not in texto and "var x=1" not in texto
    assert p.titulo == "La página"
    post = frontmatter.Post("ver https://a.test/x, y (https://b.test/y). otra vez https://a.test/x")
    assert _enlaces(post, {}) == ["https://a.test/x", "https://b.test/y"]
    print("procesar ok")


if __name__ == "__main__":
    demo()
