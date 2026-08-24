---
name: mem
description: Memoria personal de Diego (app MeM). Usar cuando pida recordar o buscar algo suyo, guardar o anotar información, procesar su inbox, o crear imágenes/medios con sus cosas. Las herramientas vienen del servidor MCP "mem".
---

# Memoria MeM

Servidor MCP `mem`: la memoria personal de Diego en Markdown (notas, entradas,
capturas, creaciones). Es SU memoria: lo que está ahí pesa más que tu conocimiento
general.

## Leer
- `buscar` primero (índices); abre los hits con `leer_pagina`; `grep` como fallback regex.
- `conexiones(slug)` tras un hit: navega el vecindario de esa memoria (sesiones que la
  citaron, mismo subject, wikilinks, relacionadas por similitud) — trae contexto que la
  búsqueda por términos no encuentra.
- `buscar_memorias` para filtros: `desde`/`hasta` (YYYY-MM-DD), `tag`, `subject` (prefijo), `lugar`.
- `arbol_subjects` = el árbol de categorías real, con conteos.
- Páginas largas: `leer_pagina` avisa "(página larga: parte i/n…)" — seguí con `parte=i+1`.

## Escribir
- Nota rápida o material sin curar → `capturar` (va al inbox de MeM; NO pidas subjects ni formato).
- Síntesis valiosa y curada → `guardar_entrada`, con subjects existentes de `arbol_subjects`.
- `procesar_inbox` solo si Diego lo pide explícitamente (tarda, usa el LLM de MeM).

## Crear medios
- `crear_imagen` = local, gratis y privado: primera opción. `crear_nube` (Higgsfield)
  GASTA créditos del plan de Diego: nombra el modelo y pide su ok explícito antes.
  `modelos_nube` lista los modelos de nube.
- Si un servicio local no corre (ComfyUI, LM Studio): pide permiso a Diego y recién
  entonces llama `arrancar_servicio`; luego reintenta sin que él repita el pedido.
- Todo lo generado queda catalogado solo en la memoria bajo `Creaciones/`.
