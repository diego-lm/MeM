"""Índice derivado de búsqueda: SQLite con FTS5 + vectores + wikilinks + geocache.

Vive en 09_Sistema/_derivados/mem.db y es 100% desechable: los .md son la única
fuente de verdad y cualquier problema (corrupción, cambio de esquema, cambio de
modelo de embeddings) se resuelve tirando el archivo y regenerando. Nada de acá
escribe jamás sobre el Markdown.

La búsqueda híbrida = BM25 (FTS5) + coseno sobre embeddings, fusionados por RRF.
Sin modelo de embeddings degrada a BM25; sin FTS5 compilado degrada al camino
naive de memoria.buscar. Nunca es peor que antes.
"""
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import frontmatter
import numpy as np

from . import embed, memoria

SCHEMA_VERSION = 1
DERIVADOS = "09_Sistema/_derivados"
RRF_K = 60      # el k=60 estándar de Reciprocal Rank Fusion
TOP = 50        # candidatos por rama antes de fusionar
UMBRAL_VECINAS = 0.55
UMBRAL_DUPLICADOS = 0.92
# Sugerencias mientras se escribe: umbral en DESVÍOS ESTÁNDAR, no en coseno.
# multilingual-e5-small comprime muchísimo el rango: medido sobre la base real,
# la basura ("asdkjhasd qwerty") saca cos 0.813 y un acierto bueno 0.807 — el
# valor absoluto no discrimina nada, la distancia a la media de la base sí.
# Con z ≥ 2 (+ match léxico obligatorio) entran los aciertos de verdad
# (tailscale 3.4, minimax 3.9, higgsfield 2.2) y no entra nada de lo lejano.
UMBRAL_SUGERENCIA = 2.0
# ...pero la z necesita población: con N memorias el máximo alcanzable es
# (N−1)/√N (con 7 memorias, 2,27), así que en una base recién nacida el umbral
# dejaría la función muerta para siempre. Debajo de este piso decide solo el
# BM25, que a esa escala ya es durísimo: un término que pasa el corte con 15
# memorias es raro en TODA la base. La z se prende justo cuando aparece el
# riesgo que tapa — que una entrada larga matchee de casualidad por acumulación.
MIN_BASE_Z = 20
# ...y el match léxico tiene que ser por una palabra que DISCRIMINE. La consulta
# FTS es un OR, así que "mañana tengo que llamar al dentista" matchea media base
# por las palabras vacías. BM25 no: su puntaje lo manda el IDF, y medido sobre la
# base real las palabras vacías dan −1,5…−0,7 mientras un término de verdad da
# −4,5 (higgsfield), −6,3 (tailscale), −14 (minimax). El corte separa limpio, y
# aguanta que la base crezca: el IDF de un término raro solo sube con N.
UMBRAL_BM25 = -2.5
RX_WIKILINK = re.compile(r"\[\[([a-z0-9-]+)(?:\|[^\]]*)?\]\]")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Nominatim exige que el User-Agent identifique la aplicación; el contacto es
# recomendación para uso intensivo, y sale del entorno para que el repo no lleve
# el mail de nadie. Quien geocodifique mucho hace `set MEM_CONTACTO=...`.
_CONTACTO = os.environ.get("MEM_CONTACTO", "").strip()
UA = f"MeM/1.0 (memoria personal local{'; ' + _CONTACTO if _CONTACTO else ''})"

# Debounce del barrido de mtimes: dentro de esta ventana, sincronizar() sin
# forzar no barre (una pantalla dispara varias consultas seguidas). Las
# escrituras propias fuerzan; las de OTRO proceso (MCP stdio) u otras apps
# (Obsidian, Dropbox) se ven con hasta SYNC_CADA s de retraso.
SYNC_CADA = float(os.environ.get("MEM_SYNC_CADA", "2"))
_SYNC: dict[str, float] = {}          # str(root) -> monotonic del último sync exitoso

# Cache de la matriz de embeddings: releerla de SQLite en cada búsqueda es lo
# caro. _VERSION la invalida: sincronizar() lo incrementa cuando algo cambió.
# ponytail: razas entre hilos benignas (GIL, asignación atómica de tupla);
# peor caso un recompute o una matriz una versión vieja que el próximo call corrige.
_MATRICES: dict[str, tuple[int, list[str], np.ndarray | None]] = {}
_VERSION: dict[str, int] = {}

