"""Acceso a la base hamuQ: buscar, leer, capturar al inbox, integrar y editar entradas.

Única vía de lectura/escritura sobre la base — la usan el chat, el CLI, el API
y el procesador nocturno. Todo se persiste como Markdown con frontmatter YAML
para que cualquier agente (Hermes, Claude, LM Studio) entienda la memoria sin
este código. Nada se borra destructivamente: papelera y registros fechados.
"""
import re
import secrets
import unicodedata
from datetime import date, datetime
from pathlib import Path

import frontmatter

INDICES = [
    "00_INDICE_GENERAL.md",
    "08_Categorias/00_CATEGORIAS.md",
    "06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md",
]
ENTRADAS = "06_Biblioteca_Conocimiento/Entradas"
ADJUNTOS_PAPELERA = "07_Inbox/_adjuntos/_papelera"   # subcarpeta: el lint solo mira archivos sueltos
MAX_PAGINA_CHARS = 20_000
NO_TEXTO = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".pdf",
            ".mp4", ".mov", ".webm", ".mp3", ".m4a", ".wav", ".ogg")
RX_NEGRITA = re.compile(r"\*\*(.+?)\*\*")
# Un medio también queda nombrado cuando una página lo MUESTRA en su cuerpo.
# Es lo único que sostiene a lo que Diego sube dentro de una sesión: eso vive
# como ![x](/attach/…) en el diálogo y nunca tiene un frontmatter `adjunto`
# propio (una sesión puede traer varios; el campo es uno solo).
RX_ATTACH_MD = re.compile(r"!\[[^\]]*\]\(/attach/([^)\s\"']+)")
MEDIA_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic",
             ".mp4", ".webm", ".mov", ".m4v",
             ".mp3", ".wav", ".ogg", ".m4a", ".opus", ".flac", ".weba")
# de qué sesión viene un adjunto suelto: su .md real en 10_Sesiones o el espejo
# que sincronizar_sesion_inbox escribe en 07_Inbox — cualquiera de los dos alcanza.
RX_SESION_PAGE = re.compile(r"^(?:10_Sesiones/|07_Inbox/sesion-)([\w-]+)\.md$")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def nucleo(root: Path) -> str:
    p = root / "09_Sistema/NUCLEO.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def buscar(root: Path, consulta: str, max_lineas: int = 30, proyecto: str | None = None) -> str:
    """Busca en los índices Y en el cuerpo de las entradas; devuelve líneas
    candidatas. El formato de cada hit es CONTRATO: chat._retrieval_scripted
    extrae los `(....md)` con regex para abrir las páginas.

    Con el índice derivado (indice.py) las entradas se rankean por búsqueda
    híbrida (BM25 + embeddings + RRF): "auto" encuentra la memoria del coche.
    Sin índice —FTS5 ausente, mem.db irrecuperable— cae al camino naive de
    siempre, que también es la referencia del formato.

    `proyecto` = desde dónde se busca; lo privado de OTRO proyecto no aparece
    (accesible). Los índices temáticos también se filtran: sus líneas llevan el
    título y el link de la entrada.
    """
    ocultas = entradas_ocultas(root, proyecto)
    hibridos = []
    try:
        from . import indice
        hibridos = indice.buscar_hibrida(root, consulta, k=max_lineas)
    except Exception as e:
        log_evento(root, "indice", f"búsqueda híbrida falló: {e}")
    if not hibridos:
        return _buscar_naive(root, consulta, max_lineas, ocultas) + _nota_lexica(root)
    terminos = [_norm(t) for t in re.findall(r"\w{3,}", consulta, re.UNICODE)]
    hits = []
    for slug, _ in hibridos:
        p = root / ENTRADAS / f"{slug}.md"
        if not p.is_file() or slug in ocultas:
            continue
        post = frontmatter.load(p)
        titulo = str(post.metadata.get("titulo", slug))
        adj = str(post.metadata.get("adjunto") or "")
        medio = f" · medio: /attach/{adj}" if adj else ""
        lineas = [(sum(1 for t in terminos if t in _norm(li)), li.strip())
                  for li in post.content.splitlines() if li.strip() and not li.startswith("#")]
        mejor = max(lineas, key=lambda x: x[0]) if lineas else (0, "")
        # hit puramente semántico: ninguna línea matchea literal, el resumen orienta más
        extracto = mejor[1] if mejor[0] else resumen_corto(post.content)
        hits.append(f"- [{titulo}](Entradas/{slug}.md){medio} — {extracto[:200]}")
    for rel in INDICES:   # las líneas de índice siguen aportando contexto de árbol
        p = root / rel
        if not p.exists():
            continue
        for i, linea in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _oculta_la_linea(linea, ocultas):
                continue
            if sum(1 for t in terminos if t in _norm(linea)) >= max(2, len(terminos) // 2):
                hits.append(f"{rel}:{i}: {linea.strip()}")
    return ("\n".join(hits[:max_lineas]) or "(sin resultados en índices; probar grep)") + _nota_lexica(root)


def _nota_lexica(root: Path) -> str:
    """Línea final de buscar() cuando la búsqueda degradó a solo-BM25/naive: sin
    esto la degradación es invisible ("auto" deja de encontrar "coche" y nadie
    se entera). El motivo nunca lleva rutas ni ".md" (contrato del regex del chat)."""
    try:
        from . import indice
        e = indice.estado(root)
        return "" if e["hibrida"] else f"\n(búsqueda solo léxica: {e['motivo']})"
    except Exception:
        return ""


def _oculta_la_linea(linea: str, ocultas: set[str]) -> bool:
    """Una línea de índice temático nombra su entrada: `- [Título](Entradas/x.md)`.
    Si esa entrada no se ve desde acá, la línea tampoco (delataría el título)."""
    return any(f"{s}.md" in linea for s in ocultas)


def _buscar_naive(root: Path, consulta: str, max_lineas: int = 30,
                  ocultas: set[str] | None = None) -> str:
    """Camino sin índice derivado: conteo de términos por línea. Es el fallback
    permanente de buscar() y la referencia de su formato de salida."""
    terminos = [_norm(t) for t in re.findall(r"\w{3,}", consulta, re.UNICODE)]
    if not terminos:
        return "(consulta vacía)"
    ocultas = ocultas or set()
    hits = []
    for rel in INDICES:
        p = root / rel
        if not p.exists():
            continue
        for i, linea in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            n = _norm(linea)
            score = sum(1 for t in terminos if t in n)
            if score and not _oculta_la_linea(linea, ocultas):
                hits.append((score, f"{rel}:{i}: {linea.strip()}"))
    for p in sorted((root / ENTRADAS).glob("*.md")):
        if p.stem in ocultas:
            continue
        post = frontmatter.load(p)
        titulo = str(post.metadata.get("titulo", p.stem))
        # Si la entrada ES un medio, el hit lleva su ruta de /attach: sin ella el
        # modelo solo tenía el .md y escribía ![titulo](Entradas/x.md), que no es
        # una imagen que se pueda mostrar — el chat lo pintaba como texto pelado
        # (visto 2026-08-06 al preguntar "qué imágenes tengo del cohete").
        adj = str(post.metadata.get("adjunto") or "")
        medio = f" · medio: /attach/{adj}" if adj else ""
        propios = []
        for linea in post.content.splitlines():
            n = _norm(linea)
            score = sum(1 for t in terminos if t in n)
            if score and linea.strip() and not linea.startswith("#"):
                propios.append((score, f"- [{titulo}](Entradas/{p.stem}.md){medio} — {linea.strip()[:200]}"))
        propios.sort(key=lambda x: -x[0])
        hits += propios[:2]   # 2 líneas por entrada: el link es el mismo, más solo repite
    hits.sort(key=lambda x: -x[0])
    return "\n".join(h for _, h in hits[:max_lineas]) or "(sin resultados en índices; probar grep)"


def leer_pagina(root: Path, rel: str, parte: int = 1, proyecto: str | None = None) -> str:
    rel = rel.strip().lstrip("/").replace("\\", "/")
    ocultos = paths_ocultos(root, proyecto)
    # los índices usan links relativos a su carpeta; probar también desde la Biblioteca
    for candidato in (rel, f"06_Biblioteca_Conocimiento/{rel}"):
        p = (root / candidato).resolve()
        if root.resolve() not in p.parents:
            return "(path fuera de la base)"
        if p.is_file():
            if p.relative_to(root.resolve()).as_posix() in ocultos:
                return "(privada: solo se lee desde su propio proyecto)"
            if p.suffix.lower() in NO_TEXTO:
                # leerlo como texto da bytes ilegibles: se devuelve el markdown de
                # /attach y quien llame decide (imagen inline en MCP, <img> en la PWA)
                ruta = p.relative_to(root.resolve()).as_posix()
                return f"(adjunto {p.suffix.lstrip('.')}, no es texto)\n\n![{p.name}](/attach/{ruta})"
            texto = p.read_text(encoding="utf-8", errors="replace")
            if len(texto) <= MAX_PAGINA_CHARS:
                return texto
            # página larga (transcripciones de adjuntos): ventanas fijas con aviso,
            # en vez del corte silencioso de antes — el agente decide si sigue
            n = -(-len(texto) // MAX_PAGINA_CHARS)   # ceil sin math
            if not 1 <= parte <= n:
                return f"(parte {parte} fuera de rango: la página tiene {n} partes)"
            trozo = texto[(parte - 1) * MAX_PAGINA_CHARS: parte * MAX_PAGINA_CHARS]
            sigue = f"leer_pagina con parte={parte + 1} para seguir" if parte < n else "fin"
            return f"{trozo}\n\n(página larga: parte {parte}/{n}; {sigue})"
    return f"(no existe: {rel})"


def grep(root: Path, patron: str, max_hits: int = 40, proyecto: str | None = None) -> str:
    try:
        rx = re.compile(patron, re.IGNORECASE)
    except re.error:
        rx = re.compile(re.escape(patron), re.IGNORECASE)
    ocultos = paths_ocultos(root, proyecto)
    nombres = {o.rsplit("/", 1)[-1][:-3] for o in ocultos}   # los índices las nombran
    hits = []
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(("10_Sesiones/", "07_Inbox/_papelera/",
                           "06_Biblioteca_Conocimiento/_papelera/",
                           f"{ENTRADAS}/_versiones/")):  # historiales, papelera y versiones no son base
            continue
        if rel in ocultos:   # privada de otro proyecto: ni una línea suya sale
            continue
        try:
            for i, linea in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(linea) and not _oculta_la_linea(linea, nombres):
                    hits.append(f"{rel}:{i}: {linea.strip()[:200]}")
                    if len(hits) >= max_hits:
                        return "\n".join(hits)
        except OSError:
            continue
    return "\n".join(hits) or "(sin resultados)"


def slugificar(texto: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", _norm(texto)).strip("-")
    return s[:60] or "entrada"


VERSIONES = f"{ENTRADAS}/_versiones"


def nuevo_id() -> str:
    """Identidad estable de una memoria, independiente del título y del slug.

    Fecha delante para que ordene solo; los 4 hex evitan choques cuando el
    procesador crea varias entradas en el mismo segundo."""
    return f"{datetime.now():%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


def _versionar(root: Path, p: Path, post) -> None:
    """Guarda la versión viva como vNNN.md antes de pisarla y bumpa `version`.

    La copia es byte a byte del archivo en disco (todavía sin la edición), así
    que cada versión es la memoria entera tal como estaba. El enlace con la
    anterior es el número: vN viene de v(N-1). Las versiones viven fuera de
    Entradas/*.md — el glob de los lectores es no recursivo, así que buscar,
    los índices y las conexiones siguen viendo solo la última.
    """
    iid = str(post.metadata.get("id") or "") or nuevo_id()   # entrada vieja sin id: se lo gana al editarse
    post.metadata["id"] = iid
    n = int(post.metadata.get("version") or 1)
    d = root / VERSIONES / iid
    d.mkdir(parents=True, exist_ok=True)
    (d / f"v{n:03}.md").write_bytes(p.read_bytes())
    post.metadata["version"] = n + 1


RX_LOG = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}:\d{2}))?\]")


