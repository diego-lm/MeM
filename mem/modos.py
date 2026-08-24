"""Modos de conversación: archivos Markdown en 09_Sistema/Modos/. Datos, no código."""
from pathlib import Path

import frontmatter


def _dir(root: Path) -> Path:
    return Path(root) / "09_Sistema" / "Modos"


def listar(root: Path) -> list[dict]:
    return [cargar(root, p.stem) for p in sorted(_dir(root).glob("*.md"))]


def cargar(root: Path, nombre: str) -> dict:
    fm = frontmatter.load(_dir(root) / f"{nombre}.md")
    m = dict(fm.metadata)
    m.setdefault("nombre", nombre)
    m.setdefault("descripcion", "")
    m.setdefault("glyph", "▮")
    m.setdefault("color", "#b2622d")
    m.setdefault("retrieval", "agentic")
    m.setdefault("herramientas", ["buscar", "leer_pagina", "conexiones", "grep"])
    m.setdefault("max_paginas", 5)
    m["prompt"] = fm.content.strip()
    return m