_FTS5: bool | None = None


def _fts5_ok() -> bool:
    global _FTS5
    if _FTS5 is None:
        try:
            c = sqlite3.connect(":memory:")
            c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
            c.close()
            _FTS5 = True
        except sqlite3.OperationalError:
            _FTS5 = False
    return _FTS5


def _crear(con: sqlite3.Connection) -> None:
    con.executescript(f"""
    CREATE TABLE meta(clave TEXT PRIMARY KEY, valor TEXT);
    CREATE TABLE entradas(
      slug TEXT PRIMARY KEY, mtime REAL NOT NULL, titulo TEXT NOT NULL,
      fecha TEXT, cuando TEXT, capturado TEXT, lugar TEXT,
      subjects TEXT NOT NULL, tags TEXT NOT NULL, passage TEXT NOT NULL,
      vec BLOB, x REAL, y REAL);
    CREATE VIRTUAL TABLE fts USING fts5(slug UNINDEXED, titulo, cuerpo, tags, subjects,
                                        tokenize="unicode61 remove_diacritics 2");
    CREATE TABLE wikilinks(origen TEXT NOT NULL, destino TEXT NOT NULL,
                           PRIMARY KEY(origen, destino));
    CREATE INDEX ix_wl_destino ON wikilinks(destino);
    CREATE TABLE geocache(lugar TEXT PRIMARY KEY, lat REAL, lon REAL, resuelto TEXT NOT NULL);
    PRAGMA user_version = {SCHEMA_VERSION};
    """)


def abrir(root: Path) -> sqlite3.Connection | None:
    """Abre/crea el índice. None si el sqlite3 de este Python no trae FTS5
    (todo degrada al camino naive). Corrupto o de otra versión → se tira y
    se recrea: es un derivado, no hay migraciones."""
    if not _fts5_ok():
        return None
    p = root / DERIVADOS / "mem.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    for _ in (1, 2):   # 2º intento: el archivo dañado ya se tiró
        con = sqlite3.connect(p)
        try:
            con.execute("PRAGMA journal_mode=TRUNCATE")  # WAL deja -wal/-shm vivos: conflictos Dropbox
            v = con.execute("PRAGMA user_version").fetchone()[0]
            if v != SCHEMA_VERSION:
                if v != 0 or con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
                    raise sqlite3.DatabaseError(f"esquema v{v}, esperaba v{SCHEMA_VERSION}")
                _crear(con)
            return con
        except sqlite3.DatabaseError as e:
            con.close()
            p.unlink(missing_ok=True)
            try:
                memoria.log_evento(root, "indice", f"mem.db regenerado ({e})")
            except Exception:
                pass
    return None


def _meta(con, clave: str) -> str | None:
    r = con.execute("SELECT valor FROM meta WHERE clave=?", (clave,)).fetchone()
    return r[0] if r else None


def _meta_set(con, clave: str, valor: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (clave, valor))


def _modelo_actual() -> str:
    return "fake" if embed._fake() else embed.modelo()


def _puede_embeber(con, descargar: bool = False) -> bool:
    guardado = _meta(con, "modelo_embed")
    if guardado and guardado != _modelo_actual():
        return False   # cambió el modelo: no mezclar vectores; `mem index` los regenera
    if not (embed.disponible() or descargar):
        return False
    if not guardado:
        _meta_set(con, "modelo_embed", _modelo_actual())
    return True


def _indexar(con, slug: str, p: Path, mtime: float) -> None:
    post = frontmatter.load(p)
    m = post.metadata
    titulo = str(m.get("titulo") or slug)
    subjects = memoria.normalizar_subjects(m.get("subjects"))
    tags = [str(t) for t in (m.get("tags") or [])]
    cuerpo = post.content
    # el vector embebe la síntesis (titulo+resumen+tags+subjects); el FTS lleva
    # el cuerpo entero — la transcripción del adjunto se encuentra por léxico
    resumen = cuerpo.split(memoria.CAB_ADJUNTO)[0].split(memoria.CAB_REGISTRO)[0]
    resumen = " ".join(re.sub(r"^#.*$", "", resumen, flags=re.M).split())
    passage = ". ".join(x for x in (titulo, resumen, ", ".join(tags), ", ".join(subjects)) if x)
    con.execute("DELETE FROM fts WHERE slug=?", (slug,))
    con.execute("DELETE FROM wikilinks WHERE origen=?", (slug,))
    con.execute("INSERT OR REPLACE INTO entradas VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)",
                (slug, mtime, titulo, str(m.get("actualizada") or m.get("creada") or ""),
                 str(m.get("cuando") or ""), str(m.get("capturado") or ""),
                 str(m.get("lugar") or ""), json.dumps(subjects, ensure_ascii=False),
                 json.dumps(tags, ensure_ascii=False), passage))
    con.execute("INSERT INTO fts VALUES (?,?,?,?,?)",
                (slug, titulo, cuerpo, " ".join(tags), " ".join(subjects)))
    for destino in sorted(set(RX_WIKILINK.findall(cuerpo))):
        if destino != slug:
            con.execute("INSERT OR IGNORE INTO wikilinks VALUES (?,?)", (slug, destino))


