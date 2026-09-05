"""Backup de la memoria fuera del vault: sacarla y volver a meterla.

Export e import viven juntos porque son la misma decisión leída al derecho y al
revés: qué de una memoria escribió Diego y qué le agregó la máquina después. Al
backup le sobra lo segundo — vuelve a salir solo procesando de nuevo.

Se exporta el texto tal como se capturó + `capturado`, `tipo`, `origen`, el
contexto que escribió Diego, las coordenadas y el lugar del GPS, el proyecto, el
adjunto, y los tags/subjects SOLO si estaban fijados (lo demás lo clasificó el
LLM). No viajan: título, síntesis, registro histórico, enlaces ni las páginas de
síntesis.

Al importar, lo que el vault ya vio (por id, papelera incluida) se saltea, lo
casi idéntico se fusiona con la entrada que ya existía, y el resto entra al
inbox como captura nueva con SU fecha original.
"""

import re
import shutil
from datetime import datetime
from pathlib import Path

import frontmatter
import numpy as np

from . import embed, indice, lint, memoria, sesiones

SESIONES = "10_Sesiones"
ADJUNTOS = "07_Inbox/_adjuntos"
SIEMPRE = ("id", "capturado", "tipo")   # aunque vengan vacíos: son la identidad

LEEME = """# Backup de MeM

- `memorias/` — una por captura, agrupadas por año. Lo que se guardó, sin lo que
  el procesador dedujo después (título, síntesis, registro histórico).
- `sesiones/` — las conversaciones enteras, tal cual.
- `adjuntos/` — las fotos, audios y archivos que las memorias nombran.

Todo es Markdown y se lee con cualquier editor. Para volver a meterlo en MeM:
Ajustes › Procesamiento › Importar, apuntando a esta carpeta.
"""


def _limpio(campos: dict) -> dict:
    return {k: v for k, v in campos.items() if v or k in SIEMPRE}


def _pura(meta: dict) -> dict:
    """Los campos de un item de inbox que son de Diego, no del procesador.

    `fijado` marca lo que él eligió a mano; lo que no está ahí lo puso el LLM y
    se rehace al procesar. `lugar_captura` sí viaja: es el nombre mecánico de
    `coords_captura` (geocoding inverso), no una lectura del modelo."""
    fij = [c for c in (meta.get("fijado") or []) if c in ("tags", "subjects")]
    return {
        "id": str(meta.get("id") or ""),
        "capturado": str(meta.get("capturado") or ""),
        "tipo": str(meta.get("tipo") or "nota"),
        "origen": str(meta.get("origen") or ""),
        "contexto_usuario": str(meta.get("contexto_usuario") or ""),
        "tags": list(meta.get("tags") or []) if "tags" in fij else [],
        "subjects": list(meta.get("subjects") or []) if "subjects" in fij else [],
        "fijado": fij,
        "proyecto": str(meta.get("proyecto") or ""),
        "coords_captura": str(meta.get("coords_captura") or ""),
        "lugar_captura": str(meta.get("lugar_captura") or ""),
        "adjunto": str(meta.get("adjunto") or ""),
    }


def _resumen(cuerpo: str) -> str:
    """El texto de una entrada sin sus encabezados, adjunto ni registro."""
    txt = cuerpo.split(memoria.CAB_ADJUNTO)[0].split(memoria.CAB_REGISTRO)[0]
    return re.sub(r"^#+ .*$", "", txt, flags=re.M).strip()


