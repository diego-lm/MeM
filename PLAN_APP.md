# Plan de Desarrollo — MeM (plataforma de memoria + chats)

**Fecha:** 2026-07-30
**Repo:** `W:\Dropbox\PROJECTS\_CODE\MeM`
**Base de conocimiento:** `W:\Dropbox\PROJECTS\_COWORK\hamuQ` (fuente de verdad, ya existe)
**Documento padre:** `hamuQ/02_Proyectos_Personales/App_Captura_Memoria/PLAN_DESARROLLO.md` (captura + procesador nocturno + protocolo de recuperación — este plan lo implementa y lo extiende con la capa de chats)

---

## 1. Objetivo y alcance

### 1.1 Visión completa (a dónde va esto)

Una plataforma personal donde toda la información capturada vive como memoria persistente en Markdown (hamuQ), y donde Diego interactúa con esa memoria a través de **modos de conversación** especializados: Q&A, research, timelines, mindmaps, brainstorm de ideas, etc. Cada modo es una manera distinta de procesar la información, con su propio historial de sesiones.

### 1.2 Alcance de esta etapa (v1 — lo único que se construye ahora)

1. **Motor de memoria** (`mem-core`): lectura/escritura/búsqueda sobre hamuQ implementando el protocolo de recuperación del plan padre, más el sistema de **limpieza/mantenimiento** (lint).
2. **Sistema de chats**: sesiones persistentes, cada una asociada a un modo, con historial propio en Markdown, que consultan la memoria con **uso mínimo de contexto** (ver §7, es el requisito central).
3. **Un solo modo incluido en v1**: `consulta` (Q&A general sobre la base). Los demás modos son archivos de configuración futuros, no código (ver §6).
4. **API + CLI** para operar todo lo anterior.

Explícitamente fuera de v1: UI pulida (una página HTML mínima de chat alcanza), app móvil de captura (la captura vía POST del plan padre se integra como endpoint, pero el cliente móvil es fase posterior), procesador nocturno automatizado (el motor expone el comando; la automatización es la Fase 2 del plan padre).

## 2. Principios de diseño

1. **Markdown es la fuente de verdad, siempre.** Memoria, historiales de chat, definiciones de modos: todo son archivos de texto en Dropbox. Cualquier base derivada (SQLite FTS5) es regenerable y descartable. Consecuencia: portabilidad total — cualquier agente (Claude Code, Cowork, LM Studio) puede leer/escribir el sistema sin pasar por el app.
2. **Los modos son datos, no código.** Un modo nuevo (research, timeline, mindmap…) = un archivo de definición nuevo, cero cambios en el motor. Así la expansión futura es escribir prompts, no programar features.
3. **Contexto proporcional a la pregunta, no al tamaño de la base.** Presupuesto de contexto explícito y medido (§7).
4. **Proveedor-agnóstico.** Una interfaz de LLM, dos implementaciones: Anthropic API y OpenAI-compatible (LM Studio, y de paso cualquier otro proveedor). Es la única abstracción "extra" del proyecto y está justificada por requisito explícito de portabilidad.
5. **El motor no necesita servidor.** `mem-core` es una librería + CLI que funciona sola sobre la carpeta local. El API es una capa fina encima. Esto permite que el procesador nocturno, los scripts y los agentes usen el motor directamente.
6. **Nada se borra.** Sesiones cerradas y capturas procesadas van a histórico, igual que el resto de hamuQ.

## 3. Arquitectura

```
                                  ┌──────────────────────────────┐
[Celular / clientes] ──POST──▶    │  API (FastAPI)               │
                                  │  /capture /sessions /chat    │
[UI web mínima] ──SSE────────▶    └──────────┬───────────────────┘
                                             │ usa
                                  ┌──────────▼───────────────────┐
[CLI: mem ...] ──────────────▶    │  mem-core (librería Python)  │
[Procesador nocturno] ───────▶    │  ├─ memoria: leer/escribir/  │
[Agentes externos]────────────    │  │   buscar/lint sobre hamuQ │
 (leen los .md directo)           │  ├─ sesiones: historiales    │
                                  │  ├─ modos: registro de modos │
                                  │  └─ llm: adapter proveedor   │
                                  └──────────┬───────────────────┘
                                             │ lee/escribe .md
                                  ┌──────────▼───────────────────┐
                                  │  hamuQ/ (Dropbox, Markdown)  │
                                  │  fuente de verdad            │
                                  └──────────────────────────────┘
```