def _quitar(con, slug: str) -> None:
    con.execute("DELETE FROM entradas WHERE slug=?", (slug,))
    con.execute("DELETE FROM fts WHERE slug=?", (slug,))
    con.execute("DELETE FROM wikilinks WHERE origen=?", (slug,))


def sincronizar(root: Path, con: sqlite3.Connection | None = None, descargar: bool = False,
                forzar: bool = False) -> int:
    """Pone el índice al día contra los .md (diff de mtimes). Corre al inicio de
    cada consulta y tras cada escritura del vault, así también absorbe ediciones
    externas (Obsidian, Dropbox). Devuelve cuántas entradas cambiaron.

    Con SYNC_CADA > 0 los barridos de consulta se debouncean; `forzar` (el hook
    de escritura, reindexar) salta la ventana: lo recién escrito se ve ya."""
    clave = str(root)
    if not forzar and time.monotonic() - _SYNC.get(clave, float("-inf")) < SYNC_CADA:
        return 0
    propio = con is None
    if propio and (con := abrir(root)) is None:
        return 0
    try:
        d = root / memoria.ENTRADAS
        en_disco = {p.stem: p for p in (d.glob("*.md") if d.exists() else [])}
        en_db = dict(con.execute("SELECT slug, mtime FROM entradas").fetchall())
        cambios = 0
        for slug in set(en_db) - set(en_disco):
            _quitar(con, slug)
            cambios += 1
        for slug, p in sorted(en_disco.items()):
            mtime = p.stat().st_mtime
            if en_db.get(slug) != mtime:
                _indexar(con, slug, p, mtime)
                cambios += 1
        if _puede_embeber(con, descargar):
            filas = con.execute("SELECT slug, passage FROM entradas WHERE vec IS NULL ORDER BY slug").fetchall()
            for i in range(0, len(filas), 64):
                lote = filas[i:i + 64]
                v = embed.encode([f[1] for f in lote], "passage", descargar)
                if v is None:
                    break
                for (slug, _), fila in zip(lote, v):
                    con.execute("UPDATE entradas SET vec=? WHERE slug=?", (fila.tobytes(), slug))
                cambios = cambios or 1
        if cambios:
            _meta_set(con, "proy_stamp", "")   # la proyección PCA quedó vieja
            _VERSION[clave] = _VERSION.get(clave, 0) + 1   # la matriz cacheada quedó vieja
        con.commit()
        _SYNC[clave] = time.monotonic()
        return cambios
    finally:
        if propio:
            con.close()


def _matriz(root: Path, con) -> tuple[list[str], np.ndarray | None]:
    if _meta(con, "modelo_embed") != _modelo_actual():
        return [], None   # vectores de otro modelo: no sirven (chequeo ANTES del cache)
    clave, version = str(root), _VERSION.get(str(root), 0)
    if (hit := _MATRICES.get(clave)) and hit[0] == version:
        return hit[1], hit[2]
    filas = con.execute("SELECT slug, vec FROM entradas WHERE vec IS NOT NULL ORDER BY slug").fetchall()
    if not filas or len({len(v) for _, v in filas}) != 1:
        slugs, M = [], None
    else:
        M = np.frombuffer(b"".join(v for _, v in filas), dtype=np.float32).reshape(len(filas), -1)
        slugs = [s for s, _ in filas]
    _MATRICES[clave] = (version, slugs, M)
    return slugs, M


