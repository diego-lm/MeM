# Formato de memoria de MeM — cómo se graban, catalogan, indexan y etiquetan las memorias

**Para qué es este documento:** especificación del formato en disco de la base hamuQ, tal como lo implementa `mem/memoria.py` y `mem/procesar.py`. Cualquier sistema nuevo que lea o escriba siguiendo estas reglas es compatible con MeM sin pasar por su código.

**Principio rector:** todo es Markdown con frontmatter YAML dentro de una carpeta Dropbox (`W:\Dropbox\MeM`). Los archivos son la fuente de verdad; los índices son derivados y regenerables. Nada se borra destructivamente: papelera y versiones, siempre.

---

## 1. Estructura de carpetas

```
hamuQ/  (W:\Dropbox\MeM)
├── 00_INDICE_GENERAL.md                  ← índice general (se busca en él)
├── 06_Biblioteca_Conocimiento/
│   ├── 00_INDICE_TEMATICO.md             ← índice por tema + línea cronológica
│   ├── Entradas/                         ← LAS MEMORIAS: un .md por memoria
│   │   └── _versiones/<id>/vNNN.md       ← versiones anteriores de cada memoria
│   └── _papelera/                        ← memorias "borradas"
├── 07_Inbox/                             ← capturas crudas sin procesar
│   ├── _adjuntos/                        ← fotos, audios, videos, PDFs capturados
│   │   └── _papelera/
│   ├── _procesado/                       ← capturas ya convertidas en memoria
│   └── _papelera/
├── 08_Categorias/
│   ├── 00_CATEGORIAS.md                  ← árbol de subjects (lo que ve "Temas")
│   └── PROYECTOS.md                      ← lista de proyectos
├── 09_Sistema/
│   ├── NUCLEO.md                         ← memoria core (≤2.000 chars, siempre en contexto)
│   ├── log.md  +  Log_Archivo/           ← log de eventos del sistema, por día
│   ├── Modos/                            ← definición de modos de chat (datos, no código)
│   ├── Canvas/  (+ _papelera/)           ← tableros de brainstorming, un JSON por tablero (§5.5)
│   └── _derivados/mem.db                 ← índice de búsqueda derivado, 100% regenerable (§5.4)
└── 10_Sesiones/                          ← historiales de chat (no son memorias)
```

---

## 2. Ciclo de vida: de captura a memoria

```
capturar → 07_Inbox/<id>.md  →  procesador (LLM)  →  Entradas/<slug>.md  +  índices
                                                      └→ la captura se mueve a _procesado/
```

### 2.1 La captura (item de inbox)

Grabar **nunca pierde nada y nunca depende del LLM**: la captura se escribe cruda al inbox y se procesa después. Nombre de archivo: `AAAA-MM-DD_HHMMSS_<slug-del-texto>.md` (si dos capturas caen en el mismo segundo, se desempata con sufijo `-2`).

```markdown
---
id: 2026-08-07_101500_idea-para-el-museo-vr
capturado: '2026-08-07T10:15:00-05:00'    # instante real, ISO con zona
tipo: nota                                 # nota | link | tarea
origen: movil                              # movil | app | api…
adjunto: 07_Inbox/_adjuntos/foto.jpg       # '' si no hay
contexto_usuario: ''                       # aclaración del usuario, tiene PRIORIDAD
tags: []                                   # si el usuario los puso a mano
subjects: []                               # ídem
fijado: []                                 # qué campos fijó el usuario: [tags, subjects, texto]
estado: pendiente                          # pendiente | procesada | error
coords_captura: '-12.121000,-77.030000'    # dónde estaba el aparato; la clave no existe sin permiso
---
El texto crudo de la captura.
```

**Regla de oro:** lo listado en `fijado` lo puso el usuario y **ningún pase automático lo sobreescribe**. `contexto_usuario` tiene prioridad sobre cualquier clasificación del LLM.

**`coords_captura`** es el par espacial de `capturado`: dónde estaba el aparato al anotar, no de qué habla la nota (eso es `lugar`). Lo pone la PWA con `navigator.geolocation` — el permiso del navegador es el único interruptor, y sin él la clave simplemente no aparece. Nunca bloquea ni demora la captura: se encola primero y las coordenadas se sellan después (tope 3 s, y si no llegan la captura sale igual).

