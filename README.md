# MeM — memoria persistente + sesiones de trabajo sobre hamuQ

Plataforma personal según `ESPECIFICACION_FUNCIONAL_APP.md` (hamuQ): la base de conocimiento vive en Markdown con frontmatter YAML (`hamuQ/` en Dropbox, fuente de verdad, legible por cualquier agente — Hermes, Claude, LM Studio). Se captura al **Inbox** (`07_Inbox/`, editable, con papelera), se explora la **Memoria** por subjects/tags/fechas, y se trabaja en **Sesiones** (`10_Sesiones/`) vinculadas a uno o más subjects, con modo de vista intercambiable (`09_Sistema/Modos/`: chat, qa, brainstorm, mindmap, timeline). Contexto por turno: NUCLEO + prompt del modo + subjects + resumen rodante + últimos turnos + solo las páginas que el modelo pide (tope `max_paginas`). Plan completo: `PLAN_APP.md`.

Frontend real (PWA) sobre el diseño **Organic** de Claude Design, en `mem/static/` — Preact + HTM vendorizados, sin build step. Ver `DESIGN_BRIEF.md`.

## Instalación

```powershell
irm https://raw.githubusercontent.com/diego-lm/MeM/main/install.ps1 | iex
```

Clona, arma el entorno virtual e instala dependencias. Guía completa (requisitos, actualizar un checkout existente, primer arranque, comandos) en [MANUAL.md](MANUAL.md).

Proveedores de IA en `config.toml` (cambiar = editar ese archivo o Ajustes → Proveedor de IA en la app):

- `anthropic` — API de Claude (`ANTHROPIC_API_KEY` en el entorno)
- `claude_code` — Claude Code instalado localmente (comando `claude`, usa la suscripción, sin key; sin tool-calling → retrieval scripted automático)
- `openai` — LM Studio u otro endpoint OpenAI-compatible (local o remoto vía `base_url`)

Los clientes de chat y de visión (`describir_imagen`) llevan un timeout de 240s (`llm.TIMEOUT_API`): sin él, un modelo local que todavía está cargando (LM Studio JIT) o un `claude_code` sin sesión iniciada colgaban el turno sin señal ninguna. El registro plegable de cada turno muestra un paso por adjunto leído y una línea de cierre `⏱ turno · agente · duración · tokens`, así que un turno lento se ve mientras pasa, no solo al final.

## Uso

```
mem serve                                            # API + UI web (PWA) en http://localhost:8765
mem ask "¿qué tenemos sobre captura volumétrica?"    # pregunta única
mem chat --subjects "IA,Qualia"                      # REPL con sesión persistente sobre subjects
mem sessions / mem modes / mem search "términos"
mem capture "una idea" --tags x --subjects Y         # nota cruda al 07_Inbox (lo fijado a mano manda)
mem inbox                                            # pendientes + procesadas recientes
mem process                                          # procesa el inbox: entiende, categoriza, etiqueta, indexa
mem archive <id-sesion>                              # destila síntesis a la Biblioteca y archiva
mem lint                                             # limpieza: links rotos, huérfanas, duplicados, sesiones viejas
mem index                                            # (re)construye el índice de búsqueda híbrida (mem.db)
mem ids                                              # one-shot: id + versión a las entradas anteriores al versionado
```