def _memorias(root: Path):
    """(nombre de archivo, campos puros, cuerpo) de todo lo capturado.

    Tres fuentes: lo pendiente del inbox, lo ya procesado (que conserva el texto
    crudo al lado de la entrada que generó) y las entradas que nacieron sin
    pasar por el inbox (MCP, taller de medios) — de esas, lo capturado es el
    título y el resumen, no hay original aparte."""
    ids, con_captura = set(), set()
    for carpeta in ("07_Inbox", "07_Inbox/_procesado"):
        for p in sorted((root / carpeta).glob("*.md")):
            post = frontmatter.load(p)
            iid = str(post.metadata.get("id") or p.stem)
            # el item de una sesión es su reflejo: la sesión entera ya se copia
            # aparte, y exportar las dos la duplicaría al volver
            if iid.startswith("sesion-") or str(post.metadata.get("tipo")) == "sesion":
                continue
            if iid in ids:
                continue
            ids.add(iid)
            if slug := Path(str(post.metadata.get("entrada") or "")).stem:
                con_captura.add(slug)
            yield p.stem, _pura(post.metadata), post.content

    for p in sorted((root / memoria.ENTRADAS).glob("*.md")):
        if p.stem in con_captura:
            continue
        post = frontmatter.load(p)
        meta = post.metadata
        if meta.get("sintesis_de"):
            continue   # una síntesis ES procesamiento: se rehace sola
        creada = str(meta.get("creada") or "")
        titulo = str(meta.get("titulo") or p.stem)
        campos = {
            "id": str(meta.get("id") or ""),
            "capturado": str(meta.get("capturado") or "") or creada,
            "tipo": "nota",
            "origen": str(meta.get("origen") or ""),
            "contexto_usuario": "",
            # acá nadie clasificó texto crudo: los temas se los dio quien la creó
            "tags": list(meta.get("tags") or []),
            "subjects": list(meta.get("subjects") or []),
            "fijado": ["tags", "subjects"],
            "proyecto": next(iter(memoria.proyectos_de(meta.get("subjects"))), ""),
            "coords_captura": str(meta.get("coords_captura") or ""),
            "lugar_captura": str(meta.get("lugar_captura") or ""),
            "adjunto": str(meta.get("adjunto") or ""),
        }
        yield f"{creada or '0000'}_{p.stem}", campos, f"{titulo}\n\n{_resumen(post.content)}"


def _sesiones(root: Path):
    """Las conversaciones que existen de verdad. Las `temporal` son borradores
    que ni salen en la lista, y las de _papelera se borraron a propósito."""
    d = root / SESIONES
    for p in sorted([*d.glob("*.md"), *d.glob("_archivo/*/*.md")]):
        try:
            estado = str(frontmatter.load(p).metadata.get("estado") or "")
        except Exception:
            continue
        if estado in ("activa", "archivada"):
            yield p


def _copiar_adjunto(root: Path, carpeta: Path, ruta: str, hechos: dict) -> str:
    """Copia el adjunto al backup y devuelve su nombre ahí; "" si ya no está."""
    if ruta in hechos:
        return hechos[ruta]
    src = (root / ruta).resolve()
    if root.resolve() not in src.parents or not src.is_file():
        return ""
    destino = carpeta / "adjuntos"
    destino.mkdir(parents=True, exist_ok=True)
    nombre, n = src.name, 1
    # dos archivos distintos con el mismo nombre: en un backup, pisar uno es
    # perderlo. Los nombres traen timestamp, así que esto casi nunca corre.
    while (q := destino / nombre).exists() and q.stat().st_size != src.stat().st_size:
        n += 1
        nombre = f"{src.stem}-{n}{src.suffix}"
    shutil.copy2(src, destino / nombre)
    hechos[ruta] = nombre
    return nombre


def exportar(root: Path, destino: Path) -> dict:
    """Escribe el backup en `destino/MeM_export_<fecha>/`. Solo lee del vault."""
    root, destino = Path(root), Path(destino)
    if not destino.is_absolute():
        raise ValueError("la carpeta tiene que ser una ruta absoluta")
    fuera = destino.resolve()
    if fuera == root.resolve() or root.resolve() in fuera.parents:
        raise ValueError("el backup no puede vivir adentro del vault")
    carpeta = fuera / f"MeM_export_{datetime.now():%Y-%m-%d_%H%M%S}"
    (carpeta / "memorias").mkdir(parents=True, exist_ok=True)

    adjuntos, n = {}, 0
    for nombre, campos, cuerpo in _memorias(root):
        if campos["adjunto"]:
            campos["adjunto"] = _copiar_adjunto(root, carpeta, campos["adjunto"], adjuntos)
        anio = (campos["capturado"] or "0000")[:4] or "0000"
        (carpeta / "memorias" / anio).mkdir(exist_ok=True)
        post = frontmatter.Post(cuerpo.strip() + "\n", **_limpio(campos))
        (carpeta / "memorias" / anio / f"{nombre}.md").write_text(
            frontmatter.dumps(post), encoding="utf-8")
        n += 1

    s = 0
    for p in _sesiones(root):
        (carpeta / "sesiones").mkdir(exist_ok=True)
        shutil.copy2(p, carpeta / "sesiones" / p.name)
        s += 1

    (carpeta / "LEEME.md").write_text(LEEME, encoding="utf-8")
    memoria.log_evento(root, "backup", f"exportadas {n} memorias y {s} sesiones a {carpeta}")
    return {"memorias": n, "sesiones": s, "adjuntos": len(adjuntos), "carpeta": str(carpeta)}