### 2.2 El procesamiento (catalogación)

El procesador toma cada pendiente y lo entiende con un LLM, dándole: el texto, el contenido real de hasta 3 URLs enlazadas, la transcripción del adjunto (visión/audio/PDF) y **el árbol de categorías existente**. El LLM devuelve un JSON:

```json
{"titulo": "...", "sintesis": "...", "tipo": "nota|link|tarea",
 "tags": ["..."], "subjects": ["Categoria/Subcategoria", "..."],
 "lugar": "...", "cuando": "..."}
```

- `sintesis`: todo lo relevante reescrito, denso, sin perder datos concretos.
- `subjects`: **reutiliza el árbol existente**; solo crea uno nuevo si nada encaja.
- `lugar`: lugar al que se refiere la información (vacío si no menciona ninguno; no se inventa).
- `cuando`: fecha **a la que se refiere** la información (ISO 8601), resolviendo lo relativo ("mañana") contra la fecha de captura. No se rellena con la fecha de captura.

Con eso se crea/actualiza la memoria (§3) y se actualizan los índices (§5). La captura se mueve a `_procesado/` con `estado: procesada` y un campo `entrada:` apuntando a la memoria que generó. Si falla, queda en el inbox con `estado: error` y el motivo en `error:`.

**Reproceso:** una captura procesada puede volver al inbox (p. ej. un adjunto que el modelo anterior no pudo leer). Como el item ya sabe qué entrada creó (`entrada:`), el reproceso escribe **en esa misma entrada** (el título nuevo reemplaza al viejo), nunca en una gemela.

---

## 3. La memoria (entrada de la Biblioteca)

Un archivo en `06_Biblioteca_Conocimiento/Entradas/<slug>.md`. Ejemplo real:

```markdown
---
id: 20260804150445-2c9e
version: 1
titulo: Black Tree - Cliente VR en Perú
creada: '2026-07-31'
actualizada: '2026-08-01'
origen: inbox
adjunto: ''
subjects:
- Trabajo/Clientes
- Tecnología/VR
tags:
- VR
- Perú
---

# Black Tree - Cliente VR en Perú

## Resumen
Cliente directo Black Tree, desarrollando simuladores de realidad virtual en Perú…

## Registro histórico

**2026-07-31** (inbox) — entrada creada.
**2026-08-01** (reorganización) — temas: Tecnología/Realidad Virtual → Trabajo/Clientes, Tecnología/VR.
```

### 3.1 Identidad: slug + id

| Cosa | Qué es | Regla |
|---|---|---|
| **slug** (nombre de archivo) | Identidad de *archivo* y clave de dedupe | Del título: minúsculas, sin tildes, no-alfanumérico → `-`, máx. 60 chars. **Guardar con un slug existente ACTUALIZA esa memoria**, no crea otra. |
| **id** (frontmatter) | Identidad *estable* de la memoria | `AAAAMMDDHHMMSS-<4 hex>`. Sobrevive a retitulados y liga las versiones (`_versiones/<id>/`). |

### 3.2 Frontmatter completo

| Campo | Tipo | Significado |
|---|---|---|
| `id` | str | Identidad estable (ver arriba). |
| `version` | int | Arranca en 1; sube con cada edición de contenido. |
| `titulo` | str | Título legible; también es el `# H1` del cuerpo. |
| `creada` / `actualizada` | fecha ISO | Solo fecha (`AAAA-MM-DD`). |
| `subjects` | lista | Categorías jerárquicas `Grupo/Hoja` (§4.1). |
| `tags` | lista | Etiquetas planas (§4.2). |
| `origen` | str | De dónde nació: `inbox`, `chat`, `edición`… |
| `adjunto` | str | Ruta relativa al medio (`07_Inbox/_adjuntos/...`), o `''`. |
| `lugar` | str | Lugar al que se refiere la información. |
| `cuando` | str ISO | Fecha a la que se refiere la información (no la de captura). |
| `enlaces` | lista | URLs que traía la captura. |
| `capturado` | str ISO | Instante de la captura original. |
| `coords_captura` | str | `"lat,lon"` de dónde se capturó (≠ `lugar`). Viene de la captura. |
| `lugar_captura` | str | Ese punto en palabras ("Miraflores, Lima, Perú"), resuelto una vez con Nominatim inverso al procesar. `''` si no hubo red. |
| `sesion` | str | Id de la sesión de chat de la que nació (si aplica). |
| `pendiente` | lista | Adjuntos que el LLM no pudo leer (se limpia al reprocesar). |
| `sintesis_de` | str | Solo en páginas de síntesis (§3.5): el subject que sintetiza. Su presencia marca la entrada como derivada. |
| `fuentes` | lista | Solo en páginas de síntesis: slugs de las memorias que la alimentaron en la última regeneración. |