def _archivar_log_viejo(root: Path, p: Path) -> None:
    """Si el log activo quedó de un día anterior, lo archiva por fecha antes de
    seguir escribiendo — así log.md nunca crece sin límite y queda organizado
    por día (09_Sistema/Log_Archivo/AAAA-MM-DD.md)."""
    if not p.exists():
        return
    dia = date.fromtimestamp(p.stat().st_mtime)
    if dia >= date.today():
        return
    destino = root / "09_Sistema/Log_Archivo"
    destino.mkdir(parents=True, exist_ok=True)
    p.rename(destino / f"{dia.isoformat()}.md")


def log_evento(root: Path, actor: str, detalle: str) -> None:
    p = root / "09_Sistema/log.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    _archivar_log_viejo(root, p)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"\n## [{datetime.now().isoformat(sep=' ', timespec='seconds')}] {actor} | {detalle}\n")


def leer_log(root: Path, desde: datetime) -> list[str]:
    """Líneas de log (activo + archivado) con timestamp >= desde, en orden
    cronológico. Entradas viejas sin hora (formato previo) cuentan como 00:00."""
    dir_arch = root / "09_Sistema/Log_Archivo"
    archivos = []
    if dir_arch.is_dir():
        archivos = [p for p in sorted(dir_arch.glob("*.md")) if p.stem >= desde.date().isoformat()]
    archivos.append(root / "09_Sistema/log.md")
    out = []
    for p in archivos:
        if not p.exists():
            continue
        for linea in p.read_text(encoding="utf-8").splitlines():
            m = RX_LOG.match(linea)
            if not m:
                continue
            ts = datetime.fromisoformat(f"{m.group(1)} {m.group(2) or '00:00:00'}")
            if ts >= desde:
                out.append(linea)
    return out


# ---------------------------------------------------------------- captura / inbox

def proyectos_de(subjects: list[str] | None) -> list[str]:
    """Los proyectos de algo, leídos de sus subjects: `Proyectos/<n>` y todo lo
    que cuelgue de él. Vacío = no es de ningún proyecto ("Sin proyecto")."""
    return [m.group(1) for s in (subjects or [])
            if (m := re.match(rf"{GRUPO_PROYECTOS}/([^/]+)", str(s)))]


def _privados(root: Path) -> set[str]:
    return {_norm(p["nombre"]) for p in proyectos_listar(root) if p["ambito"] == "privado"}


def hereda_privado(root: Path, subjects: list[str] | None) -> bool:
    """¿Nace privado lo que se guarda bajo estos subjects? (pedido 2026-08-08)

    Lo privado dejó de ser una propiedad del proyecto que se leía en cada
    consulta: es un campo PROPIO de la memoria (`privada`), y esto lo resuelve
    UNA sola vez, al crearla. Después, cambiarle el ámbito al proyecto ya no
    toca a las memorias que salieron de él, y la casilla de la ficha manda.
    """
    privados = _privados(root)
    return bool(privados) and any(_norm(n) in privados for n in proyectos_de(subjects))


def accesible(meta: dict, proyecto: str | None) -> bool:
    """¿Se ve esta memoria (o captura) estando parado en `proyecto`?

    Regla (pedido 2026-08-12): los proyectos ordenan, no encierran — desde
    cualquiera se llega a todo... salvo lo privado, que NO sale del suyo: ni a
    una búsqueda, ni a la galería, ni al contexto de un chat de otro proyecto.

    `proyecto=None` = quien pregunta no está parado en ninguno (MCP, CLI, un
    shell viejo): no hay "otro proyecto" desde el cual filtrar y no se filtra.
    "Sin proyecto" ("") ES un proyecto: una memoria privada sin proyecto se ve
    solo desde ahí, que es donde nació.
    """
    if proyecto is None or not meta.get("privada"):
        return True
    suyos = [_norm(n) for n in proyectos_de(meta.get("subjects"))]
    return _norm(proyecto) in suyos if suyos else not proyecto


def entradas_ocultas(root: Path, proyecto: str | None) -> set[str]:
    """Slugs que no se pueden ver desde `proyecto`. Para los llamadores que
    trabajan con slugs sueltos (índice, grafo); el que ya tiene el frontmatter
    en la mano usa accesible() y no paga este barrido.

    ponytail: relee el frontmatter de la Biblioteca entera. Con ~10³ entradas
    son decenas de ms; si molesta, la columna `privada` en mem.db lo mata.
    """
    d = root / ENTRADAS
    if proyecto is None or not d.exists():
        return set()
    return {p.stem for p in d.glob("*.md")
            if not accesible(frontmatter.load(p).metadata, proyecto)}


def paths_ocultos(root: Path, proyecto: str | None) -> set[str]:
    """Lo mismo en rutas relativas y sumando el inbox — grep y leer_pagina
    trabajan con rutas, y una captura pendiente de un proyecto privado es tan
    privada como la memoria en que se va a convertir."""
    out = {f"{ENTRADAS}/{s}.md" for s in entradas_ocultas(root, proyecto)}
    inbox = root / "07_Inbox"
    if proyecto is None or not inbox.exists():
        return out
    return out | {f"07_Inbox/{p.name}" for p in inbox.glob("*.md")
                  if not accesible(frontmatter.load(p).metadata, proyecto)}


def migrar_privadas(root: Path) -> int:
    """Siembra `privada` en las entradas que ya existían cuando lo privado se
    heredaba del proyecto. Solo toca las que NO tienen el campo: una memoria que
    Diego destildó a mano guarda `privada: false` y nunca se vuelve a marcar.

    Idempotente: la segunda corrida no escribe nada. Corre al arrancar el server.
    """
    entradas = root / ENTRADAS
    if not entradas.exists():
        return 0
    n = 0
    for p in sorted(entradas.glob("*.md")):
        post = frontmatter.load(p)
        if "privada" in post.metadata or not hereda_privado(root, post.metadata.get("subjects")):
            continue
        post.metadata["privada"] = True   # sin tocar `actualizada`: no es una edición
        p.write_text(frontmatter.dumps(post), encoding="utf-8")
        n += 1
    if n:
        log_evento(root, "privado", f"{n} memoria(s) marcadas privadas (heredado del proyecto)")
    return n


def capturar(root: Path, contenido: str, tipo: str = "nota", contexto: str = "",
             tags: list[str] | None = None, subjects: list[str] | None = None,
             adjunto: str = "", origen: str = "movil", fijar: bool = True,
             coords: str = "", proyecto: str = "", capturado: str = "",
             extra: dict | None = None) -> str:
    """Escribe una captura al inbox. No procesa nada — cero pérdida primero.

    Lo que el usuario fija a mano (tags/subjects) queda en `fijado`: el
    procesador nocturno no lo sobreescribe (spec §3.3). fijar=False cuando los
    tags/subjects vienen de un pase automático (el procesador puede mejorarlos).

    `coords` ("lat,lon") = dónde estaba Diego al capturar, si el browser lo dio.
    Es el par espacial de `capturado`: `lugar` es de qué habla la info, esto es
    desde dónde se anotó. El procesador le pone nombre (indice.lugar_de_coords).

    `proyecto` = dónde estaba parado al capturar (pedido 2026-08-12): la memoria
    que salga de acá nace dentro de ese proyecto. Viaja como campo (procesar.py
    lo re-inyecta al promover) y como subject (para que el candado lo vea ya).

    `capturado`/`extra` los usa el import de un backup (exportar.py): la memoria
    vuelve con SU fecha, no con la de hoy, y con los campos que traía (`fijado`,
    `privada`, `id_origen`). Sin el `fijado` original, los tags que en su momento
    puso el LLM entrarían como fijados y congelarían una clasificación que nadie
    eligió.
    """
    ahora = datetime.fromisoformat(capturado).astimezone() if capturado else datetime.now().astimezone()
    carpeta = root / "07_Inbox"
    carpeta.mkdir(parents=True, exist_ok=True)
    # El id es fecha+segundo+slug: dos capturas del mismo segundo con texto
    # parecido caían en el MISMO archivo y la segunda pisaba a la primera, sin
    # aviso. Cero pérdida (spec §8.1) manda desempatar.
    base = iid = f"{ahora:%Y-%m-%d_%H%M%S}_{slugificar(contenido[:40])}"
    n = 1
    while (p := carpeta / f"{iid}.md").exists():
        n += 1
        iid = f"{base}-{n}"
    # el subject del proyecto se suma DESPUÉS de calcular `fijado`: fijarlo
    # congelaría también la clasificación que el procesador todavía no hizo.
    subs = list(subjects or [])
    if proyecto:
        subs = list(dict.fromkeys([*subs, subject_proyecto(proyecto)]))
    post = frontmatter.Post(
        contenido.strip() + "\n", id=iid,
        capturado=ahora.isoformat(timespec="seconds"), tipo=tipo, origen=origen,
        adjunto=adjunto, contexto_usuario=contexto,
        tags=list(tags or []), subjects=subs,
        fijado=[c for c, v in (("tags", tags), ("subjects", subjects)) if v] if fijar else [],
        estado="pendiente",
        # solo si el browser dio permiso: la clave no existe cuando no hay geo
        **({"coords_captura": coords} if coords else {}),
        **({"proyecto": proyecto} if proyecto else {}),
        # capturado desde un proyecto privado → el item ya nace privado y no se
        # muestra en el celu sin verificar, todavía sin procesar
        **({"privada": True} if hereda_privado(root, subs) else {}),
    )
    if extra:
        post.metadata.update(extra)
    p.write_text(frontmatter.dumps(post), encoding="utf-8")
    return f"07_Inbox/{iid}.md"


def _inbox_item(p: Path) -> dict:
    post = frontmatter.load(p)
    return {**dict(post.metadata), "id": p.stem, "texto": post.content.strip()}


def inbox_listar(root: Path) -> list[dict]:
    """Lo que sigue en el inbox: pendientes y errores. Lo procesado SALE del
    inbox — vive en la Biblioteca y se consulta desde Memory."""
    return [_inbox_item(p) for p in sorted((root / "07_Inbox").glob("*.md"), reverse=True)]


def inbox_editar(root: Path, iid: str, texto: str | None = None,
                 tags: list[str] | None = None, subjects: list[str] | None = None) -> dict:
    """Edita un item pendiente; los campos tocados quedan fijados (el usuario manda)."""
    p = root / "07_Inbox" / f"{iid}.md"
    if not p.is_file():
        raise FileNotFoundError(f"item no encontrado en el inbox: {iid}")
    post = frontmatter.load(p)
    fijado = list(post.metadata.get("fijado") or [])
    if texto is not None:
        post.content = texto.strip() + "\n"
        fijado.append("texto")
    for campo, valor in (("tags", tags), ("subjects", subjects)):
        if valor is not None:
            post.metadata[campo] = list(valor)
            fijado.append(campo)
    post.metadata["fijado"] = list(dict.fromkeys(fijado))
    p.write_text(frontmatter.dumps(post), encoding="utf-8")
    return _inbox_item(p)