API: `/capture`, `/capture/triage` (el agente revisa la captura antes de encolarla), `/attach` (POST sube adjunto, GET lo sirve), `/inbox` (GET/PATCH/DELETE→papelera), `/modes`, `/sessions` (GET con `?archivadas=1`, POST, PATCH — incluye renombrar título) | `/sessions/{id}` (GET trae memorias y adjuntos vivos que generó, DELETE→papelera; `?contenido=1` también manda a la papelera esas memorias y los adjuntos que ninguna otra página viva referencia), `/sessions/{id}/messages` (SSE), `/sessions/{id}/distill` | `/archive` (devuelven `{texto, entradas:[{path,estado}]}`), `/memory/tree`, `/memory/search` (con `orden=relevancia` para ranking híbrido), `/memory/entry` (POST) | `/memory/entry/{slug}` (GET con conexiones, PATCH) | `/memory/entry/{slug}/link` (confirma un wikilink sugerido) | `/memory/entry/{slug}/versions[/{n}]`, `/memory/merge` (POST `{absorbe, absorbida}`: fusiona dos entradas — ver Lint más abajo), `/memory/reindex` | `/memory/graph` | `/memory/semantic` | `/memory/suggest` (memorias muy cercanas a un borrador) | `/memory/map` (índice derivado y vistas), `/canvas` (GET/POST) | `/canvas/{id}` (GET/PATCH/DELETE→papelera) | `/canvas/{id}/sugerir`, `/config` (GET sanitizado, PATCH proveedor/modelo/base_url/`estilos_guardados`), `/log` (con `?horas=`), `/process`, `/provider/test`, `/lint`, `/lint/ignore` (POST `{tipo, clave}`: descarta un aviso puntual, se guarda en `lint_ignorar.json` en el propio vault).

## Búsqueda híbrida (mem.db)

La búsqueda combina dos ramas y las fusiona por RRF: **BM25** (SQLite FTS5, sin tildes) sobre título, cuerpo completo (incluidas transcripciones de adjuntos), tags y subjects, y **similitud semántica** (coseno) sobre un embedding de la síntesis de cada entrada — encuentra "auto" buscando "coche". El índice vive en `hamuQ/09_Sistema/_derivados/mem.db` y es **100% regenerable**: los `.md` siguen siendo la única fuente de verdad, y si el archivo falta o se corrompe se reconstruye solo. Cada escritura del vault lo actualiza vía hooks; las ediciones externas (Obsidian, Dropbox) las absorbe un diff de mtimes al buscar.

- **Modelo de embeddings**: `config → embed_modelo` (default `Xenova/multilingual-e5-small`, ONNX int8, ~120 MB). Se descarga **una sola vez** y solo en un reindex explícito (`mem index` o Ajustes → Procesamiento → "⌕ Reindexar búsqueda") — nunca al capturar ni al arrancar. Sin modelo, la búsqueda degrada limpia a BM25; sin FTS5, al camino léxico naive de siempre. Cambiar el modelo invalida los vectores y pide reindexar.
- **Recordar mientras se escribe** (`/memory/suggest`, franja sobre el compositor de una sesión): memorias MUY cercanas al borrador, tocables para insertar `[[slug|Título]]` en el cursor. Precisión sobre exhaustividad — exige las **dos** señales: match léxico con peso BM25 real (una palabra que discrimine, no las vacías del OR de FTS) y **z ≥ 2 desvíos** de similitud sobre la base. Umbral en z y no en coseno porque e5-small comprime el rango: medido sobre la base real, la basura saca 0,813 y un acierto bueno 0,807 — el valor absoluto no dice nada, la distancia a la media sí. La z se prende recién a partir de 20 memorias: su máximo alcanzable es (N−1)/√N, así que en una base recién nacida el umbral dejaría la función muerta.
- **Lugar de captura**: si el navegador da permiso, cada captura guarda `coords_captura` ("lat,lon") y el procesador le pone nombre (`lugar_captura`) con Nominatim inverso, una sola vez, escrito al `.md`. Es el par espacial de `capturado`, igual que `cuando` vs `capturado` en el tiempo. El permiso del navegador es el único interruptor (Ajustes → Procesamiento muestra el estado y permite concederlo de antemano); requiere https o localhost, y nunca demora ni bloquea la captura.
- **Mapa geográfico**: los `lugar` del frontmatter se geocodifican con **Nominatim (OpenStreetMap)** respetando su política de uso: máx. 1 request/segundo, User-Agent identificado, a lo sumo 5 lugares nuevos por consulta de la app, cache permanente (lo irresoluble no se reintenta). Los tiles del mapa son de OSM y llevan su atribución obligatoria.
- **Dropbox**: `mem.db` usa journal TRUNCATE (nunca WAL) para convivir con la sincronización; aun así conviene **ignorar `09_Sistema/_derivados/`** en Dropbox — es un derivado local que no vale nada en otra máquina.
- Sin re-ranker por ahora: el punto de enchufe está anotado en `indice.buscar_hibrida`.
- **Lint accionable** (`mem lint` / Ajustes → Procesamiento / `GET /lint`): "posible duplicado" muestra la similitud y la `fecha` de ambas entradas ("0.95, 2026-08-06 ≈ 2026-08-07"); un par donde **ambas** entradas tienen `origen: crear` se salta (variantes de un mismo prompt de generación — duplicado esperado, no aviso). Cada aviso trae acciones: "fusionar" (`POST /memory/merge`: une tags/subjects/enlaces, ancla el cuerpo de la absorbida como actualización fechada, redirige sus wikilinks y la manda a la papelera versionada) o "descartar" (`POST /lint/ignore`: la clave del par o la ruta del medio queda en `09_Sistema/lint_ignorar.json`, en el vault — es criterio del usuario, no un derivado).