def _consulta_fts(texto: str) -> str:
    # cada término entre comillas: el texto del usuario no puede inyectar
    # operadores de la sintaxis MATCH de FTS5
    return " OR ".join(f'"{t}"' for t in re.findall(r"\w+", texto, re.UNICODE))


def _top_coseno(root: Path, con, texto: str) -> list[str]:
    slugs, M = _matriz(root, con)
    if M is None:
        return []
    q = embed.encode([texto], "query")   # nunca descarga en una búsqueda
    if q is None or q.shape[1] != M.shape[1]:
        return []
    sims = M @ q[0]
    return [slugs[i] for i in np.argsort(-sims)[:TOP]]


def buscar_hibrida(root: Path, texto: str, k: int = 20) -> list[tuple[str, float]]:
    """BM25 + coseno fusionados por RRF (k=60). [(slug, score)] descendente.
    Sin vectores degrada a BM25 solo; sin índice devuelve []."""
    if not texto.strip() or (con := abrir(root)) is None:
        return []
    try:
        sincronizar(root, con)
        ramas = []
        if q := _consulta_fts(texto):
            ramas.append([r[0] for r in con.execute(
                "SELECT slug FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT ?", (q, TOP))])
        ramas.append(_top_coseno(root, con, texto))
        puntajes: dict[str, float] = {}
        for rama in ramas:
            for i, slug in enumerate(rama):
                puntajes[slug] = puntajes.get(slug, 0.0) + 1.0 / (RRF_K + i + 1)
        # ponytail: sin re-ranker; el punto de enchufe es reordenar este top antes del return
        return sorted(puntajes.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    finally:
        con.close()


def _vecinas(root: Path, con, slug: str, k: int, umbral: float) -> list[tuple[str, float]]:
    slugs, M = _matriz(root, con)
    if M is None or slug not in slugs:
        return []
    i = slugs.index(slug)
    sims = M @ M[i]
    return [(slugs[j], float(sims[j])) for j in np.argsort(-sims)
            if j != i and sims[j] >= umbral][:k]


def vecinas(root: Path, slug: str, k: int = 5, umbral: float = UMBRAL_VECINAS) -> list[tuple[str, float]]:
    """Las k memorias semánticamente más cercanas a `slug` (para sugerir enlaces)."""
    if (con := abrir(root)) is None:
        return []
    try:
        sincronizar(root, con)
        return _vecinas(root, con, slug, k, umbral)
    finally:
        con.close()


def wikilinks_de(root: Path, slug: str) -> tuple[list[str], list[str]]:
    """(salientes, backlinks) de los [[wikilinks]] del cuerpo."""
    if (con := abrir(root)) is None:
        return [], []
    try:
        sincronizar(root, con)
        sal = [r[0] for r in con.execute(
            "SELECT destino FROM wikilinks WHERE origen=? ORDER BY destino", (slug,))]
        back = [r[0] for r in con.execute(
            "SELECT origen FROM wikilinks WHERE destino=? ORDER BY origen", (slug,))]
        return sal, back
    finally:
        con.close()


def conexiones(root: Path, slug: str) -> dict | None:
    """Conexiones de una entrada leyendo SOLO el índice (una conexión, un sync):
    wikilinks/backlinks con título, hasta 5 entradas que comparten subject y las
    vecinas semánticas. None si no hay índice o el slug no está indexado —
    memoria.conexiones cae entonces a su scan de archivos."""
    if (con := abrir(root)) is None:
        return None
    try:
        sincronizar(root, con)
        fila = con.execute("SELECT subjects FROM entradas WHERE slug=?", (slug,)).fetchone()
        if fila is None:
            return None
        wikilinks = [{"slug": s, "titulo": t} for s, t in con.execute(
            "SELECT w.destino, e.titulo FROM wikilinks w JOIN entradas e ON e.slug = w.destino "
            "WHERE w.origen=? ORDER BY w.destino", (slug,))]   # el JOIN filtra destinos borrados
        backlinks = [{"slug": s, "titulo": t} for s, t in con.execute(
            "SELECT w.origen, e.titulo FROM wikilinks w JOIN entradas e ON e.slug = w.origen "
            "WHERE w.destino=? ORDER BY w.origen", (slug,))]
        entradas = [{"slug": s, "titulo": t} for s, t in con.execute(
            "SELECT DISTINCT e.slug, e.titulo FROM entradas e, json_each(e.subjects) j "
            "WHERE e.slug != ? AND j.value IN (SELECT value FROM json_each(?)) "
            "ORDER BY e.slug LIMIT 5", (slug, fila[0]))]
        titulos = dict(con.execute("SELECT slug, titulo FROM entradas"))
        enlazadas = {slug, *(f["slug"] for f in wikilinks), *(f["slug"] for f in backlinks)}
        relacionadas = [{"slug": s, "titulo": titulos.get(s, s), "score": round(sc, 2)}
                        for s, sc in _vecinas(root, con, slug, 5, UMBRAL_VECINAS)
                        if s not in enlazadas]
        return {"entradas": entradas, "wikilinks": wikilinks,
                "backlinks": backlinks, "relacionadas": relacionadas}
    finally:
        con.close()


def sugerencias(root: Path, texto: str, k: int = 4, proyecto: str | None = None) -> list[dict]:
    """Memorias MUY cercanas a un texto que se está escribiendo ("recordar
    mientras uno escribe"). Precisión sobre exhaustividad: pide las DOS señales
    a la vez —match léxico (BM25) y z ≥ UMBRAL_SUGERENCIA sobre la base, esta
    última recién a partir de MIN_BASE_Z memorias—, así que un borrador sin nada
    que ver devuelve [] en vez de rellenar con vecinos lejanos.
    Devuelve [{slug, titulo, privada, z}] de más a menos cercana."""
    if len((texto or "").strip()) < 12 or (con := abrir(root)) is None:
        return []
    try:
        sincronizar(root, con)
        if not (q := _consulta_fts(texto)):
            return []
        fts = [r[0] for r in con.execute(
            "SELECT slug, rank FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT 8", (q,))
            if r[1] <= UMBRAL_BM25]
        slugs, M = _matriz(root, con)
        if not fts or M is None:
            return []
        v = embed.encode([texto], "query")   # nunca descarga: esto corre al teclear
        if v is None or v.shape[1] != M.shape[1]:
            return []
        sims = M @ v[0]
        mu, sd = float(sims.mean()), float(sims.std()) or 1e-9
        z = {slug: (float(sims[i]) - mu) / sd for i, slug in enumerate(slugs)}
        titulos = dict(con.execute("SELECT slug, titulo FROM entradas"))
        minimo = UMBRAL_SUGERENCIA if len(slugs) >= MIN_BASE_Z else -9.0
        cerca = sorted((x for x in fts if x in z and z[x] >= minimo), key=lambda s: -z[s])[:k]
        # `privada` no está en el índice (es del frontmatter): con ≤ k archivos
        # sale más barato leerlos que arrastrar una columna y migrar el esquema.
        # De paso sale el filtro por proyecto: sugerir el TÍTULO de una memoria
        # privada de otro proyecto ya sería mostrarla.
        fichas = [(s, _meta_entrada(root, s)) for s in cerca]
        return [{"slug": s, "titulo": titulos.get(s, s), "z": round(z[s], 2),
                 "privada": bool(m.get("privada"))}
                for s, m in fichas if memoria.accesible(m, proyecto)]
    finally:
        con.close()


def _meta_entrada(root: Path, slug: str) -> dict:
    try:
        return frontmatter.load(root / memoria.ENTRADAS / f"{slug}.md").metadata
    except Exception:
        return {"privada": True}    # ante la duda, privada: el candado no se abre solo


def duplicados(root: Path, umbral: float = UMBRAL_DUPLICADOS) -> list[tuple[str, str, float]]:
    """Pares de memorias casi idénticas por coseno (para el lint).

    ponytail: O(n²) sobre la matriz entera; alcanza hasta ~5k entradas."""
    if (con := abrir(root)) is None:
        return []
    try:
        sincronizar(root, con)
        slugs, M = _matriz(root, con)
        if M is None or len(slugs) < 2:
            return []
        S = M @ M.T
        ii, jj = np.where(np.triu(S, 1) >= umbral)
        return sorted(((slugs[i], slugs[j], float(S[i, j])) for i, j in zip(ii, jj)),
                      key=lambda x: -x[2])
    finally:
        con.close()


def _grupo(subjects_json: str) -> str:
    subs = json.loads(subjects_json)
    return subs[0].split("/")[0] if subs else "—"


def proyeccion(root: Path, proyecto: str | None = None) -> list[dict]:
    """Mapa semántico: los embeddings proyectados a 2D, cacheado en el índice.

    ponytail: PCA por SVD (numpy puro); UMAP si algún día los clusters de PCA
    no alcanzan — la interfaz no cambia."""
    if (con := abrir(root)) is None:
        return []
    try:
        sincronizar(root, con)
        slugs, M = _matriz(root, con)
        if M is None or len(slugs) < 3:
            return []
        if _meta(con, "proy_stamp") != "ok":
            C = M - M.mean(axis=0)
            U, S, _ = np.linalg.svd(C, full_matrices=False)
            xy = U[:, :2] * S[:2]
            xy = xy / max(float(np.abs(xy).max()), 1e-9)
            for slug, (x, y) in zip(slugs, xy):
                con.execute("UPDATE entradas SET x=?, y=? WHERE slug=?", (float(x), float(y), slug))
            _meta_set(con, "proy_stamp", "ok")
            con.commit()
        oc = memoria.entradas_ocultas(root, proyecto)
        return [{"slug": s, "titulo": t, "x": x, "y": y, "grupo": _grupo(subj)}
                for s, t, x, y, subj in con.execute(
                    "SELECT slug, titulo, x, y, subjects FROM entradas "
                    "WHERE x IS NOT NULL ORDER BY slug") if s not in oc]
    finally:
        con.close()


def _cercanas(root: Path, con: sqlite3.Connection) -> dict[str, list[tuple[str, float]]]:
    """Las (hasta 3) vecinas semánticas de cada entrada, cos ≥ umbral."""
    slugs, M = _matriz(root, con)
    if M is None or len(slugs) < 2:
        return {}
    S = M @ M.T
    return {slug: [(slugs[j], float(S[i, j])) for j in np.argsort(-S[i])[:4]
                   if j != i and S[i, j] >= UMBRAL_VECINAS][:3]
            for i, slug in enumerate(slugs)}


def _hubs(filas: list, col: int, prefijo: str, tipo: str) -> tuple[list, list]:
    """Nodos-eje por un valor compartido (lugar, tag): dos memorias con el mismo
    valor quedan conectadas A TRAVÉS de él, que además dice POR QUÉ lo están —
    el mismo patrón que ya usan los subjects, y O(n) en vez del O(n²) de unir
    cada par. Un valor que aparece en UNA sola memoria no conecta nada: se cae."""
    cuenta: dict[str, int] = {}
    for fila in filas:
        for v in _valores(fila[col]):
            cuenta[v] = cuenta.get(v, 0) + 1
    nodos = [{"id": f"{prefijo}:{v}", "tipo": tipo, "label": v, "grupo": "—"}
             for v in sorted(cuenta) if cuenta[v] > 1]
    aristas = [{"a": f"e:{fila[0]}", "b": f"{prefijo}:{v}", "tipo": tipo}
               for fila in filas for v in _valores(fila[col]) if cuenta[v] > 1]
    return nodos, aristas


def _valores(campo: str) -> list[str]:
    """Los valores de una celda: un JSON de lista (tags) o un texto suelto (lugar)."""
    if not campo:
        return []
    if campo.startswith("["):
        return [str(x).strip() for x in json.loads(campo) if str(x).strip()]
    return [campo.strip()]


def grafo(root: Path, slugs: list[str] | None = None, proyecto: str | None = None) -> dict:
    """Grafo de memorias + ejes por los que se conectan: subject (jerárquico),
    lugar y tag, más las aristas directas de wikilink y similitud semántica
    (top-3 por nodo, cos ≥ 0.55, con su peso para poder filtrar por cercanía).

    Sin `slugs`, la base entera. Con `slugs`, solo esas memorias y su vecindario
    (vecinas semánticas + con quien se enlazan): el mapa de UNA sesión. Una lista
    vacía devuelve un grafo vacío — es "nada seleccionado", no "todo"."""
    if (con := abrir(root)) is None:
        return {"nodos": [], "aristas": []}
    try:
        sincronizar(root, con)
        filas = con.execute(
            "SELECT slug, titulo, subjects, lugar, tags FROM entradas ORDER BY slug").fetchall()
        # el índice no guarda `privada`: el filtro por proyecto se aplica acá,
        # antes de contar ejes — un nodo privado de otro proyecto no existe
        if oc := memoria.entradas_ocultas(root, proyecto):
            filas = [f for f in filas if f[0] not in oc]
        nodos, aristas, vistos = [], [], set()
        vivos = {f[0] for f in filas}
        enlaces = [(o, d) for o, d in con.execute("SELECT origen, destino FROM wikilinks") if d in vivos]
        cercanas = _cercanas(root, con)
        if slugs is not None:
            foco = {s for s in slugs if s in vivos}
            vivos = set(foco)
            for s in foco:
                vivos |= {otro for otro, _ in cercanas.get(s, ())}
                vivos |= {d for o, d in enlaces if o == s} | {o for o, d in enlaces if d == s}
            filas = [f for f in filas if f[0] in vivos]
        # los ejes se cuentan sobre las filas YA acotadas: en el mapa de una
        # sesión, un tag que solo tiene una memoria visible no dibuja un eje suelto
        for prefijo, tipo, col in (("l", "lugar", 3), ("t", "tag", 4)):
            ns, ls = _hubs(filas, col, prefijo, tipo)
            nodos += ns
            aristas += ls
        for slug, titulo, subjects, _lugar, _tags in filas:
            subs = json.loads(subjects)
            nodos.append({"id": f"e:{slug}", "tipo": "memoria", "label": titulo,
                          "grupo": subs[0].split("/")[0] if subs else "—"})
            for s in subs:
                partes = s.split("/")
                for i in range(len(partes)):
                    path = "/".join(partes[:i + 1])
                    if path not in vistos:
                        vistos.add(path)
                        nodos.append({"id": f"s:{path}", "tipo": "subject",
                                      "label": partes[i], "grupo": partes[0]})
                        if i:
                            aristas.append({"a": f"s:{'/'.join(partes[:i])}", "b": f"s:{path}",
                                            "tipo": "subject"})
                aristas.append({"a": f"e:{slug}", "b": f"s:{s}", "tipo": "subject"})
        pares = set()
        for origen, destino in enlaces:
            if origen in vivos and destino in vivos:
                pares.add(frozenset((origen, destino)))
                aristas.append({"a": f"e:{origen}", "b": f"e:{destino}", "tipo": "wikilink"})
        for slug, vecinos in cercanas.items():
            if slug not in vivos:
                continue
            for otro, cos in vecinos:
                par = frozenset((slug, otro))
                if otro in vivos and par not in pares:
                    pares.add(par)
                    aristas.append({"a": f"e:{slug}", "b": f"e:{otro}",
                                    "tipo": "semantico", "peso": round(cos, 3)})
        return {"nodos": nodos, "aristas": aristas}
    finally:
        con.close()


def _geocodificar(lugar: str) -> tuple[float, float] | None:
    """Una consulta a Nominatim. None = respondió pero no encontró el lugar.
    Errores de red suben: el caller decide no insistir en esta pasada."""
    url = NOMINATIM + "?" + urllib.parse.urlencode({"q": lugar, "format": "json", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=10) as r:
        datos = json.loads(r.read().decode("utf-8"))
    return (float(datos[0]["lat"]), float(datos[0]["lon"])) if datos else None


def lugar_de_coords(coords: str) -> str:
    """"lat,lon" → nombre legible ("Miraflores, Lima, Perú"), o "" si no se pudo.

    Es la inversa de `_geocodificar` y la usa el procesador del inbox para que el
    .md guarde un lugar que un humano entienda, no solo dos números. Sin cache:
    corre UNA vez por captura y el resultado queda escrito en el Markdown para
    siempre. Va espaciada de sobra respecto a la política de Nominatim (1 req/s)
    porque entre dos capturas hay una llamada al LLM."""
    try:
        lat, lon = (float(x) for x in coords.split(","))
    except (ValueError, TypeError):
        return ""
    url = NOMINATIM.replace("/search", "/reverse") + "?" + urllib.parse.urlencode(
        {"lat": lat, "lon": lon, "format": "json", "zoom": 14})   # zoom 14 = barrio/pueblo
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            datos = json.loads(r.read().decode("utf-8"))
    except Exception:
        return ""   # sin red o Nominatim caído: quedan las coordenadas, que es lo que importa
    a = datos.get("address") or {}
    partes = [a.get(k) for k in ("neighbourhood", "suburb", "city", "town", "village",
                                 "state", "country")]
    vistos = list(dict.fromkeys(p for p in partes if p))
    return ", ".join(vistos[:3]) or str(datos.get("display_name") or "")


def mapa(root: Path, max_geocode: int = 5, proyecto: str | None = None) -> dict:
    """Memorias agrupadas por lugar con lat/lon. Geocodifica a lo sumo
    `max_geocode` lugares nuevos por llamada (política Nominatim: 1 req/s,
    resultado SIEMPRE cacheado; un lugar irresoluble no se reintenta)."""
    if (con := abrir(root)) is None:
        return {"lugares": [], "sin_geo": []}
    try:
        sincronizar(root, con)
        grupos: dict[str, dict] = {}
        oc = memoria.entradas_ocultas(root, proyecto)
        for slug, titulo, lugar in con.execute(
                "SELECT slug, titulo, lugar FROM entradas WHERE lugar != '' ORDER BY slug"):
            if slug in oc:
                continue
            clave = memoria._norm(lugar).strip()
            g = grupos.setdefault(clave, {"lugar": lugar, "memorias": []})
            g["memorias"].append({"slug": slug, "titulo": titulo})
        cache = {l: (lat, lon) for l, lat, lon in con.execute("SELECT lugar, lat, lon FROM geocache")}
        pendientes = [c for c in sorted(grupos) if c not in cache]
        for n, clave in enumerate(pendientes[:max_geocode]):
            if n:
                time.sleep(1.1)   # política Nominatim: máximo 1 request por segundo
            try:
                r = _geocodificar(grupos[clave]["lugar"])
            except Exception:
                break   # sin red: no gastar más intentos en esta pasada
            cache[clave] = (r[0], r[1]) if r else (None, None)
            con.execute("INSERT OR REPLACE INTO geocache VALUES (?,?,?,?)",
                        (clave, cache[clave][0], cache[clave][1],
                         datetime.now().isoformat(timespec="seconds")))
        con.commit()
        lugares, sin_geo = [], []
        for clave in sorted(grupos):
            lat, lon = cache.get(clave, (None, None))
            g = grupos[clave]
            if lat is None:
                sin_geo.append(g["lugar"])
            else:
                lugares.append({"lugar": g["lugar"], "lat": lat, "lon": lon,
                                "n": len(g["memorias"]), "memorias": g["memorias"]})
        return {"lugares": lugares, "sin_geo": sin_geo}
    finally:
        con.close()


def estado(root: Path) -> dict:
    """Sonda barata: ¿la búsqueda es híbrida o degradó a léxica, y por qué?
    No sincroniza ni carga la matriz. Los motivos NUNCA llevan rutas ni ".md"
    (romperían el regex con que el chat extrae páginas de los resultados)."""
    if (con := abrir(root)) is None:
        return {"hibrida": False, "motivo": "el sqlite3 de este Python no trae FTS5"}
    try:
        guardado = _meta(con, "modelo_embed")
        if not guardado:
            return {"hibrida": False, "motivo": "sin modelo de embeddings; reindexar lo descarga"}
        if guardado != _modelo_actual():
            return {"hibrida": False, "motivo": "cambió el modelo de embeddings; falta reindexar"}
        if not con.execute("SELECT count(vec) FROM entradas").fetchone()[0]:
            return {"hibrida": False, "motivo": "sin vectores; falta reindexar"}
        return {"hibrida": True, "motivo": "ok"}
    finally:
        con.close()


def reindexar(root: Path, geo: bool = True, max_geocode: int = 500) -> dict:
    """Rebuild total del índice (la geocache se conserva). Es la única operación
    que descarga el modelo de embeddings si no está."""
    t0 = time.time()
    if (con := abrir(root)) is None:
        return {"error": "el sqlite3 de este Python no trae FTS5; búsqueda naive activa"}
    try:
        for tabla in ("entradas", "fts", "wikilinks", "meta"):
            con.execute(f"DELETE FROM {tabla}")
        con.commit()
        sincronizar(root, con, descargar=True, forzar=True)   # forzar: recién vaciado
        n, con_vec = con.execute("SELECT count(*), count(vec) FROM entradas").fetchone()
    finally:
        con.close()
    geocodificados = len(mapa(root, max_geocode=max_geocode)["lugares"]) if geo else 0
    proyeccion(root)
    memoria.log_evento(root, "indice", f"reindexado: {n} entradas, {con_vec} con embedding")
    return {"entradas": n, "con_embedding": con_vec,
            "geocodificados": geocodificados, "ms": int((time.time() - t0) * 1000)}