def adjuntos_referidos(root: Path) -> dict[str, list[str]]:
    """Qué páginas nombran a cada archivo de 07_Inbox/_adjuntos/: su frontmatter
    `adjunto` (la página lo posee) o un ![x](/attach/…) en el cuerpo (la página
    lo muestra). Lo comparten el lint —que mira los dos lados del vínculo— y el
    borrado, que necesita saber si la página que se va era la última que lo
    nombraba. Definición única: dos versiones de "qué cuenta como referencia" es
    justo cómo vuelve este bug.

    Las papeleras no cuentan: lo borrado no sostiene a un archivo vivo. Una
    mención colgada tampoco cuenta —solo suma si el archivo existe—: una sesión
    vieja que enseñaba un medio ya borrado es historia, no un vínculo roto que
    alguien tenga que ir a arreglar.

    ponytail: rglob + frontmatter en cada .md, como el resto del módulo; si esto
    pasa a FTS5, esto también.
    """
    root = Path(root)
    out: dict[str, list[str]] = {}
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if "_papelera/" in rel or "_versiones/" in rel:
            continue
        post = frontmatter.load(p)
        nombrados = [str(post.metadata.get("adjunto") or "")]
        nombrados += [m for m in RX_ATTACH_MD.findall(post.content)
                      if (root / m.lstrip("/")).is_file()]
        for adj in dict.fromkeys(x.strip().lstrip("/") for x in nombrados):
            if adj:
                out.setdefault(adj, []).append(rel)
    return out


def adjuntos_sueltos_de(root: Path, sid: str, rutas: list[str]) -> list[str]:
    """De los medios que aparecen en una sesión, los que quedarían sin nadie que
    los nombre si esa sesión se va: lo que DELETE /sessions/{sid}?contenido=1
    manda a la papelera.

    Las páginas de la propia sesión —su .md y su item de inbox `sesion-{sid}`—
    no cuentan como quien lo sostiene. Ahora que mostrar un medio en el cuerpo
    ya es referirlo (adjuntos_referidos), si contaran, lo que Diego subió a la
    sesión no se iría nunca con ella."""
    referidos = adjuntos_referidos(root)
    propia = lambda pagina: pagina.endswith(f"{sid}.md")   # noqa: E731
    return [r for r in rutas if not [x for x in referidos.get(r, []) if not propia(x)]]


def soltar_adjunto(root: Path, pagina: Path) -> str:
    """El archivo se va a la papelera CON la página que lo guardaba. Si no, queda
    para siempre en _adjuntos/ sin nada que lo nombre —tres avisos así en la base
    de Diego (2026-08-07), y un video pesa lo que pesa. `pagina` es la página ya
    movida a su papelera: se le reescribe la ruta para que el par siga junto.

    Solo si era la última que lo nombraba: un adjunto procesado lo comparten el
    item del inbox y la entrada que salió de él, y borrar uno no puede romperle
    la imagen al otro."""
    post = frontmatter.load(pagina)
    adj = str(post.metadata.get("adjunto") or "").strip().lstrip("/")
    if not adj:
        return ""
    f = root / adj
    if adjuntos_referidos(root).get(adj) or not f.is_file():
        return ""
    destino = root / ADJUNTOS_PAPELERA
    destino.mkdir(parents=True, exist_ok=True)
    f.replace(destino / f.name)   # replace y no rename: un nombre repetido no revienta un borrado
    post.metadata["adjunto"] = f"{ADJUNTOS_PAPELERA}/{f.name}"
    pagina.write_text(frontmatter.dumps(post), encoding="utf-8")
    return f"{ADJUNTOS_PAPELERA}/{f.name}"


def papelera_adjunto_suelto(root: Path, ruta: str) -> str:
    """Manda a su papelera un archivo huérfano de 07_Inbox/_adjuntos/ (`mem lint`:
    'medio sin entrada' — nada lo referencia). `ruta` tiene que vivir plana ahí
    adentro: sin eso, no hay forma de que este endpoint mueva otra cosa del vault."""
    ruta = ruta.strip().lstrip("/")
    base = "07_Inbox/_adjuntos"
    if not ruta.startswith(f"{base}/") or "/" in ruta[len(base) + 1:]:
        raise ValueError(f"ruta inválida para papelera de adjuntos sueltos: {ruta}")
    f = root / ruta
    if not f.is_file():
        raise FileNotFoundError(f"adjunto no encontrado: {ruta}")
    destino = root / ADJUNTOS_PAPELERA
    destino.mkdir(parents=True, exist_ok=True)
    f.replace(destino / f.name)
    log_evento(root, "adjunto", f"huérfano a papelera: {ruta}")
    return f"{ADJUNTOS_PAPELERA}/{f.name}"


def inbox_eliminar(root: Path, iid: str) -> str:
    """'Eliminar' = mover a papelera; nunca borrado destructivo (spec §8.3)."""
    p = root / "07_Inbox" / f"{iid}.md"
    if not p.is_file():
        raise FileNotFoundError(f"item no encontrado en el inbox: {iid}")
    destino = root / "07_Inbox/_papelera"
    destino.mkdir(parents=True, exist_ok=True)
    p.rename(destino / p.name)
    soltar_adjunto(root, destino / p.name)
    return f"07_Inbox/_papelera/{p.name}"


def sincronizar_sesion_inbox(root: Path, sid: str, contenido: str, subjects: list[str] | None = None,
                             proyecto: str = "") -> str:
    """Refleja una sesión de chat como UN item del inbox (id fijo `sesion-{sid}`),
    reescrito en el mismo archivo en cada cambio — no una captura por mensaje.
    La llama chat.turno en cada mensaje.

    Si el item ya fue procesado (vive en 07_Inbox/_procesado/), lo reabre ANTES
    de reescribirlo: "se modificó algo en la sesión" también actualiza su
    procesamiento — el próximo procesar_inbox lo retoma y cae en la MISMA
    entrada de la Biblioteca (guardar_entrada dedupea por slug vía `entrada`).

    subjects nunca queda fijado acá: es contenido que se genera solo, no algo
    que Diego tipeó a mano — el procesador lo puede reclasificar como a
    cualquier captura. Si Diego SÍ lo editó desde el Inbox (inbox_editar fija
    "subjects"), esta función deja ese campo en paz.
    """
    ahora = datetime.now().astimezone().isoformat(timespec="seconds")
    carpeta = root / "07_Inbox"
    carpeta.mkdir(parents=True, exist_ok=True)
    iid = f"sesion-{sid}"
    p = carpeta / f"{iid}.md"
    if not p.is_file():
        procesado = root / "07_Inbox/_procesado" / f"{iid}.md"
        if procesado.is_file():
            procesado.rename(p)

    subjects = list(subjects or [])
    if proyecto:
        subjects = list(dict.fromkeys([*subjects, subject_proyecto(proyecto)]))
    privada = hereda_privado(root, subjects)

    if p.is_file():
        post = frontmatter.load(p)
        post.content = contenido.strip() + "\n"
        post.metadata.update(tipo="sesion", estado="pendiente", proyecto=proyecto, capturado=ahora)
        post.metadata.pop("error", None)
        if "subjects" not in set(post.metadata.get("fijado") or []):
            post.metadata["subjects"] = subjects
        if privada and not post.metadata.get("privada"):
            post.metadata["privada"] = True
        p.write_text(frontmatter.dumps(post), encoding="utf-8")
        return f"07_Inbox/{iid}.md"

    post = frontmatter.Post(
        contenido.strip() + "\n", id=iid, sesion=sid, proyecto=proyecto, capturado=ahora,
        tipo="sesion", origen="chat", adjunto="", contexto_usuario="",
        tags=[], subjects=subjects, fijado=[], estado="pendiente",
        **({"privada": True} if privada else {}),
    )
    p.write_text(frontmatter.dumps(post), encoding="utf-8")
    return f"07_Inbox/{iid}.md"


def estado_sesion_inbox(root: Path, sid: str) -> str:
    """Estado del item de inbox de una sesión (pendiente/procesada/error), o ''
    si todavía no se sincronizó nada — lo consulta GET /sessions/{sid}."""
    p = root / "07_Inbox" / f"sesion-{sid}.md"
    if p.is_file():
        return str(frontmatter.load(p).metadata.get("estado") or "pendiente")
    if (root / "07_Inbox/_procesado" / f"sesion-{sid}.md").is_file():
        return "procesada"
    return ""


# ---------------------------------------------------------------- entradas (Memory)

# El modelo escribe la jerarquía de mil formas ("Tecnología > IA", "Tecnología |
# IA"), pero el árbol solo entiende "/". Sin normalizar, "Tecnología > IA" es un
# subject de un solo nivel y termina como grupo suelto de primer nivel en
# 00_CATEGORIAS.md, al lado de Tecnología y sin hijos.
RX_SEP = re.compile(r"\s*(?:[>›»/\\|]|::|--)\s*")


def normalizar_subject(s: str) -> str:
    return "/".join(x for x in RX_SEP.split(" ".join(str(s).split())) if x)


def normalizar_subjects(lista) -> list[str]:
    return list(dict.fromkeys(x for x in (normalizar_subject(s) for s in (lista or [])) if x))


CAB_ADJUNTO = "## Contenido del adjunto"
CAB_REGISTRO = "## Registro histórico"


def _poner_seccion(md: str, cabecera: str, texto: str, antes: str) -> str:
    """Escribe (o reescribe) una sección del cuerpo, delante de `antes`. Reescribir
    y no acumular: un reproceso mejor del mismo adjunto reemplaza al anterior, no
    deja dos transcripciones contradictorias de la misma foto."""
    if not texto.strip():
        return md
    bloque = f"{cabecera}\n{texto.strip()}\n\n"
    if cabecera in md:
        i = md.index(cabecera)
        j = md.index(antes, i) if antes in md[i:] else len(md)
        return md[:i] + bloque + md[j:]
    return md.replace(antes, bloque + antes, 1) if antes in md else md.rstrip() + "\n\n" + bloque


def _indice_hook(root: Path) -> None:
    """Aviso al índice derivado de búsqueda (indice.py) tras cada escritura.
    Best-effort: la escritura del vault ya está hecha; si el índice falla, se
    anota en el log y la próxima búsqueda lo repara con su sincronizar()."""
    try:
        from . import indice
        indice.sincronizar(root, forzar=True)   # lo recién escrito se ve ya, sin debounce
    except Exception as e:
        try:
            log_evento(root, "indice", f"sync falló: {e}")
        except Exception:
            pass