## Vistas de exploración (Memory)

Además de Recientes/Temas/Proyectos: **Tiempo** (lista por bloques o línea temporal bitemporal con zoom — eje `cuando ?? fecha`, capa opcional del instante de captura), **Lugares** (chips o mapa Leaflet/OSM: círculos ● por `lugar` — de qué habla la memoria — y rombos ◇ por `coords_captura` — desde dónde se anotó, capa que se prende y se apaga), **Grafo** (la base entera como red d3-force: las memorias se conectan A TRAVÉS de nodos-eje que dicen por qué —subject ●, tag ○, lugar ◇, un eje por valor compartido por dos o más—, más `[[wikilink]]` ⇄ y similitud semántica ┈; cada capa se prende y apaga, y dos deslizadores acotan el dibujo: **parentesco** (cuántos saltos desde el centro) y **cercanía** (el coseno mínimo de las aristas semánticas). Tap en memoria abre la ficha, tap en un eje resalta su vecindario; el zoom con rueda o dos dedos crece desde el punto que se toca), **Mapa semántico** (proyección 2D de los embeddings: lo parecido queda cerca) y **◫ Canvas** (tableros de brainstorming: tarjetas arrastrables de memorias y notas; "✦ Sugerir" mezcla el tablero con vecinas semánticas y memorias lejanas al azar y le pide ideas al LLM, cada idea citando sus fuentes). Todo 2D, todo vendorizado (d3-force, Leaflet), sin CDN.

Dentro de una sesión, los modos de vista **mindmap** y **timeline** son esos mismos componentes: el mindmap es el grafo con física acotado al vecindario de las memorias que la sesión citó (`GET /memory/graph?slugs=…`), con el centro de la sesión y las ideas conversadas como nodos extra que no se guardan en el índice; el timeline es la misma línea temporal con eje real. Cambiar de modo sigue siendo un PATCH a `modo` — nada de datos cambia.

## Versiones de una memoria

Cada entrada lleva `id` (autogenerado, estable aunque cambie el título) y `version` en el frontmatter. Antes de cada cambio de **contenido** — título, resumen, nota, actualización fechada, reproceso — la entrada se copia entera a `06_Biblioteca_Conocimiento/Entradas/_versiones/<id>/vNNN.md` y la viva sube de versión. El archivo `Entradas/<slug>.md` es siempre la última: búsquedas, índices, conexiones y links `#entry/<slug>` no cambian en nada. Poner o quitar un tag no versiona (en la app es un toque de chip). Las versiones se leen desde la pantalla de la memoria, bajo "Versiones anteriores"; nada se restaura ni se pisa.