def _ids_existentes(root: Path) -> set[str]:
    """Todo id que el vault ya vio, papelera incluida: reimportar un backup no
    puede resucitar lo que se borró a propósito. `id_origen` es el rastro que
    deja el import, para reconocer lo suyo aunque acá tenga otro id.

    ponytail: relee el frontmatter del vault entero (decenas de ms con ~10³
    archivos); si molesta, una tabla en mem.db."""
    ids = set()
    for pat in ("06_Biblioteca_Conocimiento/Entradas/*.md",
                "06_Biblioteca_Conocimiento/_papelera/**/*.md",
                "07_Inbox/*.md", "07_Inbox/_procesado/*.md", "07_Inbox/_papelera/**/*.md"):
        for p in root.glob(pat):
            try:
                meta = frontmatter.load(p).metadata
            except Exception:
                continue
            if iid := str(meta.get("id") or ""):
                ids.add(iid)
            ids.update(str(x) for x in (meta.get("id_origen") or []) if x)
    return ids


def _matriz_dedup(root: Path):
    """La Biblioteca vectorizada, UNA vez para todo el import.

    ponytail: no se recalcula al fusionar — dos importados casi idénticos entre
    sí entran los dos y el lint los cruza después."""
    if not embed.disponible() or (con := indice.abrir(root)) is None:
        return [], None
    try:
        indice.sincronizar(root, con)
        return indice._matriz(root, con)
    finally:
        con.close()


def _passage(texto: str, meta: dict) -> str:
    """El texto armado como lo indexa indice._indexar, para comparar como con
    como: contra el vector de una entrada, que lleva sus tags y temas adentro."""
    partes = (" ".join(texto.split()), ", ".join(meta.get("tags") or []),
              ", ".join(meta.get("subjects") or []))
    return ". ".join(x for x in partes if x)


def _similar(root: Path, slugs: list, M, texto: str, meta: dict) -> str:
    """Slug de una entrada casi idéntica, o "". Dos señales como el lint: el
    coseno solo confunde "mismo tema" con "misma cosa"; las palabras confirman.

    Compara contra el resumen de la entrada, no contra su archivo: el registro
    histórico y el frontmatter son andamiaje y arruinan el Jaccard."""
    if M is None or not texto:
        return ""
    q = embed.encode([_passage(texto, meta)], "passage")
    if q is None or q.shape[1] != M.shape[1]:
        return ""
    sims = M @ q[0]
    i = int(np.argmax(sims))
    if sims[i] < indice.UMBRAL_DUPLICADOS:
        return ""
    p = root / memoria.ENTRADAS / f"{slugs[i]}.md"
    if not p.is_file():
        return ""
    resumen = _resumen(frontmatter.load(p).content)
    return slugs[i] if lint.solape(texto, resumen) >= lint.MIN_SOLAPE else ""


def _restaurar_adjunto(root: Path, origen: Path, nombre: str) -> str:
    src = origen / "adjuntos" / Path(nombre).name
    if not src.is_file():
        return ""
    rel = f"{ADJUNTOS}/{src.name}"
    dst = root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(src, dst)
    return rel