def guardar_entrada(root: Path, titulo: str, contenido: str, subjects: list[str],
                    origen: str = "chat", tags: list[str] | None = None, adjunto: str = "",
                    meta: dict | None = None, slug: str = "", transcripcion: str = "",
                    reemplazar: bool = False, privada: bool | None = None) -> str:
    """Crea o actualiza una entrada de la Biblioteca y sus índices. Dedupe por slug.

    `meta` = metadatos extraídos al procesar (lugar, cuando, enlaces, capturado…);
    van al frontmatter para poder buscar por ellos. Al actualizar, las listas se
    unen y los escalares ya presentes no se pisan.

    `slug` fuerza a QUÉ entrada escribir. Lo usa el reproceso, donde el item ya
    sabe qué entrada creó: sin esto, un título distinto del modelo abriría una
    entrada gemela y la original quedaría pendiente para siempre.

    `transcripcion` = el texto de un adjunto que NO se puede leer de otra forma
    (lo que se ve en una foto o video, lo que se oye en un audio, el texto de un
    PDF). Va literal al cuerpo: es la única copia y es lo que hace que la memoria
    se pueda encontrar por su contenido y no solo por el nombre del archivo.

    `reemplazar` (reproceso): el título y el resumen nuevos pisan a los viejos.
    El registro histórico nunca se toca — eso es historia.

    `privada` (None = deducir del proyecto de sus subjects) se escribe SOLO al
    crear: es un campo propio de la memoria y a partir de ahí lo manda la
    casilla de la ficha, no el ámbito del proyecto.
    """
    hoy = date.today().isoformat()
    subjects = normalizar_subjects(subjects)
    slug = slug or slugificar(titulo)
    entradas = root / ENTRADAS
    entradas.mkdir(parents=True, exist_ok=True)
    p = entradas / f"{slug}.md"
    extra = {k: v for k, v in (meta or {}).items() if v}
    if p.exists():
        post = frontmatter.load(p)
        _versionar(root, p, post)   # todo lo que sigue toca el cuerpo: la anterior queda guardada
        post.metadata["actualizada"] = hoy
        # una captura privada que se suma a una entrada ya existente la vuelve
        # privada; al revés no: destildar es una decisión del usuario y se respeta
        if privada and not post.metadata.get("privada"):
            post.metadata["privada"] = True
        if tags:
            post.metadata["tags"] = list(dict.fromkeys(list(post.metadata.get("tags") or []) + list(tags)))
        for k, v in extra.items():
            previo = post.metadata.get(k)
            if isinstance(v, list):
                post.metadata[k] = list(dict.fromkeys([*(previo or []), *v]))
            elif not previo:
                post.metadata[k] = v
        # una entrada que crece hacia otro tema se reindexa bajo el subject nuevo
        nuevos = [s for s in subjects if s not in (post.metadata.get("subjects") or [])]
        if nuevos:
            post.metadata["subjects"] = list(post.metadata.get("subjects") or []) + nuevos
        if reemplazar:
            previo = str(post.metadata.get("titulo") or slug)
            previos_subj = list(post.metadata.get("subjects") or [])
            # los temas del pase anterior eran una conjetura sobre material que no
            # se pudo leer: unirlos a los nuevos llena el árbol de grupos sueltos.
            post.metadata["subjects"] = subjects or previos_subj
            post.metadata["titulo"] = titulo
            post.content = re.sub(r"\A# .*$", f"# {titulo}", post.content.lstrip(), count=1, flags=re.M)
            post.content = _poner_seccion(post.content, "## Resumen", contenido, CAB_ADJUNTO
                                          if CAB_ADJUNTO in post.content else CAB_REGISTRO)
            if previo != titulo or previos_subj != post.metadata["subjects"]:
                todos = list(post.metadata["subjects"])
                _desindexar(root, slug)   # el link viejo lleva el título y los temas viejos
                _retitular(root, slug, titulo)
                _indexar_tematico(root, titulo, slug, todos, hoy, cronologico=False)
                _indexar_categorias(root, titulo, slug, todos)
            nuevos = []
        post.content = _poner_seccion(post.content, CAB_ADJUNTO, transcripcion, CAB_REGISTRO)
        post.content += f"\n**{hoy}** ({origen}) — {contenido.strip()}\n"
        p.write_text(frontmatter.dumps(post), encoding="utf-8")
        if nuevos:
            titulo_previo = str(post.metadata.get("titulo", titulo))
            _indexar_tematico(root, titulo_previo, slug, nuevos, hoy, cronologico=False)
            _indexar_categorias(root, titulo_previo, slug, nuevos)
        _indice_hook(root)
        log_evento(root, "entrada", f"actualizada: {slug} (origen={origen})")
        return f"entrada actualizada: {ENTRADAS}/{slug}.md"
    adj_txt = f"{CAB_ADJUNTO}\n{transcripcion.strip()}\n\n" if transcripcion.strip() else ""
    cuerpo = (
        f"# {titulo}\n\n"
        f"## Resumen\n{contenido.strip()}\n\n"
        f"{adj_txt}"
        f"{CAB_REGISTRO}\n\n**{hoy}** ({origen}) — entrada creada.\n"
    )
    priv = hereda_privado(root, subjects) if privada is None else bool(privada)
    post = frontmatter.Post(cuerpo, titulo=titulo, creada=hoy, actualizada=hoy,
                            subjects=list(subjects), tags=list(tags or []), origen=origen,
                            adjunto=adjunto, id=nuevo_id(), version=1, **extra,
                            **({"privada": True} if priv else {}))
    p.write_text(frontmatter.dumps(post), encoding="utf-8")
    _indexar_tematico(root, titulo, slug, subjects, hoy)
    _indexar_categorias(root, titulo, slug, subjects)
    _indice_hook(root)
    log_evento(root, "entrada", f"creada: {slug} (origen={origen})")
    return f"entrada creada: {ENTRADAS}/{slug}.md"


def _entrada(root: Path, slug: str) -> Path:
    p = root / ENTRADAS / f"{slug}.md"
    if not p.is_file():
        raise FileNotFoundError(f"entrada no encontrada: {slug}")
    return p


def leer_entrada(root: Path, slug: str) -> dict:
    p = _entrada(root, slug)
    post = frontmatter.load(p)
    return {**dict(post.metadata), "slug": slug, "contenido": post.content}


def eliminar_entrada(root: Path, slug: str) -> str:
    """'Borrar' una entrada = desindexarla y moverla a la papelera de la
    Biblioteca; nunca borrado destructivo (spec §8.3). Lo usa el borrado de una
    sesión "con sus memorias".

    ponytail: sus versiones quedan en _versiones/<id>/ — nada destructivo, y como
    un slug reciclado estrena id nunca chocan."""
    p = _entrada(root, slug)
    _desindexar(root, slug)
    destino = root / "06_Biblioteca_Conocimiento/_papelera"
    destino.mkdir(parents=True, exist_ok=True)
    p.rename(destino / p.name)
    soltar_adjunto(root, destino / p.name)
    # la línea cronológica que _desindexar conserva se queda, pero sin link: el
    # archivo ya no está, y ese link roto salía en cada `mem lint` para siempre
    # (en la app, un clic al 404). Acá y no en _desindexar, que también corre al
    # reindexar una entrada que sigue viva.
    rx = re.compile(rf"\[([^\]]*)\]\((?:\.\./)?[\w/]*Entradas/{re.escape(slug)}\.md\)")
    for rel in INDICES:
        f = root / rel
        if f.exists():
            f.write_text(rx.sub(r"\1 (borrada)", f.read_text(encoding="utf-8")), encoding="utf-8")
    _indice_hook(root)
    log_evento(root, "entrada", f"borrada: {slug} → papelera")
    return f"06_Biblioteca_Conocimiento/_papelera/{p.name}"


def fusionar_entradas(root: Path, absorbe: str, absorbida: str) -> str:
    """Dos memorias casi idénticas (lint: 'posible duplicado') → una. `absorbida`
    se borra vía `eliminar_entrada` (papelera, reversible); `absorbe` se queda con
    la UNIÓN de tags/subjects/enlaces y el cuerpo entero de la absorbida anexado
    como actualización fechada — nada se pierde, se apila (mismo principio que el
    registro histórico del resto del módulo).

    El adjunto de la absorbida se hereda SOLO si `absorbe` no tenía uno propio:
    se lo apunta ANTES de borrar, así `eliminar_entrada` → `soltar_adjunto` ve que
    el archivo sigue nombrado por alguien vivo y no lo manda a la papelera.

    Los [[wikilinks]] de terceros hacia `absorbida` se reescriben a `absorbe` —
    si no, quedarían apuntando a un slug que ya no existe."""
    if absorbe == absorbida:
        raise ValueError("no se puede fusionar una entrada consigo misma")
    pa, pb = _entrada(root, absorbe), _entrada(root, absorbida)
    posta, postb = frontmatter.load(pa), frontmatter.load(pb)
    _versionar(root, pa, posta)
    hoy = date.today().isoformat()
    posta.metadata["tags"] = list(dict.fromkeys(
        [*(posta.metadata.get("tags") or []), *(postb.metadata.get("tags") or [])]))
    nuevos_subj = [s for s in (postb.metadata.get("subjects") or [])
                   if s not in (posta.metadata.get("subjects") or [])]
    if nuevos_subj:
        posta.metadata["subjects"] = list(posta.metadata.get("subjects") or []) + nuevos_subj
    nuevos_enl = [e for e in (postb.metadata.get("enlaces") or [])
                  if e not in (posta.metadata.get("enlaces") or [])]
    if nuevos_enl:
        posta.metadata["enlaces"] = list(posta.metadata.get("enlaces") or []) + nuevos_enl
    if not posta.metadata.get("adjunto") and postb.metadata.get("adjunto"):
        posta.metadata["adjunto"] = postb.metadata["adjunto"]
    posta.metadata["actualizada"] = hoy
    # `absorbida` en texto plano y NO como [[wikilink]]: para cuando se lee esta
    # línea la entrada ya está en la papelera (unas líneas más abajo) — un
    # wikilink a un slug que ya no existe es exactamente el "wikilink roto" que
    # el propio lint reporta (bug real, visto al fusionar la base de Diego).
    posta.content = (f"{posta.content.rstrip()}\n\n**{hoy}** (fusión) — combinada con `{absorbida}`. "
                     f"Contenido de esa entrada:\n\n{postb.content.strip()}\n")
    pa.write_text(frontmatter.dumps(posta), encoding="utf-8")
    if nuevos_subj:
        titulo = str(posta.metadata.get("titulo") or absorbe)
        _indexar_tematico(root, titulo, absorbe, nuevos_subj, hoy, cronologico=False)
        _indexar_categorias(root, titulo, absorbe, nuevos_subj)
    # terceros que citaban a la absorbida ahora citan a la que absorbe
    rx = re.compile(rf"\[\[{re.escape(absorbida)}(\|[^\]]*)?\]\]")
    for p in sorted((root / ENTRADAS).glob("*.md")):
        if p.stem in (absorbe, absorbida):
            continue
        texto = p.read_text(encoding="utf-8")
        nuevo = rx.sub(lambda m: f"[[{absorbe}{m.group(1) or ''}]]", texto)
        if nuevo != texto:
            p.write_text(nuevo, encoding="utf-8")
    eliminar_entrada(root, absorbida)   # papelera + desindexa + suelta el adjunto si ya no hace falta
    log_evento(root, "entrada", f"fusión: {absorbida} → {absorbe}")
    return f"fusionada: {absorbida} → {absorbe}"


def entradas_de_sesion(root: Path, sid: str) -> list[dict]:
    """Memorias que nacieron de una sesión (frontmatter `sesion`): las escribe
    guardar_entrada desde el chat, el catálogo de lo generado y el destilado.

    ponytail: scan lineal como buscar_memorias; si eso pasa a FTS5, esto también."""
    entradas = root / ENTRADAS
    if not sid or not entradas.exists():
        return []
    out = []
    for p in sorted(entradas.glob("*.md")):
        m = frontmatter.load(p).metadata
        if str(m.get("sesion") or "") == sid:
            out.append({"slug": p.stem, "titulo": str(m.get("titulo") or p.stem)})
    return out