## 4. Modelo de datos (extensión de hamuQ)

Se extiende la estructura del plan padre (07_Inbox, 08_Categorias, 09_Sistema) con:

```
hamuQ/
├── 09_Sistema/
│   ├── NUCLEO.md                ← memoria core ≤2.000 chars (plan padre)
│   ├── PIPELINE_NOCTURNO.md     ← definición del pipeline (plan padre)
│   ├── Modos/                   ← NUEVO: un archivo por modo de conversación
│   │   └── consulta.md          ← único modo de v1
│   ├── log.md
│   └── search.db                ← derivado FTS5 (fase de escalado)
└── 10_Chats/                    ← NUEVO: historiales de sesiones
    ├── consulta/                ← una carpeta por modo
    │   └── 2026-07-30_1015_pipeline-vr.md
    └── _archivo/YYYY-MM/        ← sesiones cerradas (nada se borra)
```

### 4.1 Formato de sesión (un archivo por sesión)

```markdown
---
id: 2026-07-30_1015_pipeline-vr
modo: consulta
creada: 2026-07-30T10:15:00+02:00
actualizada: 2026-07-30T10:42:00+02:00
estado: activa                    # activa | archivada
resumen: >                        # resumen rodante, lo mantiene el motor (§7)
  Diego preguntó por opciones de captura volumétrica para el museo VR;
  se revisaron las entradas X e Y; conclusión preliminar: gaussian splatting.
paginas_usadas: [06_Biblioteca_Conocimiento/Entradas/gaussian-splatting.md]
---

## [2026-07-30 10:15] Diego
¿Qué teníamos sobre captura volumétrica?

## [2026-07-30 10:16] Asistente
(respuesta…)
```

Legible por humanos y por cualquier agente, greppable, y el frontmatter `resumen` es lo único que se recarga al reabrir la sesión.

### 4.2 Formato de modo (`09_Sistema/Modos/<modo>.md`)

```markdown
---
nombre: consulta
descripcion: Q&A general sobre la base de conocimiento
retrieval: agentic          # agentic (el modelo usa tools) | scripted (el motor busca primero)
herramientas: [buscar, leer_pagina, grep, guardar_entrada]
max_paginas: 5              # tope de páginas de memoria por turno
destilar: preguntar         # al cerrar sesión: preguntar | siempre | nunca (ver §5.3)
---

# Instrucciones del modo
Eres el asistente de consulta de la base hamuQ…
(system prompt completo del modo)
```

El motor carga esto como configuración. Agregar el modo `timeline` mañana = crear `timeline.md` con su prompt y sus reglas de salida (ej. mermaid). Cero código nuevo, salvo que el modo necesite un renderer específico en la UI (eso sí es código, y es de la fase de expansión).

## 5. Motor de memoria (`mem-core`)

### 5.1 Recuperación (implementa el Componente C del plan padre)

Expuesta como funciones/tools que el chat usa bajo demanda:

- `buscar(consulta)` → busca en `00_CATEGORIAS.md` + `00_INDICE_TEMATICO.md` + `00_INDICE_GENERAL.md`; devuelve candidatos (título, path, una línea). Solo índices: decenas de líneas de costo.
- `leer_pagina(path)` → devuelve una página. El motor cuenta páginas leídas por turno y corta en `max_paginas`.
- `grep(patron)` → fallback por palabra clave sobre la carpeta (ripgrep si está, `re` sobre archivos si no).
- `guardar_entrada(titulo, contenido, categorias)` → crea/actualiza entrada en la Biblioteca siguiendo la plantilla y actualiza índices (misma lógica que usará el procesador nocturno — se escribe una sola vez, acá).