Tests: `.venv\Scripts\python -m pytest` (corren sobre una base de fixture, nunca la real; incluyen el procesador de inbox con `fijado` simulado).

## Lo que puede usar el chat

El frontmatter `herramientas` de cada modo decide qué ve el agente:

- `buscar`, `leer_pagina`, `grep`, `guardar_entrada` — la base hamuQ.
- `web` — `buscar_web` (DuckDuckGo, sin API key) y `leer_web` (descarga una URL y devuelve texto plano).
- `skill` — abre instrucciones en Markdown de `hamuQ/09_Sistema/Skills/*.md`. El índice (nombre + `descripcion` del frontmatter) va en el system prompt; el cuerpo se carga solo si el agente lo pide. Agregar una skill = escribir un `.md`.
- `mcp` — herramientas de servidores MCP externos, declarados en `mcp.json` en la raíz del repo con el mismo formato que Claude Code (sin ese archivo no hay servidores y no cuesta nada):

```json
{"mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "W:/ruta"]}}}
```

Solo transporte stdio y solo `tools` (ni resources ni prompts). Un servidor que no arranca se salta sin romper el chat.

Los **subjects de una sesión se descubren solos**: al responder, se suman los que declaran las páginas leídas; si el turno no leyó nada de la base (web o conocimiento del modelo), los del árbol que la pregunta nombre.

## Agregar un modo de vista nuevo

Crear `hamuQ/09_Sistema/Modos/<nombre>.md` con frontmatter (`glyph`, `color`, `retrieval`, `herramientas`, `max_paginas`) + system prompt. Sin tocar el backend. El frontend mapea `chat`/`qa`/`brainstorm`/`mindmap`/`timeline` a renderers dedicados (`mem/static/js/screens/chat.js`); un modo con nombre distinto cae al renderer de chat simple. Una misma sesión puede cambiar de vista cuando quiera (spec §5.2).

## Conectar MeM a Claude (Desktop, Code, web y móvil)

`mem/mcp.py` es un servidor MCP con las tools de la memoria (`buscar`, `leer_pagina` — paginada para páginas largas con `parte` —, `conexiones` — el vecindario de una entrada: sesiones, mismo subject, wikilinks, relacionadas —, `grep`, `buscar_memorias`, `arbol_subjects`, `guardar_entrada`, `editar_memoria`, `capturar`, `procesar_inbox`) más las de creación de medios. Dos transportes, el mismo `despachar`:

- **stdio** — `python -m mem.mcp`, para Claude Desktop y Claude Code (ver `mem-plugin/.mcp.json`).
- **HTTP** — `POST /mcp/{secreto}` del API, para claude.ai en la web y en el móvil.

### Requisitos

1. `mem serve` corriendo (puerto 8765) — el tray de `scripts/mem_tray.ps1` lo levanta al iniciar sesión.
2. `mcp_secreto` con valor en `config.toml`: es la única llave de esa ruta.
3. Funnel de Tailscale publicando `/mcp` (`tailscale funnel status` debe mostrar `https://tu-maquina.tu-tailnet.ts.net:8443` → `/mcp proxy http://127.0.0.1:8765/mcp`).

### Web (claude.ai)

Settings → Connectors → **Add custom connector** → URL `https://tu-maquina.tu-tailnet.ts.net:8443/mcp/<mcp_secreto>` → sin OAuth (los campos avanzados van vacíos) → Add. En un chat, activar "mem" desde el menú de herramientas. Requiere plan Pro/Max/Team.

### Móvil (iOS / Android)

Los conectores no se dan de alta desde la app: se configuran una sola vez en la web con la misma cuenta y aparecen solos en el móvil. Ahí se activan por chat desde el menú `+`. Nada que instalar en el teléfono más allá de la app de Claude.

### Los links "Abrir en el browser"