def tags_existentes(root: Path, tope: int = 60) -> list[str]:
    """Los tags que ya se usan, del más frecuente al menos. El catalogador los
    recibe para reutilizarlos: sin esta lista cada pase inventaba los suyos y la
    base terminó con 125 tags para 45 entradas (`IA generativa` vs `ia-generativa`
    vs `imagen IA`…), o sea filtros que no filtran nada.

    ponytail: scan lineal como buscar_memorias; si eso pasa a FTS5, esto también."""
    entradas = root / ENTRADAS
    cuenta: dict[str, int] = {}
    for p in (sorted(entradas.glob("*.md")) if entradas.exists() else []):
        for t in frontmatter.load(p).metadata.get("tags") or []:
            if s := str(t).strip():
                cuenta[s] = cuenta.get(s, 0) + 1
    return [t for t, _ in sorted(cuenta.items(), key=lambda kv: (-kv[1], kv[0]))[:tope]]


def adjuntos_de_sesion_desde(root: Path, sid: str, desde: float) -> list[str]:
    """Adjuntos de las entradas de `sid` escritas después de `desde` (time.time()).

    Para el turno de claude_code, que genera por su MCP propio: el motor no ve la
    tool, así que pregunta por lo que apareció mientras respondía. El stat filtra
    antes de parsear — en un turno que no generó nada no abre ningún archivo.
    """
    entradas = root / ENTRADAS
    if not sid or not entradas.exists():
        return []
    out = []
    for p in sorted(entradas.glob("*.md")):
        if p.stat().st_mtime <= desde:
            continue
        m = frontmatter.load(p).metadata
        if str(m.get("sesion") or "") == sid and (a := str(m.get("adjunto") or "")):
            out.append(a)
    return out


def conexiones(root: Path, slug: str, proyecto: str | None = None) -> dict:
    """Conexiones de una entrada: sesiones que la citaron (paginas_usadas),
    hasta 5 entradas que comparten subject, los [[wikilinks]] en ambos sentidos
    y las `relacionadas` — vecinas por similitud semántica, con score. Las
    relacionadas son SUGERENCIAS: nada se enlaza sin confirmación del usuario
    (enlazar_entradas).

    Una vecina privada de otro proyecto no se nombra acá tampoco: la ficha es
    una puerta más (pedido 2026-08-12)."""
    ocultas = entradas_ocultas(root, proyecto)
    entrada = leer_entrada(root, slug)
    subjects = set(entrada.get("subjects") or [])
    marcas = (f"Entradas/{slug}.md", f"{ENTRADAS}/{slug}.md")

    sesiones_rel = []
    ses_dir = root / "10_Sesiones"
    if ses_dir.exists():
        for p in list(ses_dir.glob("*.md")) + list(ses_dir.glob("_archivo/*/*.md")):
            meta = frontmatter.load(p).metadata
            paginas = [str(pg) for pg in (meta.get("paginas_usadas") or [])]
            if any(any(m in pg for m in marcas) for pg in paginas):
                sesiones_rel.append({"id": meta.get("id", p.stem), "titulo": meta.get("titulo", p.stem)})

    idx = None
    try:
        from . import indice
        idx = indice.conexiones(root, slug)   # una sola pasada por mem.db
    except Exception:
        pass   # sin índice derivado, las conexiones clásicas alcanzan
    if idx is not None:
        if ocultas:   # el índice no sabe de privacidad: se filtra a la salida
            idx = {k: [x for x in v if x["slug"] not in ocultas] for k, v in idx.items()}
        return {"sesiones": sesiones_rel, **idx}

    # Fallback sin índice (FTS5 ausente, slug aún no indexado): el scan de
    # siempre para "mismo subject"; wikilinks/relacionadas necesitan mem.db.
    entradas_rel = []
    if subjects:
        for p in sorted((root / ENTRADAS).glob("*.md")):
            if p.stem == slug or p.stem in ocultas or len(entradas_rel) >= 5:
                continue
            meta2 = frontmatter.load(p).metadata
            if subjects & set(meta2.get("subjects") or []):
                entradas_rel.append({"slug": p.stem, "titulo": meta2.get("titulo", p.stem)})
    return {"sesiones": sesiones_rel, "entradas": entradas_rel,
            "wikilinks": [], "backlinks": [], "relacionadas": []}


def enlazar_entradas(root: Path, slug: str, destino: str) -> str:
    """Confirma una sugerencia de enlace: deja una línea fechada con el
    [[wikilink]] en el registro histórico de `slug`. Es la ÚNICA escritura sobre
    Markdown de toda la capa de enlaces, y siempre a pedido del usuario —
    ninguna sugerencia automática toca los .md (invariante §7.14)."""
    p = _entrada(root, slug)
    _entrada(root, destino)   # el destino tiene que existir
    if destino == slug:
        return "(una entrada no se enlaza consigo misma)"
    post = frontmatter.load(p)
    if re.search(rf"\[\[{re.escape(destino)}(\]\]|\|)", post.content):
        return "(ya estaban enlazadas)"
    hoy = date.today().isoformat()
    post.metadata["actualizada"] = hoy
    post.content += f"\n**{hoy}** (enlace) — vinculada con [[{destino}]].\n"
    p.write_text(frontmatter.dumps(post), encoding="utf-8")
    _indice_hook(root)
    return f"enlazadas: {slug} → [[{destino}]]"


def anotar_registro(root: Path, slug: str, texto: str, origen: str = "aviso") -> str:
    """Deja una línea fechada en el registro histórico de `slug`. La usa el lint
    cuando Diego acepta un aviso semántico ("esta memoria quedó contradicha por
    aquella"): el LLM propone, esto escribe, y solo a pedido — un falso positivo
    anexado solo no se podría deshacer, porque el registro es historia (§7.3).

    No versiona: igual que enlazar_entradas, no toca el título ni el resumen."""
    p = _entrada(root, slug)
    post = frontmatter.load(p)
    hoy = date.today().isoformat()
    post.metadata["actualizada"] = hoy
    post.content += f"\n**{hoy}** ({origen}) — {texto.strip()}\n"
    p.write_text(frontmatter.dumps(post), encoding="utf-8")
    _indice_hook(root)
    return f"anotada: {slug}"


def resumen_corto(md: str, n: int = 200) -> str:
    """Primeras líneas del cuerpo (solo el resumen) para la ficha. La transcripción
    del adjunto queda fuera: es larga y en la ficha taparía al resumen."""
    cuerpo = md.split(CAB_ADJUNTO)[0].split(CAB_REGISTRO)[0]
    texto = " ".join(re.sub(r"^#.*$", "", cuerpo, flags=re.M).split())
    return texto[:n] + ("…" if len(texto) > n else "")


def buscar_memorias(root: Path, texto: str = "", tag: str = "", subject: str = "",
                    desde: str = "", hasta: str = "", lugar: str = "",
                    orden: str = "fecha", sesion: str = "", sin_proyecto: bool = False,
                    proyecto: str | None = None) -> list[dict]:
    """Búsqueda sobre las entradas: texto libre + cualquier metadato.

    `texto` busca en título, cuerpo, tags, subjects, lugar y enlaces — o sea, en
    todo lo que el procesador indexó. `desde`/`hasta` aceptan tanto la fecha del
    archivo como la fecha a la que se refiere la información (`cuando`).
    `sesion` = solo lo nacido en esa sesión (frontmatter `sesion`) — el Media
    Manager la usa para el alcance "esta sesión". `sin_proyecto` = el filtro que
    `subject` no puede expresar: lo que NO cuelga de ningún proyecto.

    `subject`/`sin_proyecto` son FILTROS (qué se pide) y `proyecto` es desde
    dónde se pregunta: aunque se pidan "todas", lo privado de otro proyecto no
    sale (accesible).

    `orden="relevancia"` (el omnibox de la app): `texto` suma los hits de la
    búsqueda híbrida a los del substring y ordena por ese ranking. Los demás
    órdenes conservan la semántica léxica exacta de siempre — los llamadores
    que filtran (MCP, Media Manager) no reciben vecinos semánticos de regalo.

    ponytail: el resto de filtros sigue en scan lineal del frontmatter.
    """
    rango: dict[str, int] = {}
    if texto and orden == "relevancia":
        try:
            from . import indice
            rango = {s: i for i, (s, _) in enumerate(indice.buscar_hibrida(root, texto, k=20))}
        except Exception:
            pass
    out = []
    entradas = root / ENTRADAS
    if not entradas.exists():
        return out
    for p in sorted(entradas.glob("*.md"), reverse=True):
        post = frontmatter.load(p)
        m = post.metadata
        fecha = str(m.get("actualizada") or m.get("creada") or "")
        cuando = str(m.get("cuando") or "")
        fechas = [f for f in (fecha, cuando[:10]) if f]
        if desde and not any(f >= desde for f in fechas):
            continue
        if hasta and not any(f <= hasta for f in fechas):
            continue
        if tag and _norm(tag) not in [_norm(str(t)) for t in (m.get("tags") or [])]:
            continue
        if subject and not any(_norm(str(s)).startswith(_norm(subject)) for s in (m.get("subjects") or [])):
            continue  # match por prefijo de path: "Tecnologia/IA" encuentra "Tecnologia/IA/LLMs"
        if sin_proyecto and any(_bajo(s, GRUPO_PROYECTOS) for s in (m.get("subjects") or [])):
            continue
        if not accesible(m, proyecto):
            continue
        if lugar and _norm(lugar) not in _norm(str(m.get("lugar", ""))):
            continue
        if sesion and str(m.get("sesion") or "") != sesion:
            continue
        campos = " ".join([str(m.get("titulo", "")), post.content, str(m.get("lugar", "")),
                           " ".join(str(x) for x in (m.get("tags") or [])),
                           " ".join(str(x) for x in (m.get("subjects") or [])),
                           " ".join(str(x) for x in (m.get("enlaces") or []))])
        if texto and _norm(texto) not in _norm(campos) and p.stem not in rango:
            continue
        # ts: instante real de subida/actualización. `creada`/`actualizada` son
        # solo fecha, así que sin esto la UI no puede filtrar "última hora".
        out.append({"slug": p.stem, "titulo": m.get("titulo", p.stem), "fecha": fecha,
                    "ts": p.stat().st_mtime,
                    "cuando": cuando, "capturado": str(m.get("capturado") or ""),
                    "lugar": m.get("lugar", ""),
                    # desde dónde se capturó (≠ lugar, que es de qué habla la info)
                    "lugar_captura": str(m.get("lugar_captura") or ""),
                    "coords_captura": str(m.get("coords_captura") or ""),
                    "subjects": normalizar_subjects(m.get("subjects")), "tags": m.get("tags") or [],
                    "enlaces": m.get("enlaces") or [], "adjunto": m.get("adjunto", ""),
                    "pendiente": m.get("pendiente") or [],
                    # candado: lo decide la memoria, no el proyecto (pedido 2026-08-08)
                    "privada": bool(m.get("privada")),
                    # de qué sesión nació: la app la filtra por tipo y linkea de vuelta
                    "sesion": str(m.get("sesion") or ""),
                    "resumen": resumen_corto(post.content)})
    if orden == "titulo":
        out.sort(key=lambda e: _norm(str(e["titulo"])))
    elif orden == "relevancia" and rango:
        out.sort(key=lambda e: rango.get(e["slug"], 10**6))
    else:
        out.sort(key=lambda e: (e["cuando"] or e["fecha"]), reverse=True)
    return out