Reglas de actualización de metadatos al escribir sobre una entrada existente: **las listas se unen** (sin duplicados, se conserva el orden), **los escalares ya presentes no se pisan**. Excepción: el reproceso reemplaza título, resumen y subjects (los anteriores eran una conjetura sobre material ilegible).

Archivos de antes del 2026-09-05 pueden traer `privada: true/false` en el frontmatter: es un campo histórico, ignorado por completo — la visibilidad de la memoria hoy es la de su proyecto (§4.3), no algo que se lea o escriba en la entrada.

### 3.3 Cuerpo: tres secciones fijas

```markdown
# Título

## Resumen
La síntesis viva. Se reescribe cuando hay una mejor.

## Contenido del adjunto        ← solo si hay adjunto no-textual
Transcripción literal: lo que se ve en la foto/video, lo que se oye en el
audio, el texto del PDF. Es LA ÚNICA copia en texto del medio — es lo que
hace la memoria encontrable por contenido. Se REEMPLAZA en cada reproceso
(nunca dos transcripciones contradictorias).

Un video no deja dos bloques (lo que se ve por un lado, lo que se oye por
otro) sino UNA línea de tiempo, ordenada por segundo y con la marca puesta:
`[mm:ss] 🗣 …` lo hablado, `[mm:ss] 👁 …` lo que se ve. Es lo que hace que
"mirá, esto es lo que te quería mostrar" y el pájaro del fotograma siguiente
signifiquen algo juntos. Va ENTERA, sin tope: se lee por partes con
`leer_pagina`, y lo que se le muestra a un modelo se resume aparte
(`media.condensar`). Un link de YouTube deja esa misma línea de tiempo con
`adjunto` vacío y la URL en `enlaces`: el video se mira y se tira.

## Registro histórico
**AAAA-MM-DD** (origen) — qué pasó.       ← solo se AGREGA, nunca se reescribe
```

El registro histórico es *append-only*: cada creación, actualización, edición o reorganización deja una línea fechada. Es la historia de la memoria.

**Wikilinks.** El cuerpo puede enlazar otras memorias con `[[slug]]` o `[[slug|texto visible]]` (el slug es el nombre de archivo sin `.md`). El sistema los indexa como enlaces dirigidos (salientes y backlinks) y la app los pinta como links a la ficha. **Ningún automatismo escribe wikilinks**: la app *sugiere* memorias relacionadas (por similitud semántica), y solo cuando el usuario confirma se agrega una línea fechada al registro histórico:

```markdown
**2026-08-07** (enlace) — vinculada con [[otro-slug]].
```

### 3.4 Versionado

Antes de pisar contenido (título, resumen, nota — no un toggle de tag), el archivo entero tal como estaba se copia a `Entradas/_versiones/<id>/vNNN.md` y `version` sube. Los lectores usan glob **no recursivo** sobre `Entradas/*.md`, así que las versiones no contaminan búsqueda ni índices.

### 3.5 Páginas de síntesis (`sintesis_de`)

Una memoria normal guarda **lo que se capturó**. Una página de síntesis guarda **lo que la biblioteca sabe** de un tema: el LLM lee todas las memorias bajo un subject y escribe una página integrada (agrupa, conecta, conserva los datos concretos, y dice explícitamente cuándo dos memorias se contradicen).

Es una entrada como cualquier otra —así hereda versionado, registro histórico, papelera, índices, búsqueda, grafo y ficha sin código nuevo— con dos diferencias:

| | Regla |
|---|---|
| **Slug** | Determinista: `sintesis-<slug del subject>`. Regenerar escribe siempre **la misma página** (con `reemplazar=True`: resumen nuevo, versión anterior en `_versiones/`, registro histórico intacto). |
| **Es derivada** | Se puede borrar sin perder nada: sus fuentes quedan intactas. Por eso el lint no la compara con ellas (se les parece por definición), el contraste de la ingesta la saltea, y **una síntesis nunca se alimenta de otra** — ni de la de su subject padre. |
| **Ciclo de vida** | Se **crea** solo a pedido (botón "✦ Sintetizar" en Temas, `mem sintesis <subject>`, `POST /memory/synthesize`). Se **refresca** sola al final de `procesar_inbox`, y solo si ya existía: ningún automatismo decide qué tema merece página viva. |
| **Cierre** | La última línea del resumen es `Fuentes: [Título](Entradas/slug.md), …`, generada por **código** (no por el modelo): links markdown que existen de verdad y que el lint vigila. Nunca `[[wikilinks]]` (§7.14). |

"Desactualizada" se decide sin LLM, con puro frontmatter: apareció una memoria bajo el tema que no está en `fuentes`, o alguna fuente tiene `actualizada` posterior a la de la síntesis.

---

## 4. Las dos taxonomías: subjects y tags

Son cosas distintas y conviven:

| | `subjects` | `tags` |
|---|---|---|
| Forma | Jerárquica: `Grupo/Hoja` | Plana: etiqueta suelta |
| Ejemplo | `Tecnología/VR`, `Trabajo/Clientes` | `VR`, `Perú`, `ia-generativa` |
| Vive en | Árbol `00_CATEGORIAS.md` + frontmatter | Solo frontmatter |
| Rol | Navegación por temas (pantalla "Temas"), agrupación | Filtro transversal |
| Búsqueda | Por **prefijo de path**: `Tecnologia/IA` encuentra `Tecnologia/IA/LLMs` | Match exacto (insensible a mayúsculas/tildes) |

### 4.1 Subjects