def importar(root: Path, origen: Path) -> dict:
    """Mete un backup al vault: lo conocido se saltea, lo casi idéntico se
    fusiona con su entrada, lo nuevo entra al inbox con su fecha original."""
    root, origen = Path(root), Path(origen)
    if not (origen / "memorias").is_dir():
        # apuntar a la carpeta que CONTIENE los backups es lo natural (es la misma
        # que se elige para exportar); de varios, el último — restaurar es querer
        # el más nuevo, y el nombre lleva la fecha, así que ordenar alcanza
        hijos = sorted(origen.glob("MeM_export_*/memorias"))
        if not hijos:
            raise ValueError("ahí no hay un backup de MeM (falta la carpeta memorias/)")
        origen = hijos[-1].parent

    ids = _ids_existentes(root)
    slugs, M = _matriz_dedup(root)
    r = {"nuevas": 0, "fusionadas": 0, "iguales": 0, "sesiones": 0,
         "adjuntos_faltantes": 0, "errores": 0}

    for p in sorted((origen / "memorias").rglob("*.md")):
        try:
            post = frontmatter.load(p)
            meta, texto = post.metadata, post.content.strip()
            iid = str(meta.get("id") or "")
            nombre = str(meta.get("adjunto") or "")
            # una foto o un PDF sin una palabra escrita ES una memoria — y la que
            # más se perdería. Vacía de verdad es la que no trae ni archivo
            if not texto and not nombre:
                r["errores"] += 1
                continue
            if iid and iid in ids:
                r["iguales"] += 1
                continue
            rastro = [iid] if iid else []
            if slug := _similar(root, slugs, M, texto, meta):
                cap = str(meta.get("capturado") or "")
                # guardar_entrada fecha la línea con hoy; la de verdad va adentro
                memoria.guardar_entrada(
                    root, str(memoria.leer_entrada(root, slug).get("titulo") or slug),
                    f"(capturado {cap}) {texto}" if cap else texto,
                    list(meta.get("subjects") or []), origen="import",
                    tags=list(meta.get("tags") or []), slug=slug,
                    meta={"id_origen": rastro})
                r["fusionadas"] += 1
            else:
                adj = ""
                if nombre and not (adj := _restaurar_adjunto(root, origen, nombre)):
                    r["adjuntos_faltantes"] += 1
                memoria.capturar(
                    root, texto, tipo=str(meta.get("tipo") or "nota"),
                    contexto=str(meta.get("contexto_usuario") or ""),
                    tags=list(meta.get("tags") or []), subjects=list(meta.get("subjects") or []),
                    adjunto=adj, origen=str(meta.get("origen") or "import"),
                    coords=str(meta.get("coords_captura") or ""),
                    proyecto=str(meta.get("proyecto") or ""),
                    capturado=str(meta.get("capturado") or ""),
                    extra={"fijado": list(meta.get("fijado") or []), "id_origen": rastro,
                           **({"lugar_captura": lc} if (lc := str(meta.get("lugar_captura") or "")) else {})})
                r["nuevas"] += 1
            if iid:
                ids.add(iid)
        except Exception as e:
            r["errores"] += 1
            memoria.log_evento(root, "backup", f"import de {p.name} falló: {e}")

    d = root / SESIONES
    for p in sorted((origen / "sesiones").glob("*.md")):
        sid = p.stem
        if (d / f"{sid}.md").exists() or (d / "_papelera" / f"{sid}.md").exists() \
                or any(d.glob(f"_archivo/*/{sid}.md")):
            continue
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, d / f"{sid}.md")
        r["sesiones"] += 1

    memoria.log_evento(root, "backup", f"import desde {origen}: {r}")
    return r