def medios_todos(root: Path, *, texto: str = "", desde: str = "", hasta: str = "",
                  sesion: str = "", subject: str = "", proyecto: str | None = None) -> list[dict]:
    """Todo el material de una galería: las entradas catalogadas (frontmatter
    `adjunto`) MÁS lo que una sesión subió o generó y todavía no se destiló en
    una entrada propia. buscar_memorias() solo mira 06_.../Entradas, así que el
    Media Manager (armado sobre ella) nunca mostraba lo recién subido a un chat
    — reportado 2026-08-10: un video vivía en la sesión, no en ninguna ficha.

    Cada item: {ruta, titulo, slug, sesion, ts, privada, subjects}. `slug` vacío =
    todavía no es una memoria propia — la app linkea a la sesión (go("chat",
    sesion)), no a una ficha que no existe. Los dos últimos son lo que el candado
    del celular necesita para tapar el medio de una memoria privada sin abrirla
    (pedido 2026-08-23): son los MISMOS campos que trae una memoria, así que la
    app usa el mismo criterio y no inventa uno para la galería.
    """
    root = Path(root)
    catalogadas = buscar_memorias(root, texto=texto, desde=desde, hasta=hasta, sesion=sesion,
                                  subject=subject, proyecto=proyecto)
    # los sueltos no tienen ficha: su privacidad es la del proyecto de la sesión dueña
    privados = _privados(root) if proyecto is not None else set()
    out = [{"ruta": e["adjunto"], "titulo": e["titulo"], "slug": e["slug"],
            "sesion": e["sesion"], "ts": e["ts"],
            "privada": e["privada"], "subjects": e["subjects"]}
           for e in catalogadas if e["adjunto"] and e["adjunto"].lower().endswith(MEDIA_EXT)]
    vistas = {it["ruta"] for it in out}
    q = _norm(texto)
    for ruta, paginas in adjuntos_referidos(root).items():
        if ruta in vistas or not ruta.lower().endswith(MEDIA_EXT):
            continue
        f = root / ruta
        if not f.is_file():
            continue
        sid = next((m.group(1) for pg in paginas if (m := RX_SESION_PAGE.match(pg))), "")
        if sesion and sid != sesion:
            continue
        ses_meta = frontmatter.load(root / "10_Sesiones" / f"{sid}.md").metadata if sid and (root / "10_Sesiones" / f"{sid}.md").is_file() else {}
        p_ses = _norm(str(ses_meta.get("proyecto") or ""))
        if subject and _norm(f"proyectos/{ses_meta.get('proyecto') or ''}") != _norm(subject):
            continue
        if p_ses in privados and p_ses != _norm(proyecto or ""):
            continue
        ts = f.stat().st_mtime
        if desde and datetime.fromtimestamp(ts).strftime("%Y-%m-%d") < desde:
            continue
        if hasta and datetime.fromtimestamp(ts).strftime("%Y-%m-%d") > hasta:
            continue
        titulo = str(ses_meta.get("titulo") or "") or ruta.split("/")[-1]
        if q and q not in _norm(f"{titulo} {ruta}"):
            continue
        # el suelto no tiene ficha: su proyecto es el de la sesión dueña, dicho
        # como subject para que la app lo lea igual que el de una memoria
        p_nombre = str(ses_meta.get("proyecto") or "")
        out.append({"ruta": ruta, "titulo": titulo, "slug": "", "sesion": sid, "ts": ts,
                    "privada": False, "subjects": [f"Proyectos/{p_nombre}"] if p_nombre else []})
    out.sort(key=lambda e: e["ts"], reverse=True)
    return out


def entrada_de_adjunto(root: Path, ruta: str) -> str:
    """Slug de la memoria que guarda ese adjunto (o "" si todavía no se procesó).
    Sirve para linkear al visor de la app EN su ficha, no al fichero pelado."""
    ruta = ruta.strip().lstrip("/").replace("\\", "/")
    return next((m["slug"] for m in buscar_memorias(root) if m["adjunto"] == ruta), "")


def editar_entrada(root: Path, slug: str, titulo: str | None = None,
                   tags: list[str] | None = None, subjects: list[str] | None = None,
                   nota: str = "", resumen: str | None = None,
                   privada: bool | None = None) -> str:
    """Edición acotada (spec §6.1): contenido, metadatos y notas; todo cambio
    queda fechado en el registro histórico, nunca se reescribe historia.

    Los cambios de CONTENIDO (título, resumen, nota) guardan la versión anterior
    completa. Poner o quitar un tag no: en la app es un toque de chip y llenaría
    el historial de versiones idénticas."""
    p = root / ENTRADAS / f"{slug}.md"
    if not p.is_file():
        raise FileNotFoundError(f"entrada no encontrada: {slug}")
    post = frontmatter.load(p)
    hoy = date.today().isoformat()
    cambios, contenido = [], False
    if titulo and titulo != post.metadata.get("titulo"):
        post.metadata["titulo"] = titulo
        cambios.append(f"título → {titulo}")
        contenido = True
    if resumen and resumen.strip():
        cambios.append("resumen actualizado")
        contenido = True
    if tags is not None and list(tags) != list(post.metadata.get("tags") or []):
        post.metadata["tags"] = list(tags)
        cambios.append(f"tags → {', '.join(tags) or '(ninguno)'}")
    if subjects is not None and list(subjects) != list(post.metadata.get("subjects") or []):
        post.metadata["subjects"] = list(subjects)
        _desindexar(root, slug)  # reasignar subject reindexa automáticamente
        _indexar_tematico(root, str(post.metadata.get("titulo", slug)), slug, subjects, hoy, cronologico=False)
        _indexar_categorias(root, str(post.metadata.get("titulo", slug)), slug, subjects)
        cambios.append(f"subjects → {', '.join(subjects) or '(ninguno)'}")
    # la casilla de privado. Se guarda SIEMPRE explícita (también el false): así
    # migrar_privadas distingue "nunca se decidió" de "Diego la destildó" y no se
    # la vuelve a marcar por el proyecto.
    if privada is not None and bool(privada) != bool(post.metadata.get("privada")):
        post.metadata["privada"] = bool(privada)
        cambios.append("marcada privada" if privada else "ya no es privada")
    if nota:
        cambios.append(f"nota: {nota}")
        contenido = True
    if not cambios:
        return "(sin cambios)"
    if contenido:
        _versionar(root, p, post)
    if resumen and resumen.strip():
        post.content = _poner_seccion(post.content, "## Resumen", resumen,
                                      CAB_ADJUNTO if CAB_ADJUNTO in post.content else CAB_REGISTRO)
    if titulo:
        post.content = re.sub(r"\A# .*$", f"# {titulo}", post.content.lstrip(), count=1, flags=re.M)
        _retitular(root, slug, titulo)   # el link del índice lleva el título como texto
    post.metadata["actualizada"] = hoy
    post.content += f"\n**{hoy}** (edición) — {'; '.join(cambios)}.\n"
    p.write_text(frontmatter.dumps(post), encoding="utf-8")
    _indice_hook(root)
    return f"entrada editada: {ENTRADAS}/{slug}.md"


def versiones(root: Path, slug: str) -> list[dict]:
    """Versiones anteriores de una entrada, de la más reciente a la más vieja.
    La versión viva no está en la lista: es el archivo de la entrada."""
    post = frontmatter.load(_entrada(root, slug))
    iid = str(post.metadata.get("id") or "")
    if not iid:
        return []
    d = root / VERSIONES / iid
    return [{"version": int(f.stem[1:]),
             "fecha": date.fromtimestamp(f.stat().st_mtime).isoformat()}
            for f in sorted(d.glob("v*.md"), reverse=True)] if d.is_dir() else []


def version_leer(root: Path, slug: str, n: int) -> dict:
    post = frontmatter.load(_entrada(root, slug))
    iid = str(post.metadata.get("id") or "")
    f = root / VERSIONES / iid / f"v{int(n):03}.md"
    if not iid or not f.is_file():
        raise FileNotFoundError(f"versión no encontrada: {slug} v{n}")
    vieja = frontmatter.load(f)
    return {"version": int(n), "titulo": str(vieja.metadata.get("titulo", slug)),
            "contenido": vieja.content}


def asignar_ids(root: Path) -> int:
    """Migración one-shot: da id y version a las entradas que venían de antes del
    versionado. Sin esto también funcionan (lo ganan al editarse), pero así el
    id existe desde ya y la app puede mostrarlo."""
    n = 0
    for p in sorted((root / ENTRADAS).glob("*.md")):
        post = frontmatter.load(p)
        if post.metadata.get("id"):
            continue
        post.metadata["id"] = nuevo_id()
        post.metadata["version"] = 1
        p.write_text(frontmatter.dumps(post), encoding="utf-8")
        n += 1
    return n


def fijar_pendiente(root: Path, slug: str, faltantes: list[str]) -> None:
    """Marca (o limpia) qué material adjunto no pudo leer el LLM. Va aparte de
    guardar_entrada porque ahí las listas se UNEN: cuando el reproceso por fin
    entiende el adjunto hay que poder BORRAR la marca, no acumularla.

    ponytail: no versiona — corre pegado a guardar_entrada, que ya guardó la
    versión anterior hace un instante."""
    p = root / ENTRADAS / f"{slug}.md"
    if not p.is_file():
        return
    post = frontmatter.load(p)
    if faltantes:
        post.metadata["pendiente"] = list(faltantes)
    else:
        post.metadata.pop("pendiente", None)
    p.write_text(frontmatter.dumps(post), encoding="utf-8")


def subjects_de(root: Path, paths: list[str]) -> list[str]:
    """Subjects declarados por unas páginas leídas. Es como una sesión descubre
    de qué trata: lo que el chat abrió dice a qué subjects pertenece."""
    out: list[str] = []
    for rel in paths:
        p = root / str(rel)
        if p.is_file() and p.suffix == ".md":
            try:
                out += [str(s) for s in (frontmatter.load(p).metadata.get("subjects") or [])]
            except (OSError, ValueError):
                continue
    return list(dict.fromkeys(out))


def subjects_mencionados(root: Path, texto: str) -> list[str]:
    """Subjects del árbol cuyo nombre aparece en el texto. Respaldo para turnos
    que no leyeron páginas (web o conocimiento del modelo)."""
    n = _norm(texto)
    out = []
    for grupo in arbol_subjects(root):
        for hijo in grupo["hijos"]:
            hoja = _norm(hijo["nombre"])
            if len(hoja) >= 4 and re.search(rf"\b{re.escape(hoja)}\b", n):
                out.append(f"{grupo['nombre']}/{hijo['nombre']}")
    return out


def arbol_subjects(root: Path) -> list[dict]:
    """Árbol de 00_CATEGORIAS.md con conteo de entradas por subject (spec §6.1)."""
    p = root / "08_Categorias/00_CATEGORIAS.md"
    if not p.exists():
        return []
    arbol: list[dict] = []
    pila: list[tuple[int, dict]] = []  # (indent, nodo) del camino actual
    for linea in p.read_text(encoding="utf-8").splitlines():
        if linea.startswith("## "):
            nodo = {"nombre": linea[3:].strip(), "hijos": [], "entradas": 0}
            arbol.append(nodo)
            pila = [(-1, nodo)]
        elif (m := re.match(r"(\s*)- \*\*(.+?)\*\*", linea)) and pila:
            indent = len(m.group(1))
            while len(pila) > 1 and pila[-1][0] >= indent:
                pila.pop()
            nodo = {"nombre": m.group(2), "hijos": [], "entradas": 0}
            pila[-1][1]["hijos"].append(nodo)
            pila.append((indent, nodo))
        elif "Entradas/" in linea and pila:
            pila[-1][1]["entradas"] += 1
    return arbol