Cuando exista `search.db` (FTS5), `buscar()` lo usa primero y cae a índices si no está — la interfaz no cambia.

### 5.2 Escritura e integración

La lógica de "integrar una pieza de información a hamuQ" (clasificar → deduplicar contra índice → crear/actualizar entrada → actualizar índices → backlinks) vive en `mem-core` como una función única, usada por: `guardar_entrada` del chat, el procesador nocturno, y el comando de destilado. Un solo lugar, tres consumidores.

### 5.3 Limpieza de la memoria

Dos mecanismos, ambos en el motor:

1. **`mem lint`** (el mantenimiento semanal del plan padre): índices desincronizados con archivos reales, entradas huérfanas sin links entrantes, duplicados aparentes por título/tema, contradicciones entre páginas (vía LLM, solo sobre pares candidatos), sesiones activas viejas sin actividad → propone archivarlas. Corrige lo mecánico automáticamente, reporta lo demás en `09_Sistema/log.md`.
2. **Destilado de sesiones**: al cerrar una sesión (o vía `mem destilar`), el motor pregunta al LLM si la conversación produjo síntesis nueva valiosa; si sí, la integra a la Biblioteca (§5.2) y anota en la sesión qué entradas generó. Así el conocimiento de los chats se acumula en la base en vez de morir en el historial — el principio del LLM Wiki de que el conocimiento se sintetiza, no solo se recupera.

### 5.4 Adapter de LLM

Interfaz mínima: `chat(mensajes, tools, stream) → respuesta/eventos`. Implementaciones: `anthropic` (SDK oficial, modelos Claude) y `openai_compat` (SDK openai apuntando a cualquier base_url: LM Studio `http://localhost:1234/v1`, u otros). Configuración en un `config.toml` local (proveedor, modelo, base_url, API key vía variable de entorno). El modo `retrieval: scripted` existe para modelos locales débiles en tool-calling: el motor ejecuta `buscar` + `leer_pagina` él mismo con los términos de la pregunta y pasa los resultados como contexto, usando el LLM solo para redactar.

## 6. Sistema de chats y modos

Flujo de un turno (modo agentic):

1. Cliente manda mensaje a la sesión.
2. Motor arma el contexto: `NUCLEO.md` + prompt del modo + `resumen` de la sesión + últimos N turnos (N≈6) + mensaje nuevo. **Nunca** el historial completo ni páginas precargadas.
3. Loop de tool-use: el modelo llama `buscar`/`leer_pagina`/`grep` según necesite, con tope `max_paginas`.
4. Respuesta en streaming (SSE) citando qué páginas usó; se persisten mensaje y respuesta en el archivo de sesión; `paginas_usadas` se actualiza.
5. Cada K turnos (K≈4) o al cerrar, el motor actualiza el `resumen` rodante con una llamada corta al LLM.

Los historiales por modo quedan naturalmente separados porque cada sesión pertenece a un modo y vive en su carpeta. "Continuar una conversación" = reabrir el archivo: costo de reapertura = frontmatter + últimos turnos, no toda la sesión.

## 7. Presupuesto de contexto (requisito central)

Costo fijo por turno (siempre en contexto):

| Pieza | Tamaño objetivo |
|---|---|
| NUCLEO.md | ≤2.000 chars |
| Prompt del modo | ≤2.000 chars |
| Resumen rodante de la sesión | ≤1.500 chars |
| Últimos N turnos | acotado por N, no por la sesión |

Costo variable: solo lo que el modelo pide vía tools (índices ≈ decenas de líneas; páginas ≤ `max_paginas`). El motor **mide** tokens de entrada por turno y los registra en el log de la sesión — así el requisito es verificable, no aspiracional. Criterio: un turno típico de consulta debe entrar en pocos miles de tokens de contexto aunque la base tenga miles de páginas.

## 8. API (FastAPI, capa fina sobre mem-core)

