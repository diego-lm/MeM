"""Sesiones de trabajo (pantalla Think): un archivo Markdown por sesión en 10_Sesiones/.

Una sesión vive sobre uno o más subjects y tiene un modo de vista activo
(chat, qa, …) intercambiable en cualquier momento — mismos datos, otra vista.
Frontmatter = metadatos + resumen rodante; cuerpo = mensajes '## [ts] Rol'.
Legible y editable por humanos y por cualquier agente sin este app.
"""
import re
from datetime import datetime
from pathlib import Path

import frontmatter

from .memoria import slugificar

RX_MSG = re.compile(r"^## \[(.+?)\] (Diego|Asistente)\s*$", re.M)

# 2026-08: el modo "crear" pasó a llamarse "media". Las sesiones viejas lo traen
# escrito en su frontmatter; se normaliza al LEER y nada se reescribe en masa.
_RENOMBRES = {"crear": "media"}


def _norm(meta: dict) -> dict:
    if meta.get("modo") in _RENOMBRES:
        meta["modo"] = _RENOMBRES[meta["modo"]]
    if meta.get("modos_usados"):
        meta["modos_usados"] = list(dict.fromkeys(_RENOMBRES.get(m, m) for m in meta["modos_usados"]))
    return meta


def _dir(root: Path) -> Path:
    return Path(root) / "10_Sesiones"


def _ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _path(root: Path, sid: str) -> Path:
    p = _dir(root) / f"{sid}.md"
    if p.is_file():
        return p
    # las archivadas viven en _archivo/YYYY-MM/: abrirlas desde la lista también funciona
    for q in _dir(root).glob(f"_archivo/*/{sid}.md"):
        return q
    raise FileNotFoundError(f"sesión no encontrada: {sid}")


def crear(root: Path, subjects: list[str] | None = None, titulo: str = "sesion", modo: str = "chat",
          estado: str = "activa", proyecto: str = "") -> str:
    ahora = datetime.now().astimezone()
    # titulo="" = sesión nueva del chat: se llama por su fecha y hora hasta que se
    # grabe (⌸) y el agente le ponga un nombre de verdad. El del primer mensaje
    # mentía apenas la charla giraba, y el sid ya lleva la fecha: no se repite.
    sid = f"{ahora:%Y-%m-%d_%H%M%S}" + (f"_{slugificar(titulo)}" if titulo else "")
    titulo = titulo or f"{ahora:%Y-%m-%d %H:%M}"
    p = _dir(root) / f"{sid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(
        "", id=sid, modo=modo, modos_usados=[modo], subjects=list(subjects or []),
        titulo=titulo, proyecto=proyecto,
        creada=_ahora(), actualizada=_ahora(), estado=estado,
        resumen="", turnos=0, paginas_usadas=[],
        tokens_entrada_total=0, tokens_entrada_ultimo_turno=0,
    )
    p.write_text(frontmatter.dumps(post), encoding="utf-8")
    return sid


def cargar(root: Path, sid: str) -> tuple[dict, list[dict]]:
    post = frontmatter.load(_path(root, sid))
    return _norm(dict(post.metadata)), _mensajes(post.content)


def _mensajes(cuerpo: str) -> list[dict]:
    partes = RX_MSG.split(cuerpo)
    return [{"ts": partes[i], "rol": partes[i + 1], "texto": partes[i + 2].strip()}
            for i in range(1, len(partes), 3)]


def agregar_mensaje(root: Path, sid: str, rol: str, texto: str) -> None:
    with _path(root, sid).open("a", encoding="utf-8") as f:
        f.write(f"\n## [{datetime.now():%Y-%m-%d %H:%M}] {rol}\n\n{texto.strip()}\n")


def actualizar_meta(root: Path, sid: str, **campos) -> None:
    """Sirve también para renombrar, cambiar modo de vista o subjects (spec §5.1)."""
    p = _path(root, sid)
    post = frontmatter.load(p)
    if campos.get("modo"):
        # historial de vistas por las que pasó la sesión (iconos en las listas)
        previos = post.metadata.get("modos_usados") or [m for m in [post.metadata.get("modo")] if m]
        campos["modos_usados"] = list(dict.fromkeys([*previos, campos["modo"]]))
    post.metadata.update(campos)
    post.metadata["actualizada"] = _ahora()
    p.write_text(frontmatter.dumps(post), encoding="utf-8")


def listar(root: Path, modo: str | None = None, archivadas: bool = False) -> list[dict]:
    base = _dir(root)
    if not base.exists():
        return []
    patron = "_archivo/*/*.md" if archivadas else "*.md"
    out = [_norm(dict(frontmatter.load(p).metadata)) for p in sorted(base.glob(patron), reverse=True)]
    # las temporales (chat del Main aún no grabado) no aparecen en Think.
    # ponytail: si se acumulan huérfanas, barrerlas por edad en el proceso nocturno.
    return [m for m in out if m.get("estado") != "temporal" and (not modo or m.get("modo") == modo)]


def de_proyecto(root: Path, proyecto: str) -> list[str]:
    """Ids de TODAS las sesiones del proyecto — activas, archivadas y temporales:
    renombrar o borrar un proyecto no debe dejar colgada ni a una temporal."""
    base = _dir(root)
    n = str(proyecto or "").strip().lower()
    if not base.exists() or not n:
        return []
    out = []
    for p in list(base.glob("*.md")) + list(base.glob("_archivo/*/*.md")):
        m = frontmatter.load(p).metadata
        if str(m.get("proyecto") or "").strip().lower() == n:
            out.append(str(m.get("id") or p.stem))
    return out


def eliminar(root: Path, sid: str) -> str:
    """'Borrar' = mover a papelera; nunca borrado destructivo (spec §8.3).
    La sesión sale de las listas pero el archivo sigue ahí si hizo falta."""
    p = _path(root, sid)
    destino = _dir(root) / "_papelera"
    destino.mkdir(parents=True, exist_ok=True)
    p.rename(destino / p.name)
    return f"10_Sesiones/_papelera/{p.name}"


def archivar(root: Path, sid: str) -> str:
    """Marca la sesión archivada y la mueve a _archivo/YYYY-MM/. Nada se borra."""
    actualizar_meta(root, sid, estado="archivada")
    p = _path(root, sid)
    destino = _dir(root) / "_archivo" / f"{datetime.now():%Y-%m}"
    destino.mkdir(parents=True, exist_ok=True)
    p.rename(destino / p.name)
    return f"10_Sesiones/_archivo/{datetime.now():%Y-%m}/{p.name}"