# ---------------------------------------------------------------- proyectos

PROYECTOS = "08_Categorias/PROYECTOS.md"
GRUPO_PROYECTOS = "Proyectos"
AMBITOS = ("personal", "trabajo", "privado")


def subject_proyecto(nombre: str) -> str:
    """Un proyecto ES un subject del grupo Proyectos. Así hereda gratis los
    índices, el árbol de Temas y la búsqueda por prefijo — no hay una segunda
    taxonomía que mantener en paralelo."""
    return f"{GRUPO_PROYECTOS}/{nombre}" if nombre else ""


def _bajo(s: str, pref: str) -> bool:
    """¿El subject cae bajo ese path? Por segmento, no por prefijo de texto:
    'Proyectos/Casa' NO matchea 'Proyectos/CasaNueva'."""
    a, b = _norm(str(s)), _norm(pref)
    return a == b or a.startswith(b + "/")


def _entradas_con_subject(root: Path, sub: str) -> list[str]:
    entradas = root / ENTRADAS
    if not entradas.exists():
        return []
    return [p.stem for p in sorted(entradas.glob("*.md"))
            if any(_bajo(s, sub) for s in (frontmatter.load(p).metadata.get("subjects") or []))]


def proyectos_listar(root: Path) -> list[dict]:
    p = root / PROYECTOS
    if not p.is_file():
        return []
    return [{"nombre": str(d.get("nombre") or ""), "ambito": str(d.get("ambito") or "personal"),
             "creado": str(d.get("creado") or "")}
            for d in (frontmatter.load(p).metadata.get("proyectos") or [])
            if str(d.get("nombre") or "").strip()]


def proyecto_guardar(root: Path, nombre: str, ambito: str = "personal") -> dict:
    """Crea el proyecto, o le cambia el ámbito si ya existe. El archivo queda
    legible sin el app: frontmatter para los datos, cuerpo para leerlo a ojo."""
    nombre = " ".join(nombre.split())
    if not nombre:
        raise ValueError("el proyecto necesita un nombre")
    if ambito not in AMBITOS:
        raise ValueError(f"ámbito inválido: {ambito} (usar {', '.join(AMBITOS)})")
    lista = proyectos_listar(root)
    prev = next((x for x in lista if _norm(x["nombre"]) == _norm(nombre)), None)
    if prev:
        prev["ambito"] = ambito
    else:
        lista.append({"nombre": nombre, "ambito": ambito, "creado": date.today().isoformat()})
    _proyectos_escribir(root, lista)
    log_evento(root, "proyectos", f"{'ámbito' if prev else 'proyecto nuevo'}: {nombre} ({ambito})")
    return prev or lista[-1]


def _proyectos_escribir(root: Path, lista: list[dict]) -> None:
    """El archivo queda legible sin el app: frontmatter para los datos, cuerpo
    para leerlo a ojo."""
    cuerpo = "# Proyectos\n\n" + "".join(
        f"- **{x['nombre']}** — {x['ambito']} ({x['creado']})\n" for x in lista)
    p = root / PROYECTOS
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(frontmatter.dumps(frontmatter.Post(cuerpo, proyectos=lista)), encoding="utf-8")


def _resubjectar(subjects, sub_viejo: str, sub_nuevo: str) -> list[str]:
    """Mueve (o quita, con sub_nuevo="") el subject del proyecto y sus hijos.
    Dedupe: una entrada etiquetada con los DOS proyectos de una unión terminaría
    con el mismo subject repetido."""
    out = []
    for s in (subjects or []):
        if not _bajo(s, sub_viejo):
            out.append(str(s))
        elif sub_nuevo:
            out.append(sub_nuevo + str(s)[len(sub_viejo):])
    return list(dict.fromkeys(out))


def _inbox_reproyectar(root: Path, sub_viejo: str, sub_nuevo: str, proyecto: str) -> None:
    """Lo mismo que se le hace a las entradas, pero a los pendientes del inbox.

    Sin esto renombrar o borrar dejaba huérfanos que se REVIVEN solos: procesar.py
    re-inyecta subject_proyecto(meta["proyecto"]) en la próxima pasada. Escribe
    directo y no por inbox_editar, que marcaría los campos como `fijado`.
    """
    for p in sorted((root / "07_Inbox").glob("*.md")):   # top-level: no baja a _adjuntos/_procesado
        post = frontmatter.load(p)
        m = post.metadata
        subs = _resubjectar(m.get("subjects"), sub_viejo, sub_nuevo)
        toca_proy = _norm(str(m.get("proyecto") or "")) == _norm(sub_viejo.split("/", 1)[-1])
        if subs == list(m.get("subjects") or []) and not toca_proy:
            continue
        m["subjects"] = subs
        if toca_proy:
            m["proyecto"] = proyecto
        p.write_text(frontmatter.dumps(post), encoding="utf-8")


def proyecto_renombrar(root: Path, viejo: str, nuevo: str, fusionar: bool = False) -> dict:
    """Renombra el proyecto y mueve el subject Proyectos/<viejo> (y sus hijos)
    en todas sus memorias — editar_entrada reindexa y ficha cada cambio. Las
    sesiones las actualiza el llamador: viven en sesiones.py.

    `fusionar`: el destino YA existe y es a propósito — unir dos proyectos es
    exactamente este mismo barrido, pero borrando la fila de origen en vez de
    renombrarla. Por eso no hay un proyecto_fusionar aparte.
    """
    nuevo = " ".join(nuevo.split())
    if not nuevo:
        raise ValueError("el proyecto necesita un nombre")
    lista = proyectos_listar(root)
    p = next((x for x in lista if _norm(x["nombre"]) == _norm(viejo)), None)
    if not p:
        raise FileNotFoundError(f"proyecto no encontrado: {viejo}")
    destino = next((x for x in lista if _norm(x["nombre"]) == _norm(nuevo) and x is not p), None)
    if fusionar and not destino:
        raise ValueError(f"no existe el proyecto destino: {nuevo}")
    if not fusionar and destino:
        raise ValueError(f"ya existe un proyecto {nuevo}")
    if fusionar and destino is p:
        raise ValueError("un proyecto no se une consigo mismo")
    sub_viejo = subject_proyecto(p["nombre"])
    if destino:                       # unión: el nombre bueno es el del destino
        sub_nuevo = subject_proyecto(destino["nombre"])
        lista.remove(p)
    else:
        sub_nuevo = subject_proyecto(nuevo)
        p["nombre"] = nuevo
    _proyectos_escribir(root, lista)
    for slug in _entradas_con_subject(root, sub_viejo):
        e = leer_entrada(root, slug)
        editar_entrada(root, slug, subjects=_resubjectar(e.get("subjects"), sub_viejo, sub_nuevo))
    out = destino or p
    _inbox_reproyectar(root, sub_viejo, sub_nuevo, out["nombre"])
    log_evento(root, "proyectos",
               f"{'unido' if destino else 'renombrado'}: {viejo} → {out['nombre']}")
    return out


def proyecto_eliminar(root: Path, nombre: str, con_contenido: bool = False,
                      destino: str = "") -> dict:
    """Quita el proyecto de la lista. Sus memorias van a la papelera con él
    (con_contenido), o quedan vivas: sin proyecto, o bajo `destino` si se pidió
    reasignarlas. Devuelve los slugs tocados; las sesiones las maneja el llamador."""
    lista = proyectos_listar(root)
    p = next((x for x in lista if _norm(x["nombre"]) == _norm(nombre)), None)
    if not p:
        raise FileNotFoundError(f"proyecto no encontrado: {nombre}")
    d = next((x for x in lista if _norm(x["nombre"]) == _norm(destino)), None) if destino else None
    if destino and (not d or d is p):
        raise ValueError(f"destino inválido: {destino}")
    lista.remove(p)
    _proyectos_escribir(root, lista)
    sub = subject_proyecto(p["nombre"])
    sub_nuevo = subject_proyecto(d["nombre"]) if d else ""
    slugs = _entradas_con_subject(root, sub)
    for slug in slugs:
        if con_contenido:
            eliminar_entrada(root, slug)
        else:
            e = leer_entrada(root, slug)
            editar_entrada(root, slug, subjects=_resubjectar(e.get("subjects"), sub, sub_nuevo))
    _inbox_reproyectar(root, sub, sub_nuevo, d["nombre"] if d else "")
    log_evento(root, "proyectos", f"proyecto borrado: {p['nombre']}"
               + (" (con contenido)" if con_contenido else f" → {d['nombre']}" if d else ""))
    return {"nombre": p["nombre"], "memorias": slugs}


def proyecto_por_nombre(root: Path, nombre: str) -> dict | None:
    """El proyecto que el usuario nombró, sin exigirle tildes ni mayúsculas."""
    n = _norm(str(nombre or ""))
    return next((p for p in proyectos_listar(root) if _norm(p["nombre"]) == n), None) if n else None


# ---------------------------------------------------------------- índices

def _fin_seccion(lineas: list[str], cabecera: str) -> int:
    """Índice donde termina la sección (antes del próximo '## ')."""
    try:
        i = next(j for j, l in enumerate(lineas) if l.strip() == cabecera)
    except StopIteration:
        lineas += ["", cabecera]
        return len(lineas)
    for j in range(i + 1, len(lineas)):
        if lineas[j].startswith("## "):
            return j
    return len(lineas)