- **Separador canónico: `/`**. Al escribir se normaliza cualquier variante (`>`, `»`, `|`, `\`, `::`, `--`) a `/`; espacios colapsados; sin duplicados. `Tecnología > IA` se guarda como `Tecnología/IA`.
- Una memoria puede tener **varios** subjects.
- El match por prefijo es **por segmento**: `Proyectos/Casa` NO matchea `Proyectos/CasaNueva`.
- Comparaciones insensibles a mayúsculas y tildes (normalización NFD, se quitan diacríticos).
- Más de dos niveles se aplanan en la hoja al indexar: `A/B/C` cuelga de `## A` como hoja `B/C`.
- **Los proyectos son subjects**: un proyecto llamado "Obra" = subject `Proyectos/Obra`. Así heredan gratis índices, árbol y búsqueda, sin segunda taxonomía. La lista de proyectos (nombre, `privado` bool, `clave_hash`, fecha) vive en `08_Categorias/PROYECTOS.md`.
- **Una memoria vive en un solo proyecto**: si llegan subjects de más de uno, se queda con el primer `Proyectos/<n>` (y sus hijos) y descarta los demás — con la privacidad del lado del proyecto (§4.3), dos proyectos en una misma memoria no tienen una respuesta que tenga sentido.
- Regla para catalogadores (humanos o LLM): **reusar el árbol existente**; crear un subject nuevo solo si de verdad nada encaja.

### 4.2 Tags

- Etiquetas libres y planas, pero con **vocabulario controlado por reutilización**: antes de etiquetar, se obtiene la lista de tags ya usados (ordenada por frecuencia, tope 60) y se instruye reusarlos **tal cual están escritos**. Sin esto la base llegó a 125 tags para 45 entradas (`IA generativa` vs `ia-generativa` vs `imagen IA`).
- Dos tags se consideran **el mismo** si solo difieren en mayúsculas, tildes o separador (`-`/`_`/espacio). La forma canónica al unificar: la variante más usada; a igual uso gana la minúscula, después la más corta.
- Al actualizar una entrada los tags se **unen** (nunca se pierden); al editar a mano, la lista del usuario **reemplaza** (el usuario manda).

---

### 4.3 Visibilidad: lo privado es del proyecto

Un proyecto **ordena**; un proyecto **privado además encierra** (cambio de criterio del 2026-09-05: antes `privada` era un campo propio de la memoria, sembrado una vez al crearla — ver más abajo por qué se abandonó).

- **`privado` es un campo del proyecto** (`08_Categorias/PROYECTOS.md`, §4.1), no de la memoria — la entrada no tiene ningún campo de visibilidad propio. Togglear un proyecto cambia al instante la visibilidad de **todo** lo suyo (sus memorias, sus capturas pendientes, sus sesiones): no hay nada que migrar.
- **El proyecto de una memoria, sesión o captura se lee de sus subjects** (el primer `Proyectos/<n>`, §4.1) o, si todavía no tiene subjects, del campo `proyecto` (sesiones, capturas recién llegadas). Lo que no cuelga de ningún proyecto es siempre público.
- **"Todo" es solo lo público**: parado en la Biblioteca compartida (sin proyecto activo) se ve lo público de todos los proyectos, nunca lo privado de ninguno — ni a una búsqueda, ni a un grep, ni a la galería, ni al contexto de un chat de otro proyecto. Parado *en* un proyecto se ve lo suyo (privado incluido) más lo público de los demás.
- **`proyecto=None` (MCP/CLI sin contexto) y `proyecto=""` ("Todo") son lo mismo**: solo lo público. Leer dentro de un proyecto privado por MCP/CLI exige además su **clave** (`clave_hash` en `PROYECTOS.md`, §4.1) — sin ella, error explícito, no un vacío silencioso.
- **Por qué se dejó de sembrar en la memoria** (hasta el 2026-09-05): un campo propio evitaba que cambiar el ámbito de un proyecto revelara o escondiera retroactivamente lo ya guardado, pero costaba una casilla de ficha ("Solo este proyecto") que mentía si la memoria tenía dos proyectos o ninguno, y una migración aparte para las entradas viejas. El modelo de un solo campo de verdad (§4.1: una memoria, un proyecto) es más simple y explicable, al costo de que cambiar `privado` sí afecta retroactivamente todo lo que cuelga del proyecto — decisión tomada a propósito.

## 5. Los índices (derivados, regenerables)

Tres archivos. Se actualizan en cada escritura, y se pueden **regenerar por completo desde el frontmatter de las entradas** (`reconstruir_indices`) — el frontmatter es la única fuente de verdad; los índices previos van a `_papelera/` fechados.

### 5.1 `08_Categorias/00_CATEGORIAS.md` — el árbol de temas

```markdown
# Categorias -- MeM

## Tecnología                                        ← grupo (nivel 1)
- [Entrada colgada del grupo](../06_Biblioteca_Conocimiento/Entradas/slug.md)
- **VR**                                             ← hoja (nivel 2)
  - [Black Tree - Cliente VR en Perú](../06_Biblioteca_Conocimiento/Entradas/black-tree-cliente-vr-en-peru.md)
```

Gramática: `## Grupo` / `- **Hoja**` / links indentados debajo. Los links son relativos a `08_Categorias/` (por eso el `../`). Un subject nuevo crea su hoja **en su grupo real** (nunca en una sección "varios").

### 5.2 `06_Biblioteca_Conocimiento/00_INDICE_TEMATICO.md` — por tema + cronológico

```markdown
# Índice Temático

## Por etiqueta/categoría

### Tecnología/VR
- [Título](Entradas/slug.md) — Título. (2026-07-31)

## Todas las entradas (orden cronológico)
- **2026-07-31** — [Título](Entradas/slug.md)
```

Dos secciones con roles distintos:
- **Por etiqueta/categoría**: refleja el estado actual; se desindexa/reindexa al reasignar subjects.
- **Cronológica**: es **historia, no se borra nunca**. Si la entrada se borra, el link se degrada a texto `Título (borrada)`; si se retitula, el texto del link se corrige.

### 5.3 `00_INDICE_GENERAL.md`

Índice general en la raíz; participa de la búsqueda igual que los otros dos.

### 5.4 `09_Sistema/_derivados/mem.db` — índice de búsqueda (SQLite)

A diferencia de los tres índices Markdown, este es **binario y 100% desechable**: se puede borrar en cualquier momento y MeM lo regenera desde los `.md` (`mem index` o el botón "Reindexar búsqueda" de Ajustes; también se autorregenera si se corrompe). **Jamás es la única copia de un dato.** Contiene, por entrada: metadatos del frontmatter, un índice léxico FTS5 (BM25 sobre título, cuerpo, tags y subjects, sin tildes), el embedding de la síntesis (vector float32; el modelo se define en `config.json → embed_modelo`), la proyección 2D cacheada (mapa semántico), los wikilinks extraídos y un geocache de lugares (Nominatim; los no resueltos no se reintentan). La búsqueda fusiona BM25 + coseno por RRF; sin este archivo (o sin FTS5, o sin modelo) todo degrada al camino léxico naive de siempre. En Dropbox conviene ignorar `_derivados/` en la sincronización: no vale nada fuera de esta máquina.

### 5.5 `09_Sistema/Canvas/*.json` — tableros de brainstorming (datos de usuario, no índice)

Un JSON legible por tablero: `{id, titulo, creado, actualizado, tarjetas: [...]}`. Cada tarjeta tiene `id`, `tipo` (`memoria` = referencia por slug, `texto` = nota libre, `idea` = sugerencia del LLM con sus `fuentes`), `texto` y posición `x`/`y`. Las tarjetas `memoria` solo **referencian** entradas por slug — borrar una tarjeta jamás toca la entrada. Borrar un tablero lo mueve a `Canvas/_papelera/`.

### 5.6 `09_Sistema/avisos_semanticos.json` — hallazgos del LLM pendientes de confirmar

Lista de avisos: `{clave, tipo, fecha, origen, slugs, detalle}`. `tipo` ∈ `contradiccion` · `obsoleta` · `falta_pagina` · `hueco`; `origen` ∈ `ingesta` (el contraste que corre al procesar una captura contra sus vecinas semánticas) · `lint` (el pase "Análisis profundo" sobre toda la Biblioteca). **`slugs[0]` es siempre la memoria afectada** —donde se anotará si el usuario acepta— y el resto, las que la contradicen. `clave = "<tipo>|<slugs ordenados unidos por +>"`: el mismo hallazgo desde los dos pases es uno solo, y el mismo par en el otro sentido también.

No es un derivado recalculable (cuesta una llamada al modelo) ni una fuente de verdad: es una **bandeja de pendientes**. Cada aviso muere de una de dos formas, ambas decididas por el usuario en la pantalla Lint: *aceptar* → línea fechada en el registro histórico de la memoria afectada, citando a las otras con `[[wikilink]]`; *descartar* → la clave va a `lint_ignorar.json → avisos` y no vuelve ni en la próxima corrida del pase caro. Borrar el archivo entero solo pierde avisos sin atender.

---

## 6. Cómo se usan las memorias (recuperación)

El protocolo es **índices primero, páginas después** — el contexto crece con la pregunta, no con el tamaño de la base:

1. **`buscar(consulta)`** — con `mem.db` disponible, rankea por la búsqueda híbrida (BM25 + coseno fusionados por RRF: encuentra "auto" buscando "coche") y devuelve la mejor línea de cada entrada más las líneas de índice que sumen; sin índice cae al conteo naive de términos por línea (≥3 letras, sin tildes) sobre los 3 índices y los cuerpos. El formato de salida es el mismo en ambos caminos: líneas con links `(Entradas/slug.md)`, ordenadas por score.
2. **`leer_pagina(path, parte=1)`** — abre una página concreta. Las páginas de más de 20.000 chars (transcripciones de adjuntos) vienen en partes de ese tamaño, con un aviso final `(página larga: parte i/n; …)` para pedir la siguiente con `parte=i+1` — nada se corta en silencio. Si el path es un medio (jpg/mp4/pdf…), devuelve el markdown `![...](/attach/ruta)` en vez de bytes.
3. **`grep(patrón)`** — fallback regex sobre toda la base, excluyendo `10_Sesiones/`, papeleras y `_versiones/`.
4. **`buscar_memorias(...)`** — búsqueda estructurada solo sobre entradas: texto libre (busca en título, cuerpo, tags, subjects, lugar y enlaces) + filtros por `tag`, `subject` (prefijo), `desde`/`hasta` (matchea tanto la fecha del archivo como `cuando`), `lugar`, `sesion`. Con `orden="relevancia"` (el buscador de la app) el texto además suma los hits de la búsqueda híbrida y ordena por ese ranking; los demás órdenes conservan la semántica léxica exacta (los llamadores que filtran no reciben vecinos semánticos de regalo).
5. **`conexiones(slug)`** — sesiones que citaron la entrada (su frontmatter `paginas_usadas`), entradas que comparten subject, wikilinks salientes, backlinks (quién la nombra con `[[slug]]`) y `relacionadas`: sugerencias por similitud semántica con su score, que **no crean ningún enlace** hasta que el usuario confirma (§3.3).

Las páginas de síntesis (§3.5) **no se filtran** de ninguno de estos caminos: que la síntesis de un tema rankee junto a sus fuentes es justamente el punto — para una pregunta sobre el tema entero, es la mejor página que se puede abrir.

---

## 7. Invariantes — el checklist de compatibilidad

Si desarrollás algo nuevo que escribe o lee esta base, esto es lo que no se puede romper:

1. **Markdown + frontmatter YAML es la fuente de verdad.** Todo debe quedar legible y editable por un humano o por otro agente sin ninguna app.
2. **Nada se borra.** "Eliminar" = desindexar + mover a `_papelera/`. Editar contenido = versionar antes (`_versiones/<id>/vNNN.md`) y anotar en el registro histórico.
3. **El registro histórico es append-only.** Nunca se reescribe historia; la sección cronológica del índice temático tampoco.
4. **Lo fijado por el usuario manda.** Campos en `fijado` y `contexto_usuario` nunca los pisa un pase automático.
5. **Dedupe por slug, identidad por id.** Escribir con un slug existente actualiza; el `id` sobrevive a retitulados.
6. **Subjects normalizados con `/`** y reutilizando el árbol existente antes de inventar; comparaciones sin tildes ni mayúsculas, match por segmento.
7. **Tags reutilizando el vocabulario existente** (lista por frecuencia) antes de inventar; minúscula preferida.
8. **Toda escritura actualiza los índices** — o, si es masiva, los regenera desde el frontmatter. Un índice siempre debe poder tirarse y reconstruirse.
9. **Al actualizar: listas se unen, escalares no se pisan** (salvo reproceso, que reemplaza título/resumen/subjects).
10. **Los adjuntos se referencian por el frontmatter `adjunto`** (ruta relativa a la base). Antes de mandar un adjunto a la papelera hay que verificar que ninguna otra página viva lo nombre.
11. **La transcripción del adjunto va literal en `## Contenido del adjunto`**: es la única copia en texto del medio y lo que lo hace buscable. Se reemplaza, no se acumula.
12. **Capturar nunca depende del LLM.** Primero se persiste crudo al inbox; entender, catalogar e indexar es un paso posterior y reintentable.
13. **`mem.db` es derivado regenerable, nunca la única copia de un dato.** Se puede borrar sin perder nada; todo lo que contiene sale de los `.md` (la única excepción tolerada es el geocache, que se repuebla solo). Un fallo del índice jamás rompe una escritura del vault.
14. **Ningún automatismo escribe wikilinks.** Las sugerencias de enlace (similitud semántica) son solo lectura; el `[[wikilink]]` se escribe únicamente con confirmación explícita del usuario, como línea fechada en el registro histórico.
15. **El LLM propone, el usuario confirma, el Markdown recién ahí cambia.** Ningún hallazgo de un modelo (contradicción, dato obsoleto, concepto faltante) toca una memoria por su cuenta: queda como aviso en `avisos_semanticos.json` hasta que el usuario lo acepta o lo descarta. Mismo criterio que §14, y por la misma razón que §3: un falso positivo anexado solo sería historia imborrable.
16. **Las páginas de síntesis son derivadas, no fuentes** (§3.5). Se regeneran sobre sí mismas, nunca alimentan a otra síntesis, y ningún pase automático crea una que el usuario no haya pedido.
17. **Lo de un proyecto privado no sale de él** (§4.3). Vale para entradas, capturas y sesiones, y en todos los caminos de lectura: búsqueda, grep, página, galería, grafo, índices y MCP/CLI (que además exigen la clave del proyecto). El `privado` del proyecto es la única señal — ninguna memoria, sesión o captura tiene un campo de visibilidad propio.