Cuando el cliente de Claude no puede mostrar algo (una foto, un video, un markdown largo), las tools devuelven un link a la PWA — `app_url` + `/#entry/<slug>` — que abre esa memoria en el navegador. `app_url` apunta al **host tailnet** (`https://tu-maquina.tu-tailnet.ts.net:8765`), no al Funnel: el Funnel solo publica `/mcp`, y publicar la app entera con `token = ""` dejaría la memoria abierta a internet. Con Tailscale activo en el móvil el link abre; sin Tailscale, no. Para que abriera desde cualquier red habría que publicar `/` en el Funnel **y** poner un `token`, pero entonces el link pediría auth que el navegador no manda.

### Seguridad

El secreto del path es toda la llave y viaja solo por el HTTPS del Funnel. Si se filtra: cambiar `mcp_secreto`, reiniciar `mem serve` y actualizar la URL en el conector. Nunca hacer funnel del 443 (queda la PWA completa expuesta).

## Frontend (mem/static/)

Sin build step: ES modules servidos directo por FastAPI (`StaticFiles`, montado al final de `mem/api.py`). Preact+HTM vendorizados en `vendor/preact-htm.js`. Router por `location.hash`; store global mínimo en `js/state.js` (prefs + ruta); cada pantalla en `js/screens/*.js` maneja su propio estado local. PWA: `manifest.webmanifest` + `sw.js` (network-first del shell con caída al cache sin red, red directa para la API, cola offline de capturas en `js/api.js`). Layout responsive: móvil = diseño del prototipo (tab bar flotante); ≥880px = sidebar + columna (extrapolación propia sobre los mismos tokens del DS, sin bisel de celular).

### Estilo del sistema (Ajustes → UX/UI)

Todo el theming vive en tokens CSS (`ds.css` los declara, `app.css` los redefine por variante) que cascadean desde `<html>`: `state.js → aplicarAlDOM()` escribe `data-theme`, `data-pal`, `data-radio`, `data-borde`, `data-burbuja` y `data-fuente` en `document.documentElement` a partir de las prefs (`localStorage`, por dispositivo); el resto es CSS puro, sin cómputo en JS.

- **Paleta** — 12 (`terracota`, `sage`, `sand`, `amber`, `phosphor`, `clay`, `tinta`, `indigo`, `menta`, `rosa`, `cobalto`, `grafito`), cada una con bloque claro y oscuro en `app.css`; lista y nombres en `settings.js → PALETTES` + `i18n.js → paletteNames`. Contraste de las variantes `-700`/`-2700` (texto sobre fondo) verificado ≥ 4.5:1 en las 24 combinaciones paleta×tema.
- **Radio** (`recto`/`suave`/`redondo`), **borde** (`sutil`/`normal`/`marcado`) y **burbuja** (`llena`/`contorno`/`tinte`/`mínima`) — redefinen `--radius-sm/md/lg`, `--color-divider` y las clases `.mem-burb-yo`/`.mem-burb-mem` (compartidas por Home y Chat) respectivamente. `borde` usa `:root[data-borde=…]` a propósito, no `[data-borde=…]` a secas: amber y phosphor fijan su propio `--color-divider` oscuro con la misma especificidad que un selector simple, y solo un selector igual de específico —ubicado después en el archivo— gana el empate y puede pisarlo.
- **Tipografía** (`archivo`/`inter`/`serif`/`sistema`) — Archivo, Inter y Fraunces (`serif`) son variables, self-hosted en `static/fonts/*.woff2` (sin CDN en runtime, precacheadas en `sw.js`) y declaradas por `@font-face` en `ds.css`; `sistema` usa `system-ui`, cero descarga.
- **Vista previa** en vivo (par de burbujas + chip + botón con el estado actual) y **3 estilos guardados** (`config.toml → estilos_guardados`, hasta 3 `{nombre, palette, themePref, burbuja, radio, borde, fuente}`; PATCH `/config` los reescribe preservando comentarios) para llevar una combinación entre dispositivos — la selección activa sigue siendo local, los slots viajan por el server.