- `POST /capture` — idéntico al Componente A del plan padre (escribe en `07_Inbox/`). Mismo servicio, un deploy menos.
- `GET /sessions?modo=` / `POST /sessions {modo}` / `POST /sessions/{id}/archive`
- `POST /sessions/{id}/messages` — SSE con la respuesta en streaming.
- `GET /modes` — lista los modos disponibles (lee `09_Sistema/Modos/`).
- `GET /health`
- Auth: token estático Bearer (un usuario, igual que el plan padre).

CLI equivalente para todo (`mem chat --modo consulta`, `mem sessions`, `mem lint`, `mem destilar`, `mem index`, `mem process`), porque el motor no depende del servidor (§2.5).

## 9. Stack

- **Python 3.12+**, gestor `uv`. Dependencias: `fastapi`, `uvicorn`, `pydantic`, `anthropic`, `openai`, `python-frontmatter`, `httpx`. SQLite FTS5 vía `sqlite3` de stdlib (fase de escalado, sin dependencia nueva).
- Un solo paquete (`mem/`) con submódulos `memoria`, `sesiones`, `modos`, `llm`, `api`, `cli` — no microservicios, no monorepo multi-lenguaje.
- Tests: `pytest`, solo sobre la lógica no trivial (retrieval, integración/dedupe, presupuesto de contexto, lint). Se corre sobre una carpeta hamuQ de fixture, nunca sobre la real.
- Corre en la PC de Diego (Windows) sobre la carpeta Dropbox local. Para acceso remoto/móvil, el mismo servidor se despliega después en Fly.io/Railway usando la API de Dropbox como backend de archivos — el acceso a archivos se hace a través de una interfaz local-o-Dropbox (segunda implementación cuando llegue esa fase, no antes).

## 10. Fases

**Fase 0 — Fundación hamuQ** (= Fase 1 del plan padre, sin código): crear `07_Inbox/`, `08_Categorias/00_CATEGORIAS.md` semilla, `09_Sistema/` (NUCLEO.md, PIPELINE_NOCTURNO.md, log.md), `09_Sistema/Modos/consulta.md`, `10_Chats/`. Validar el protocolo a mano con 2–3 capturas de prueba.

**Fase 1 — Motor de memoria**: `mem-core` (memoria: buscar/leer/grep/integrar; adapter LLM ambos proveedores) + CLI `mem search` / `mem ask` (pregunta única, sin sesión). Criterio: una pregunta sobre la base responde citando páginas, con el presupuesto de contexto medido y dentro de objetivo, contra Claude API y contra LM Studio.

**Fase 2 — Chats con historial**: sesiones (formato §4.1), modos (formato §4.2), resumen rodante, destilado al cerrar. CLI `mem chat`. Criterio: cerrar una sesión, reabrirla al día siguiente y continuar con costo de reapertura ≈ frontmatter + últimos turnos; una síntesis valiosa quedó integrada a la Biblioteca.

**Fase 3 — API + UI mínima**: FastAPI + auth + SSE + página HTML de chat con selector de modo y lista de sesiones. Incluye `POST /capture`. Criterio: chatear desde el navegador del celular (red local) con la misma sesión que se abrió por CLI.

**Fase 4 — Limpieza y procesador**: `mem lint` completo + `mem process` (pipeline nocturno leyendo `PIPELINE_NOCTURNO.md`, ejecutable por Programador de tareas / Cowork / headless). Criterio: los criterios de aceptación de la Fase 2 del plan padre.

**Fase 5 — Escalado de búsqueda**: `mem index` (FTS5 regenerable), `buscar()` híbrido con fallback. Solo cuando los índices Markdown se queden cortos — no antes.

**Fase 6+ — Expansión (fuera de alcance actual)**: modos nuevos (research con búsqueda web, timeline/mindmap con salida mermaid, brainstorm), renderers en la UI para esos formatos, app/PWA de captura móvil, deploy remoto con backend Dropbox API.

## 11. Criterios de aceptación globales

