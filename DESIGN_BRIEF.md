# MeM — Brief de diseño UX/UI

**Qué es:** app personal de memoria + chats. La base de conocimiento vive en Markdown (hamuQ); el usuario conversa con ella por **modos** (Q&A, research, timeline, mindmap, brainstorm…), cada uno con sus propias sesiones. Un solo usuario (Diego). Idioma de la UI: español.

**Plataformas:** web desktop + móvil (el celular es clave: captura rápida y consulta en movimiento). Tema claro y oscuro.

**Personalidad deseada:** limpia, elegante, veloz; densidad tipo herramienta (no juguete); el Markdown es protagonista (tablas, código, links legibles).

---

## 1. Layout general
- Navegación principal: sesiones/chats, captura rápida, explorador de memoria, configuración.
- Sidebar colapsable en desktop; navegación móvil equivalente.
- Indicador global de estado del servidor/proveedor LLM (ok / sin conexión / procesando).

## 2. Chat (pantalla principal)
**Lista de sesiones**
- Item: título, modo (con icono/color por modo), nº de turnos, fecha de última actividad.
- Agrupado o filtrable por modo. Búsqueda por texto en títulos.
- Acciones por sesión: abrir, renombrar, **archivar** (ver flujo abajo).
- Estado vacío (sin sesiones).

**Crear sesión**
- Selector de modo: nombre + descripción de cada modo (vienen del sistema, lista dinámica).
- Título opcional (si no, se autogenera con el primer mensaje).

**Conversación**
- Mensajes de usuario y asistente diferenciados; render Markdown completo en el asistente (tablas, listas, código, links).
- **Actividad de herramientas** mientras el modelo trabaja: chips/indicadores por paso (`buscar`, `leer_pagina`, `grep`, `guardar_entrada`) + estado "pensando". Debe sentirse vivo sin ser ruidoso.
- **Metadata por respuesta:** páginas de la base citadas (idealmente clicables → abren la entrada en el explorador) + tokens de entrada del turno (dato pequeño, discreto).
- Errores en línea (proveedor caído, timeout) con opción de reintentar.
- Input: textarea multilínea con auto-alto, enviar con Enter, estados deshabilitado/enviando.

**Detalles de sesión (panel o vista)**
- Resumen rodante de la sesión (texto), páginas usadas acumuladas, tokens totales, modo, fechas.

**Archivar sesión (flujo)**
- Confirmación → se ejecuta el "destilado" (el sistema decide si guarda síntesis en la Biblioteca) → resultado visible: "se guardaron N entradas" o "nada que guardar" → sesión pasa a archivo.

## 3. Captura rápida (optimizada para móvil)
- Form mínimo, un gesto: tipo (`nota` | `link` | `tarea`), contenido (texto o URL), contexto opcional (proyecto/tema), tags opcionales.
- Confirmación inmediata "guardado en inbox" (la captura nunca se pierde; se procesa de noche).
- Pensada como pantalla de inicio en el celular (PWA): abrir → escribir → listo.

## 4. Explorador de memoria
- Vista de categorías (árbol de `00_CATEGORIAS.md`) → entradas por categoría.
- Vista de una entrada (render Markdown, con su registro histórico).
- Estado del inbox: capturas pendientes / procesadas / con error.
- Log del sistema (lectura, cronológico).
- Resultado de `lint` (problemas de consistencia) como lista accionable.

## 5. Configuración
- Proveedor LLM: `anthropic` | `openai` (LM Studio u otro endpoint compatible); modelo; base URL; API key (solo referencia a variable de entorno, nunca se muestra la key).
- Carpeta base hamuQ (path, solo lectura o con selector).
- Token de acceso del API (para uso remoto), puerto.
- Tema claro/oscuro/auto.

## 6. Modos futuros (diseñar el sistema, no cada pantalla todavía)
Cada modo puede traer un **renderer de salida propio** además del chat:
- `research`: respuesta con fuentes web citadas.
- `timeline`: línea de tiempo visual.
- `mindmap`: grafo/mapa mental.
- `brainstorm`: tarjetas de ideas.
El diseño debe definir: iconografía/color por modo, contenedor genérico donde vive el renderer dentro de la conversación, y cómo escala el selector de modos cuando haya 6–10.

## 7. Transversales
- Estados vacíos, de carga y de error en todas las vistas.
- Accesibilidad: contraste AA, targets táctiles ≥44px, navegación por teclado en desktop.
- Tipografía pensada para Markdown denso y español (acentos, ¿?/¡!).

## Fuera de alcance del diseño
- Autenticación multiusuario (usuario único).
- Edición de la base desde la UI (v1 es lectura + lo que escribe el chat).