def demo() -> None:
    import os
    import tempfile
    os.environ["MEM_EMBED_FAKE"] = "1"
    with tempfile.TemporaryDirectory() as tmp:
        a, b, bak = Path(tmp) / "a", Path(tmp) / "b", Path(tmp) / "bak"
        memoria.capturar(a, "la birome azul quedó en el cajón de la cocina",
                         tags=["casa"], subjects=["Casa/Objetos"])
        memoria.guardar_entrada(a, "Cafetera", "la cafetera italiana es de tres pocillos",
                                ["Casa/Objetos"], origen="mcp", tags=["casa"])
        (a / ADJUNTOS).mkdir(parents=True, exist_ok=True)
        (a / ADJUNTOS / "foto.jpg").write_bytes(b"jpg")
        memoria.capturar(a, "", tipo="imagen", adjunto=f"{ADJUNTOS}/foto.jpg")
        sid = sesiones.crear(a, titulo="charla")
        sesiones.agregar_mensaje(a, sid, "Diego", "hola")
        sesiones.crear(a, titulo="borrador", estado="temporal")

        r = exportar(a, bak)
        assert (r["memorias"], r["sesiones"], r["adjuntos"]) == (3, 1, 1), r   # la temporal no va
        carpeta = Path(r["carpeta"])
        crudo = "\n".join(p.read_text(encoding="utf-8") for p in carpeta.glob("memorias/*/*.md"))
        assert memoria.CAB_REGISTRO not in crudo and "titulo:" not in crudo, \
            "el procesamiento no viaja al backup"
        assert "capturado:" in crudo and "Casa/Objetos" in crudo

        r2 = importar(b, carpeta)
        assert (r2["nuevas"], r2["fusionadas"], r2["iguales"], r2["sesiones"]) == (3, 0, 0, 1), r2
        capturas = sorted((b / "07_Inbox").glob("*.md"))
        assert len(capturas) == 3
        orig = frontmatter.load(sorted((a / "07_Inbox").glob("*.md"))[0]).metadata
        copia = [frontmatter.load(p).metadata for p in capturas]
        assert any(m["capturado"] == orig["capturado"] for m in copia), "vuelve con SU fecha"
        assert any(m["fijado"] == ["tags", "subjects"] for m in copia), "el fijado original se respeta"

        foto = [m for m in copia if m.get("tipo") == "imagen"]
        assert foto and (b / str(foto[0]["adjunto"])).is_file(), \
            "una memoria que es solo una foto vuelve con su archivo"

        assert importar(b, carpeta)["iguales"] == 3, "importar dos veces no duplica"
        r4 = importar(a, carpeta)
        assert (r4["nuevas"], r4["fusionadas"], r4["iguales"], r4["sesiones"]) == (0, 0, 3, 0), r4

        # un backup viejo, con otros ids, que dice lo mismo que una entrada de
        # hoy: no se puede reconocer por id y tiene que fusionarse, no duplicar
        fake = Path(tmp) / "fake" / "memorias" / "2026"
        fake.mkdir(parents=True)
        vieja = frontmatter.load(next(carpeta.glob("memorias/*/*cafetera*.md")))
        vieja.metadata.update(id="de-otro-lado", capturado="2024-03-02T10:00:00")
        (fake / "x.md").write_text(frontmatter.dumps(vieja), encoding="utf-8")
        r5 = importar(a, Path(tmp) / "fake")
        assert (r5["fusionadas"], r5["nuevas"]) == (1, 0), r5
        cuerpo = (a / memoria.ENTRADAS / "cafetera.md").read_text(encoding="utf-8")
        assert "(capturado 2024-03-02T10:00:00)" in cuerpo, "la fecha original no se pierde"
        assert importar(a, Path(tmp) / "fake")["iguales"] == 1, "fusionar deja rastro"

        # un adjunto que no llegó al backup no puede tumbar el import entero
        solo = Path(tmp) / "solo" / "memorias" / "2026"
        solo.mkdir(parents=True)
        (solo / "y.md").write_text(frontmatter.dumps(frontmatter.Post(
            "una nota con foto", id="sin-foto", capturado="2024-04-01T09:00:00",
            tipo="imagen", adjunto="perdida.jpg")), encoding="utf-8")
        r6 = importar(a, Path(tmp) / "solo")
        assert (r6["nuevas"], r6["adjuntos_faltantes"], r6["errores"]) == (1, 1, 0), r6

        for mala, msg in (((a / "07_Inbox"), "adentro del vault"), (Path("relativa"), "absoluta")):
            try:
                exportar(a, mala)
                raise AssertionError(f"tenía que fallar: {msg}")
            except ValueError:
                pass
    print("backup ok")


if __name__ == "__main__":
    demo()