- Los del plan padre (§8) siguen vigentes: nada se pierde, nada se borra, Markdown fuente de verdad, derivados regenerables.
- Contexto por turno: fijo acotado (§7) + variable proporcional a la pregunta; medido y registrado, nunca la base completa ni el historial completo.
- Cambiar de proveedor LLM (Claude ↔ LM Studio) es editar `config.toml`, sin tocar código ni datos.
- Agregar un modo de conversación nuevo no requiere tocar el motor (solo su archivo de definición; renderer de UI aparte si el formato lo pide).
- Todo lo que el app escribe (sesiones, entradas, índices) es legible y editable por un humano o por otro agente sin el app.

## 12. Riesgos y decisiones diferidas

- **Tool-calling en modelos locales**: mitigado por `retrieval: scripted` (§5.4) desde el diseño.
- **Concurrencia** (chat activo + procesador nocturno): el lock `proceso.lock` del plan padre cubre el procesador; los chats solo tocan su archivo de sesión y las escrituras a índices pasan por la función única de integración (§5.2), que es secuencial. Suficiente para un usuario; si algún día hay corrupción de índices, se agrega lock por archivo — no antes.
- **Dropbox sync conflicts** (PC + futuro deploy remoto escribiendo a la vez): irrelevante en v1 (todo local). Se resuelve en la fase de deploy remoto eligiendo un solo escritor por recurso.
- **UI**: deliberadamente mínima hasta que los modos visuales (timeline, mindmap) la justifiquen.

## 13. Delta 2026-08-07 — búsqueda híbrida + vistas 2D + canvas (implementado)

La Fase 5 ("escalado de búsqueda") se adelantó y se amplió según el reporte de métodos modernos de memoria LLM. Decisiones tomadas con Diego:

- **Índice derivado `09_Sistema/_derivados/mem.db`** (`mem/indice.py`): FTS5/BM25 + embeddings + fusión RRF (k=60). 100% regenerable; journal TRUNCATE (nunca WAL en Dropbox); corrupción ⇒ borrar y regenerar solo. Hooks en `guardar/editar/eliminar_entrada` + diff de mtimes en cada búsqueda (absorbe Obsidian/Dropbox). `buscar()` conserva formato y fallback naive.
- **Embeddings ONNX locales en proceso** (`mem/embed.py`): `Xenova/multilingual-e5-small` int8 (~120 MB, `config → embed_modelo`), descarga solo en reindex explícito; sin modelo degrada a BM25. Tests con embedder fake determinista (`MEM_EMBED_FAKE=1`), cero red.
- **Wikilinks `[[slug]]`** + backlinks + sugerencias semánticas en `conexiones()`; ningún automatismo escribe enlaces — solo confirmación explícita (línea fechada). Duplicados por coseno en `mem lint`.
- **Vistas 2D nuevas en Memory**: Grafo (d3-force vendorizado), Mapa semántico (PCA propia, x/y cacheadas), Tiempo con línea temporal bitemporal (cuando vs capturado), Lugares con mapa (Leaflet vendorizado + tiles OSM; geocoding Nominatim: 1 req/s, cache permanente, ≤5 por llamada).
- **Canvas de brainstorming** (`mem/canvas.py` + `#canvas`): tableros JSON en `09_Sistema/Canvas/`, tarjetas arrastrables, "✦ Sugerir" = vecinas semánticas + lejanas al azar → ideas del LLM con fuentes.
- **Los modos de sesión `mindmap` y `timeline` usan los mismos componentes** (no quedaron dos mapas distintos de la misma idea): el mindmap es `vis/grafo.js` acotado al vecindario de las memorias que citó la sesión (`GET /memory/graph?slugs=…`, subgrafo = foco + vecinas semánticas + wikilinks) más dos tipos de nodo inyectados desde el cliente —el centro de la sesión y las ideas conversadas, que **no se persisten en `mem.db`** porque no son memorias—; el timeline es `vis/timeline.js` con los mismos datos por subject de antes. Clic en idea sigue abriendo el cuadro de "expandir".

**Diferido explícitamente**: re-ranker (punto de enchufe anotado en `buscar_hibrida`), `sqlite-vec` (>10k entradas), UMAP (la PCA alcanza), tldraw (canvas propio mínimo), modo 3D/VR.