def _indexar_tematico(root: Path, titulo: str, slug: str, subjects: list[str],
                      hoy: str, cronologico: bool = True) -> None:
    idx = root / "06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md"
    if not idx.exists():
        idx.write_text("# Índice Temático\n\n## Por etiqueta/categoría\n\n## Todas las entradas (orden cronológico)\n", encoding="utf-8")
    lineas = idx.read_text(encoding="utf-8").splitlines()
    linea = f"- [{titulo}](Entradas/{slug}.md) — {titulo}. ({hoy})"
    for cat in subjects:
        cab = f"### {cat}"
        if cab in [l.strip() for l in lineas]:
            i = next(j for j, l in enumerate(lineas) if l.strip() == cab)
            lineas.insert(i + 1, linea)
        else:
            i = _fin_seccion(lineas, "## Por etiqueta/categoría")
            lineas[i:i] = ["", cab, linea]
    if cronologico:
        i = _fin_seccion(lineas, "## Todas las entradas (orden cronológico)")
        lineas.insert(i, f"- **{hoy}** — [{titulo}](Entradas/{slug}.md)")
    idx.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def _indexar_categorias(root: Path, titulo: str, slug: str, subjects: list[str]) -> None:
    """Archiva el link de la entrada bajo cada uno de sus temas. Un tema que no
    existe se crea EN SU GRUPO real (## Grupo → - **Hoja**) — nada de amontonar
    en una sección "_Nuevas": el árbol de categorías es lo que Temas muestra."""
    p = root / "08_Categorias/00_CATEGORIAS.md"
    if not p.exists():
        return
    lineas = p.read_text(encoding="utf-8").splitlines()
    link = f"[{titulo}](../06_Biblioteca_Conocimiento/Entradas/{slug}.md)"
    nuevos = []
    for cat in subjects:
        seg = [x.strip() for x in str(cat).split("/") if x.strip()]
        if not seg:
            continue
        hoja = "/".join(seg[1:])  # más de dos niveles se aplana en la hoja
        idx = None
        seccion = None
        for i, linea in enumerate(lineas):
            if linea.startswith("## "):
                seccion = linea[3:].strip()
            elif (m := RX_NEGRITA.search(linea)) and _norm(m.group(1)) == _norm(hoja or seg[0]) \
                    and (not hoja or seccion == seg[0]):
                idx = i  # 1 nivel: la hoja vale en cualquier grupo; 2+: solo dentro del suyo
                break
        if idx is not None:
            indent = len(lineas[idx]) - len(lineas[idx].lstrip()) + 2
            lineas.insert(idx + 1, " " * indent + f"- {link}")
            continue
        existia = any(l.strip() == f"## {seg[0]}" for l in lineas)
        fin = _fin_seccion(lineas, f"## {seg[0]}")  # crea la sección si falta
        lineas[fin:fin] = [f"- **{hoja}**", f"  - {link}"] if hoja else [f"- {link}"]
        if hoja or not existia:
            nuevos.append(cat)
    if nuevos:
        log_evento(root, "categorias", f"temas nuevos: {', '.join(nuevos)}")
    p.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def reconstruir_indices(root: Path) -> str:
    """Regenera 00_CATEGORIAS.md y la sección por-categoría del índice temático
    desde el frontmatter de las entradas (la única fuente de verdad). Los archivos
    previos van a una _papelera fechada; la sección cronológica se conserva."""
    entradas = []
    for p in sorted((root / ENTRADAS).glob("*.md")):
        post = frontmatter.load(p)
        m = post.metadata
        subjects = normalizar_subjects(m.get("subjects"))
        if subjects != [str(s) for s in (m.get("subjects") or [])]:
            m["subjects"] = subjects   # repara en el origen los separadores raros
            p.write_text(frontmatter.dumps(post), encoding="utf-8")
        entradas.append((p.stem, str(m.get("titulo", p.stem)), subjects, str(m.get("creada") or "")))
    # árbol: Grupo → hoja ("" = colgada directo del grupo) → [(titulo, slug, creada)]
    arbol: dict[str, dict[str, list]] = {}
    for slug, titulo, subjects, creada in entradas:
        for s in subjects:
            seg = [x.strip() for x in s.split("/") if x.strip()]
            if not seg:
                continue
            hoja = "/".join(seg[1:])
            arbol.setdefault(seg[0], {}).setdefault(hoja, []).append((titulo, slug, creada))

    def respaldar(p: Path) -> None:
        if p.exists():
            destino = p.parent / "_papelera"
            destino.mkdir(parents=True, exist_ok=True)
            (destino / f"{p.stem}_{datetime.now():%Y-%m-%d_%H%M%S}{p.suffix}").write_text(
                p.read_text(encoding="utf-8"), encoding="utf-8")

    cat = root / "08_Categorias/00_CATEGORIAS.md"
    lineas = ["# Categorias -- MeM", "",
              "_(arbol de temas; regenerado desde el frontmatter de las entradas)_"]
    for grupo in sorted(arbol):
        lineas += ["", f"## {grupo}"]
        hojas = arbol[grupo]
        for titulo, slug, _ in hojas.get("", []):
            lineas.append(f"- [{titulo}](../06_Biblioteca_Conocimiento/Entradas/{slug}.md)")
        for hoja in sorted(h for h in hojas if h):
            lineas.append(f"- **{hoja}**")
            for titulo, slug, _ in hojas[hoja]:
                lineas.append(f"  - [{titulo}](../06_Biblioteca_Conocimiento/Entradas/{slug}.md)")
    respaldar(cat)
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    idx = root / "06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md"
    previas = idx.read_text(encoding="utf-8").splitlines() if idx.exists() else []
    try:
        i = next(j for j, l in enumerate(previas) if l.strip() == "## Todas las entradas (orden cronológico)")
        cronologico = previas[i:]
    except StopIteration:
        cronologico = ["## Todas las entradas (orden cronológico)"]
    lineas2 = ["# Índice Temático", "", "## Por etiqueta/categoría"]
    for grupo in sorted(arbol):
        for hoja in sorted(arbol[grupo]):
            ruta = f"{grupo}/{hoja}" if hoja else grupo
            lineas2 += ["", f"### {ruta}"]
            for titulo, slug, creada in arbol[grupo][hoja]:
                lineas2.append(f"- [{titulo}](Entradas/{slug}.md) — {titulo}. ({creada})")
    respaldar(idx)
    idx.write_text("\n".join(lineas2 + ["", *cronologico]) + "\n", encoding="utf-8")
    n_temas = sum(len(v) for v in arbol.values())
    return f"{len(arbol)} grupos, {n_temas} temas, {len(entradas)} entradas indexadas"


def _retitular(root: Path, slug: str, titulo: str) -> None:
    """Cambia el texto de los links a una entrada que se renombró. Hace falta por
    la línea cronológica, que _desindexar conserva a propósito: si no, el índice
    seguiría diciendo "Adjunto no leído" y el chat leería ese título."""
    rx = re.compile(rf"\[[^\]]*\]\(((?:\.\./)?[\w/]*Entradas/{re.escape(slug)}\.md)\)")
    for rel in INDICES:
        p = root / rel
        if p.exists():
            p.write_text(rx.sub(rf"[{titulo}](\1)", p.read_text(encoding="utf-8")), encoding="utf-8")


def _desindexar(root: Path, slug: str) -> None:
    """Quita los links por-categoría de una entrada (árbol + índice temático).
    La línea cronológica ('- **fecha** — …') se conserva: es historia."""
    marca = f"Entradas/{slug}.md)"
    for rel in ("08_Categorias/00_CATEGORIAS.md", "06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md"):
        p = root / rel
        if not p.exists():
            continue
        lineas = [l for l in p.read_text(encoding="utf-8").splitlines()
                  if not (marca in l and l.lstrip().startswith("- ["))]
        p.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def _demo_privacidad(root: Path) -> None:
    """Lo privado no sale de su proyecto, lo demás se ve desde cualquiera."""
    proyecto_guardar(root, "Diario", "privado")
    proyecto_guardar(root, "Taller", "personal")
    capturar(root, "anoche soñé", proyecto="Diario")
    iid = sorted((root / "07_Inbox").glob("*.md"))[-1]
    m = frontmatter.load(iid).metadata
    assert m["proyecto"] == "Diario" and m["subjects"] == ["Proyectos/Diario"], m
    assert m.get("privada") is True, "capturar dentro de un proyecto privado nace privada"
    assert m["fijado"] == [], "el subject del proyecto no congela la clasificación pendiente"

    guardar_entrada(root, "Secreto", "cuerpo", ["Proyectos/Diario"])
    guardar_entrada(root, "Banco", "cuerpo", ["Proyectos/Taller"])
    editar_entrada(root, "banco", privada=True)          # privada a mano, proyecto normal
    guardar_entrada(root, "Publica", "cuerpo", ["Proyectos/Taller"])
    guardar_entrada(root, "Suelta", "cuerpo", [])
    editar_entrada(root, "suelta", privada=True)         # privada y sin proyecto

    def slugs(p):
        return {e["slug"] for e in buscar_memorias(root, proyecto=p)}

    assert "secreto" in slugs("Diario") and "banco" not in slugs("Diario")
    assert {"banco", "publica"} <= slugs("Taller") and "secreto" not in slugs("Taller")
    assert "publica" in slugs("Diario"), "lo NO privado se ve desde cualquier proyecto"
    assert "suelta" in slugs("") and "suelta" not in slugs("Taller"), \
        "'Sin proyecto' es un proyecto más: lo privado de ahí solo se ve ahí"
    assert {"secreto", "banco", "suelta"} <= {e["slug"] for e in buscar_memorias(root)}, \
        "sin contexto (MCP, CLI) no se filtra nada"
    assert entradas_ocultas(root, "Taller") == {"secreto", "suelta"}
    assert f"07_Inbox/{iid.name}" in paths_ocultos(root, "Taller"), "la captura pendiente también"
    assert "privada" in leer_pagina(root, f"{ENTRADAS}/secreto.md", proyecto="Taller")
    assert "cuerpo" in leer_pagina(root, f"{ENTRADAS}/secreto.md", proyecto="Diario")
    assert "Secreto" not in buscar(root, "cuerpo", proyecto="Taller"), \
        "ni por el índice temático, que la nombra"
    assert "secreto" not in grep(root, "cuerpo", proyecto="Taller")
    assert "secreto" in grep(root, "cuerpo", proyecto="Diario")


def demo() -> None:
    """Check de proyectos: unir, reasignar, barrido del inbox y el límite de lo
    privado (no sale de su proyecto). `python -m mem.memoria`"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _demo_privacidad(Path(tmp) / "priv")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        proyecto_guardar(root, "Casa", "personal")
        proyecto_guardar(root, "CasaNueva", "trabajo")
        guardar_entrada(root, "Obra", "cuerpo", ["Proyectos/Casa/Obra", "Tecnologia/IA"])
        guardar_entrada(root, "Ambas", "cuerpo", ["Proyectos/Casa", "Proyectos/CasaNueva"])
        guardar_entrada(root, "Vecina", "cuerpo", ["Proyectos/CasaNueva"])
        capturar(root, "pendiente de casa", subjects=["Proyectos/Casa"])
        iid = sorted((root / "07_Inbox").glob("*.md"))[0]
        post = frontmatter.load(iid)
        post.metadata["proyecto"] = "Casa"          # como lo deja sincronizar_sesion_inbox
        iid.write_text(frontmatter.dumps(post), encoding="utf-8")

        assert proyecto_renombrar(root, "Casa", "CasaNueva", fusionar=True)["nombre"] == "CasaNueva"
        assert [p["nombre"] for p in proyectos_listar(root)] == ["CasaNueva"], "la fila origen se va"
        assert leer_entrada(root, "obra")["subjects"] == ["Proyectos/CasaNueva/Obra", "Tecnologia/IA"], \
            "los hijos se mudan con el padre"
        assert leer_entrada(root, "ambas")["subjects"] == ["Proyectos/CasaNueva"], "sin duplicar"
        m = frontmatter.load(iid).metadata
        assert m["subjects"] == ["Proyectos/CasaNueva"] and m["proyecto"] == "CasaNueva", \
            "el inbox pendiente también se barre (si no, procesar.py revive el viejo)"
        assert not buscar_memorias(root, sin_proyecto=True), "todas cuelgan de un proyecto"

        proyecto_guardar(root, "Refugio", "personal")
        proyecto_eliminar(root, "CasaNueva", destino="Refugio")
        assert leer_entrada(root, "vecina")["subjects"] == ["Proyectos/Refugio"]
        proyecto_eliminar(root, "Refugio")
        assert leer_entrada(root, "vecina")["subjects"] == [], "sin destino, quedan sin proyecto"
        assert len(buscar_memorias(root, sin_proyecto=True)) == 3
        try:
            proyecto_eliminar(root, "no existe")
            raise AssertionError("tenía que fallar")
        except FileNotFoundError:
            pass
    print("proyectos ok")





if __name__ == "__main__":
    demo()
