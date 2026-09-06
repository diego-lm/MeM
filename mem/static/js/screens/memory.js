// MEMORY — spec §6 + plan 2026-07-31: una omnibox que busca en memorias, inbox
// y sesiones a la vez (resultados agrupados), y cuatro vistas de exploración
// sobre los mismos datos: Recientes · Temas (árbol) · Tiempo (por mes) · Lugares.
// Tap en una ficha abre un vistazo (sheet) sin salir; "Abrir completo" → #entry.
import { html, useState, useEffect, useMemo } from "../../vendor/preact-htm.js";
import { useStore, setState, go } from "../state.js";
import { dict, fechaRelativa } from "../i18n.js";
import { get, post } from "../api.js";
import { ScreenHead, useProyectos, proyectosListos, ChipMenu, itemsDeProyectos, norm, TITULO_SEC,
         onMemoriasCambian, useProcesando, procesarInbox, IMG_EXT, VID_EXT, AUD_EXT } from "../ui.js";
import { usePrivado, privadosDe, esSesionPrivada, esMemoriaPrivada, proyectoDe } from "../privado.js";
import { leerIgnorados } from "./lint.js";
import { Vistazo } from "../vistazo.js";
import { GraphView } from "../vis/grafo.js";
import { TimelineGlobal } from "../vis/timeline.js";
import { GeoMap } from "../vis/geomapa.js";

import { COLORES } from "../vis/util.js";

// Índices de las vistas: el orden es el de L.memViewNames (i18n.js) y los usan
// tanto la banda de arriba como los bloques de contenido de abajo. Estaban
// escritos como números sueltos ("vista === 5" era Pendientes) y no había forma
// de reordenar nada sin cazarlos de a uno.
// Los índices siguen el orden de L.memViewNames (i18n.js). Proyectos y Mapa
// semántico salieron del menú (pedido 2026-09-05): al primero lo reemplaza el
// filtro de proyecto, que hace lo mismo desde cualquier vista. Los huecos 4 y
// 7 quedan para no renumerar los otros seis.
const V = { REC: 0, TEMAS: 1, TIEMPO: 2, LUGARES: 3, PEND: 5, GRAFO: 6, MEDIA: 8 };
// Un solo menú "cómo lo miro" en vez de dos grupos (Explorar / Mapas) que
// obligaban a adivinar en cuál estaba cada vista (pedido 2026-09-05). Cada
// opción lleva su glifo, y ese mismo glifo es el que queda en el chip: así el
// control ocupa una palabra corta en vez de dos menús.
const VISTAS = [[V.REC, "▤"], [V.TEMAS, "⊞"], [V.LUGARES, "⌖"], [V.TIEMPO, "◷"], [V.GRAFO, "⌬"], [V.MEDIA, "▦"]];
const glifoVista = (i) => (VISTAS.find(([v]) => v === i) || [0, "▤"])[1];
// glifos de los filtros: el chip muestra SOLO el glifo mientras está en su
// valor por defecto, y le suma la palabra en cuanto filtra algo. Un tema y la
// vista Temas comparten el ⊞ a propósito: son el mismo concepto.
const G_SUB = "⊞", G_TAG = "#", G_TIPO = "⬚", G_FECHA = "◷", G_PROY = "◈";

// De qué está hecha una memoria: nació en una sesión, trae un archivo, trae
// enlaces, o es texto a secas. Sale de campos que la entrada ya guarda — no hay
// un tipo nuevo que mantener en el frontmatter.
export const TIPOS_MEM = ["sesion", "adjunto", "url", "texto"];
export const tipoDe = (r) =>
  r.sesion ? "sesion" : r.adjunto ? "adjunto" : (r.enlaces || []).length ? "url" : "texto";
const GLIFO_TIPO = { sesion: "▮", adjunto: "▦", url: "⚯", texto: "▤" };

// Ficha de una memoria: lo que el procesador extrajo e indexó — síntesis,
// cuándo pasó, dónde, sus subjects y tags.
// En el celular se reduce al TÍTULO (pedido 2026-08-09): todo lo demás lleva
// .mem-ficha-sub y lo esconde una regla de ui.js — así entran muchas más de un
// vistazo y el resto se ve tocándola. El único ancho es CSS, sin ramas en JS.
function Ficha({ r, onOpen, L, lang, onReprocesar, reprocesando, privada, seleccionado, onSeleccionar }) {
  const meta = [selloFecha(r, lang), r.lugar, r.enlaces?.length ? `⚯ ${r.enlaces.length}` : "", r.adjunto ? "▦" : ""].filter(Boolean);
  const pend = r.pendiente || [];
  return html`
    <div role="button" tabindex="0" onClick=${onOpen} class="mem-ficha ${privada ? "mem-privada" : ""}"
         style="position:relative;padding:13px 14px;border-radius:var(--radius-md);background:var(--color-surface);border:1px solid ${pend.length ? "color-mix(in srgb,var(--color-accent) 45%,transparent)" : "var(--color-divider)"};cursor:pointer;box-shadow:var(--shadow-sm)">
      <!-- picker opcional (pedido 2026-08-10): juntar memorias de texto con la
           selección de medios para mandarlas juntas a una sesión. Sin
           onSeleccionar la ficha se comporta exactamente como siempre. -->
      ${onSeleccionar && html`
        <span role="button" tabindex="0" title=${seleccionado ? L.tQuitarSeleccion : L.tAgregarSeleccion}
              onClick=${(e) => { e.stopPropagation(); onSeleccionar(); }}
              style="position:absolute;top:7px;right:7px;width:30px;height:30px;border-radius:var(--radius-md);z-index:1;display:flex;align-items:center;justify-content:center;font-size:13px;background:${seleccionado ? "var(--color-accent)" : "var(--color-bg)"};color:${seleccionado ? "var(--color-bg)" : "var(--text-3)"};border:1px solid ${seleccionado ? "var(--color-accent)" : "var(--color-divider)"}">${seleccionado ? "✓" : "＋"}</span>`}
      <div class="mem-ficha-tit" style="font-size:14.5px;font-weight:600;margin-bottom:4px;${onSeleccionar ? "padding-right:32px" : ""}">${r.titulo}</div>
      ${r.resumen && html`
        <div class="mem-ficha-sub" style="font-size:13px;line-height:1.45;opacity:.75;margin-bottom:7px;overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2">${r.resumen}</div>`}
      ${!!pend.length && html`
        <div class="mem-pend mem-ficha-sub" style="display:flex;flex-direction:column;gap:3px;margin-bottom:7px;font-family:var(--font-mono);font-size:10px;line-height:1.45">
          ${pend.map((x) => html`<span>⚠ ${L.tUnprocessed}: ${x}</span>`)}
        </div>`}
      <div class="mem-ficha-sub" style="display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-bottom:${(r.subjects || []).length || (r.tags || []).length ? "7px" : "0"}">
        ${meta.map((x) => html`<span style="font-family:var(--font-mono);font-size:10px;opacity:.55">${x}</span>`)}
      </div>
      <div class="mem-ficha-sub" style="display:flex;flex-wrap:wrap;gap:5px">
        <!-- el proyecto es un subject más, pero es EL que ordena: se dibuja
             perfilado y con su ◈ en vez de escribir "Proyectos/Yachay" como
             una categoría cualquiera (pedido 2026-08-12) -->
        ${(r.subjects || []).map((x) => (String(x).startsWith("Proyectos/") ? html`
          <span style="font-family:var(--font-mono);font-size:9.5px;padding:2px 8px;border-radius:var(--radius-md);border:1px solid color-mix(in srgb,var(--color-accent) 55%,transparent);color:var(--color-accent-700)">${privada ? "⚿" : "◈"} ${String(x).slice(10)}</span>` : html`
          <span style="font-family:var(--font-mono);font-size:9.5px;padding:2px 8px;border-radius:var(--radius-md);background:color-mix(in srgb,var(--color-accent) 16%,transparent);color:var(--color-accent-700)">${x}</span>`))}
        ${(r.tags || []).map((x) => html`
          <span style="font-family:var(--font-mono);font-size:9.5px;padding:2px 8px;border-radius:var(--radius-md);background:color-mix(in srgb,var(--color-accent-2) 20%,transparent);color:var(--color-accent-2-700)">${x}</span>`)}
      </div>
      <!-- nació en una sesión: se dice y se puede volver a ella -->
      ${r.sesion && html`
        <div role="button" tabindex="0" class="mem-ficha-sub" onClick=${(e) => { e.stopPropagation(); go("chat", r.sesion); }}
             style="margin-top:8px;display:inline-flex;align-items:center;gap:6px;height:24px;padding:0 9px;border-radius:var(--radius-md);cursor:pointer;font-family:var(--font-mono);font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;border:1px solid var(--color-divider);color:var(--text-2)">
          ▮ ${L.tFromSession}: ${String(r.sesion).slice(0, 17)} ›</div>`}
      ${onReprocesar && html`
        <div role="button" tabindex="0" onClick=${(e) => { e.stopPropagation(); if (!reprocesando) onReprocesar(); }}
             class=${reprocesando ? "mem-btn-procesando" : "mem-btn-accent"}
             style="margin-top:10px;height:32px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:12.5px;cursor:${reprocesando ? "default" : "pointer"}">
          ${reprocesando ? "…" : `⟳ ${L.tReprocess}`}</div>`}
    </div>`;
}

// celda de la vista Media (venía de chat.js — el Media Manager se mudó acá
// entero el 2026-08-10): item = {ruta, titulo, slug, sesion}. slug vacío =
// todavía no es una memoria propia, así que onAbrir decide con qué navegar.
function CeldaMedia({ item, on, onToggle, onAbrir }) {
  const url = `/attach/${item.ruta}`;
  const esVid = VID_EXT.test(item.ruta), esAud = AUD_EXT.test(item.ruta);
  return html`
    <div role="button" tabindex="0" onClick=${onToggle}
         style="position:relative;aspect-ratio:1;border-radius:var(--radius-md);overflow:hidden;cursor:pointer;background:var(--color-surface);border:2px solid ${on ? "var(--color-accent)" : "transparent"};box-shadow:var(--shadow-sm)">
      ${esAud
        ? html`<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:22px;opacity:.55">♪</div>`
        : esVid
        ? html`<video src=${url} muted preload="metadata" style="width:100%;height:100%;object-fit:cover;display:block"></video>`
        : html`<img src=${url} alt=${item.titulo} loading="lazy" style="width:100%;height:100%;object-fit:cover;display:block" />`}
      <span style="position:absolute;top:5px;right:5px;width:20px;height:20px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:11px;background:${on ? "var(--color-accent)" : "rgba(0,0,0,.4)"};color:${on ? "var(--color-bg)" : "#fff"}">${on ? "✓" : ""}</span>
      ${(item.slug || item.sesion) && html`
        <span role="button" tabindex="0" title=${item.titulo} class="mem-hit" onClick=${(e) => { e.stopPropagation(); onAbrir(); }}
              style="position:absolute;bottom:5px;left:5px;width:20px;height:20px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:10.5px;background:rgba(0,0,0,.45);color:#fff">↗</span>`}
    </div>`;
}

const FILTRO_MEDIA_DEFAULT = { alcance: "todas", tipo: "todos", texto: "", desde: "", hasta: "" };
const TIPO_MEDIA_EXT = { imagen: IMG_EXT, video: VID_EXT, audio: AUD_EXT };

/** Vista Media de Memory (pedido 2026-08-10 — antes vivía atada a una sesión en
 *  chat.js): la galería entera de la base, catalogada Y suelta (medios.py del
 *  server ya mezcla las dos). "Sesión abierta" solo aparece si hay una — Memory
 *  no tiene sesión propia. */
function MediaTab({ lang, sesionActiva, proyecto, elegidos, onToggle, oculta, L }) {
  const [filtro, setFiltro] = useState(FILTRO_MEDIA_DEFAULT);
  const [items, setItems] = useState(null);
  const campo = (k) => (v) => setFiltro((p) => ({ ...p, [k]: v }));
  const opciones = { todas: L.mediaAlcance.todas, ...(sesionActiva ? { sesion: L.mediaAlcance.sesion } : {}) };

  useEffect(() => {
    const qs = new URLSearchParams({ texto: filtro.texto, desde: filtro.desde, hasta: filtro.hasta });
    if (filtro.alcance === "sesion" && sesionActiva) qs.set("sesion", sesionActiva.id);
    // "todas" ya no manda subject=Proyectos/<n>: el server acota solo con la
    // cabecera X-Proyecto (parado en un proyecto ve lo suyo + lo público de los
    // demás; en Todo, solo lo público) — pedirlo de nuevo acá era filtrar dos
    // veces por lo mismo (pedido 2026-09-05).
    setItems(null);
    get(`/media?${qs}`).then(setItems).catch(() => setItems([]));
  }, [sesionActiva?.id, proyecto, filtro.alcance, filtro.texto, filtro.desde, filtro.hasta]);

  // el candado también acá: la galería enseña el adjunto de una memoria privada
  // sin abrirla, así que sin verificar no puede mostrarlo (pedido 2026-08-23).
  // Y la galería es la del proyecto donde uno está parado, igual que el resto de
  // Memory (pedido 2026-09-05): el server manda lo suyo MÁS lo público de los
  // demás — bien para buscar, mal para una vista que dice "Media" a secas.
  const medios = (items || []).filter((e) => !oculta(e) && (!proyecto || proyectoDe(e) === proyecto)
    && (filtro.tipo === "todos" || TIPO_MEDIA_EXT[filtro.tipo].test(e.ruta)));

  return html`
    <div>
      <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:12px">
        ${Object.keys(opciones).length > 1 && html`
          <${ChipMenu} etiqueta=${opciones[filtro.alcance] || opciones.todas} on=${filtro.alcance !== "todas"} ancho=${180}
                       items=${Object.entries(opciones).map(([id, label]) => ({ id, label, on: id === filtro.alcance }))}
                       onPick=${campo("alcance")} />`}
        <${ChipMenu} etiqueta=${L.mediaTipo[filtro.tipo]} on=${filtro.tipo !== "todos"} ancho=${160}
                     items=${Object.entries(L.mediaTipo).map(([id, label]) => ({ id, label, on: id === filtro.tipo }))}
                     onPick=${campo("tipo")} />
        <input value=${filtro.texto} onInput=${(e) => campo("texto")(e.target.value)} placeholder=${L.phMediaSearch}
               style="flex:1;min-width:120px;height:30px;border-radius:var(--radius-md);border:1px solid var(--color-divider);background:var(--color-surface);padding:0 12px;font-size:12.5px;outline:none;color:var(--color-text)" />
        <input type="date" value=${filtro.desde} onInput=${(e) => campo("desde")(e.target.value)}
               style="height:30px;border-radius:var(--radius-md);border:1px solid var(--color-divider);background:var(--color-surface);padding:0 8px;font-size:11.5px;color:var(--color-text)" />
        <input type="date" value=${filtro.hasta} onInput=${(e) => campo("hasta")(e.target.value)}
               style="height:30px;border-radius:var(--radius-md);border:1px solid var(--color-divider);background:var(--color-surface);padding:0 8px;font-size:11.5px;color:var(--color-text)" />
      </div>
      ${items === null && html`<div style="opacity:.5;font-size:13px;padding:20px 4px">…</div>`}
      ${items !== null && !medios.length && html`<div style="opacity:.5;font-size:13px;padding:20px 4px;text-align:center">${L.tMediaEmpty}</div>`}
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(94px,1fr));gap:7px">
        ${medios.map((e) => html`
          <${CeldaMedia} key=${e.ruta} item=${e} on=${elegidos.has(`m:${e.ruta}`)}
                         onToggle=${() => onToggle({ tipo: "medio", ruta: e.ruta, titulo: e.titulo })}
                         onAbrir=${() => (e.slug ? go("entry", e.slug) : e.sesion ? go("chat", e.sesion) : null)} />`)}
      </div>
    </div>`;
}

/** Bandeja fija abajo: lo que se fue juntando (medios + memorias de texto) va
 *  a una sesión — la abierta si hay una, o una nueva. Mismo canal que usa Home
 *  para pasarle un borrador a Chat (sessionStorage `mem.draft.{id}`): Chat ya
 *  sabe leerlo, así que acá no hace falta ni una línea nueva de ese lado. */
function BandejaSeleccion({ seleccion, sesionActiva, proyecto, L, onLimpiar, onEnviado, onInsertar }) {
  const [creando, setCreando] = useState(false);

  function llevarA(sid) {
    const medios = seleccion.filter((x) => x.tipo === "medio");
    const enlazadas = seleccion.filter((x) => x.tipo === "memoria").map((x) => ({ slug: x.slug, titulo: x.titulo }));
    // Memory como popup de ESTA MISMA sesión ya montada: inyecta directo, sin
    // sessionStorage — go() a la ruta en la que ya estamos no dispara
    // hashchange y ese borrador quedaría sin leer (Chat no se remonta).
    if (onInsertar && sid === sesionActiva?.id) {
      onInsertar({ adjuntos: medios.map((x) => ({ ruta: x.ruta, titulo: x.titulo })), enlazadas });
    } else {
      sessionStorage.setItem(`mem.draft.${sid}`, JSON.stringify({ texto: "", adjuntos: medios.map((x) => x.ruta), enlazadas }));
      go("chat", sid);
    }
    onLimpiar();
    onEnviado?.();   // Memory como popup (pedido 2026-08-10): se cierra sola al mandar
  }
  // un solo botón (pedido 2026-08-10): a la sesión abierta si hay una, si no
  // crea una nueva — como si hubiéramos tipeado algo en el chat en blanco.
  async function agregar() {
    if (creando) return;
    if (sesionActiva) { llevarA(sesionActiva.id); return; }
    setCreando(true);
    try {
      const { id } = await post("/sessions", { subjects: [], titulo: "", modo: "chat", temporal: true, proyecto });
      llevarA(id);
    } catch { setCreando(false); }
  }

  if (!seleccion.length) return null;
  return html`
    <div style="position:sticky;bottom:0;left:0;right:0;display:flex;align-items:center;gap:8px;padding:10px 20px;margin:0 -20px;background:var(--color-surface);border-top:1px solid var(--color-divider);box-shadow:var(--shadow-lg);z-index:2">
      <span style="font-family:var(--font-mono);font-size:11.5px;flex-shrink:0">${seleccion.length} ${L.tSelectedWord}</span>
      <span style="flex:1"></span>
      <div role="button" tabindex="0" onClick=${agregar} class="mem-btn-accent"
           style="height:36px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:12.5px;cursor:${creando ? "default" : "pointer"};opacity:${creando ? .6 : 1};white-space:nowrap">
        ${creando ? "…" : L.tAddToSession}</div>
      <span role="button" tabindex="0" onClick=${onLimpiar} class="mem-hit" style="width:30px;height:30px;flex-shrink:0;display:flex;align-items:center;justify-content:center;cursor:pointer;opacity:.5">✕</span>
    </div>`;
}

// toggle chico de sub-modo dentro de una vista (Tiempo: bloques↔línea; Lugares:
// lista↔mapa) — mismo trazo que la tira de vistas de arriba
function ChipsModo({ opciones, valor, onPick }) {
  return html`
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:12px">
      ${opciones.map((nombre, i) => html`
        <div role="button" tabindex="0" onClick=${() => onPick(i)}
             style="height:32px;padding:0 12px;border-radius:var(--radius-md);display:flex;align-items:center;cursor:pointer;font-family:var(--font-mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;background:${valor === i ? "color-mix(in srgb,var(--color-accent) 15%,transparent)" : "transparent"};color:${valor === i ? "var(--color-accent-700)" : "color-mix(in srgb,var(--color-text) 55%,transparent)"};border:1px solid ${valor === i ? "color-mix(in srgb,var(--color-accent) 55%,transparent)" : "var(--color-divider)"}">
          ${nombre}</div>`)}
    </div>`;
}

// filas compactas para los bloques Inbox / Sesiones de la omnibox
function FilaInbox({ it, L, lang, onOpen }) {
  return html`
    <div role="button" tabindex="0" onClick=${onOpen}
         style="display:flex;align-items:center;gap:10px;padding:11px 12px;border-radius:var(--radius-md);border:1px solid var(--color-divider);background:var(--color-surface);margin-bottom:7px;cursor:pointer">
      <span style="height:20px;padding:0 7px;border-radius:var(--radius-md);flex-shrink:0;font-family:var(--font-mono);font-size:9.5px;display:flex;align-items:center;background:color-mix(in srgb,var(--color-ok) 18%,transparent);color:var(--color-accent-2-700)">${L.inboxTypes[it.tipo] || it.tipo || "nota"}</span>
      <span style="flex:1;min-width:0;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${it.texto || "—"}</span>
      <span style="font-family:var(--font-mono);font-size:9.5px;opacity:.5;flex-shrink:0">${fechaRelativa(it.capturado, lang)}</span>
    </div>`;
}
function FilaSesion({ ses, lang, onOpen, privada }) {
  return html`
    <div role="button" tabindex="0" onClick=${onOpen} class=${privada ? "mem-privada" : ""}
         style="display:flex;align-items:center;gap:10px;padding:11px 12px;border-radius:var(--radius-md);border:1px ${ses.estado === "archivada" ? "dashed" : "solid"} var(--color-divider);background:var(--color-surface);margin-bottom:7px;cursor:pointer;opacity:${ses.estado === "archivada" ? 0.75 : 1}">
      <span style="font-family:var(--font-mono);font-size:11px;color:var(--color-accent-700);flex-shrink:0">${ses.estado === "archivada" ? "⌖" : "▮"}</span>
      ${privada && html`<span style="flex-shrink:0;font-size:11px">⚿</span>`}
      <span style="flex:1;min-width:0">
        <span style="display:block;font-size:13.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${ses.titulo || ses.id}</span>
        <span style="display:block;font-family:var(--font-mono);font-size:9.5px;opacity:.55;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${(ses.subjects || []).join(" · ") || ses.modo}</span>
      </span>
      <span style="font-family:var(--font-mono);font-size:9.5px;opacity:.5;flex-shrink:0">${fechaRelativa(ses.actualizada, lang)}</span>
    </div>`;
}

function sumaNodo(n) {
  return n.entradas + n.hijos.reduce((acc, h) => acc + sumaNodo(h), 0);
}
function contarEntradas(arbol) {
  return arbol.reduce((n, g) => n + sumaNodo(g), 0);
}
function contarSubjects(arbol) {
  return arbol.reduce((n, g) => n + g.hijos.filter((h) => sumaNodo(h) > 0).length, 0);
}

// Dos relojes distintos, a propósito: Recientes filtra por CUÁNDO SE SUBIÓ (ts,
// el mtime del archivo — `creada`/`actualizada` son solo fecha y no sirven para
// una ventana de una hora), y Tiempo ordena por CUÁNDO PASÓ lo que cuenta la
// memoria, cayendo al ts si el procesador no extrajo hora.
const instante = (r) => new Date(
  (r.cuando || "").length > 10 ? r.cuando : r.ts ? r.ts * 1000 : `${r.fecha || "1970-01-01"}T00:00:00`);

// Sello de la ficha: del día de hoy sale como "hace 15 minutos"; de cualquier
// otro día, fecha CON hora (antes solo se veía el día).
function selloFecha(r, lang) {
  const d = instante(r);
  if (!d.getTime()) return "";
  const en = lang === "en";
  const ahora = new Date();
  if (d.toDateString() === ahora.toDateString()) {
    const min = Math.max(0, Math.round((ahora - d) / 60000));
    if (min < 1) return en ? "just now" : "hace un momento";
    if (min < 60) return en ? `${min} min ago` : `hace ${min} minuto${min > 1 ? "s" : ""}`;
    const h = Math.round(min / 60);
    return en ? `${h} hour${h > 1 ? "s" : ""} ago` : `hace ${h} hora${h > 1 ? "s" : ""}`;
  }
  return d.toLocaleString(en ? "en-US" : "es-ES",
    { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

// Ventana de fecha del buscador. "t" (todos) = sin corte — es una opción más,
// no un caso aparte: `ms` en 0 hace que enVentana() no filtre nada.
const VENTANAS = [["h", 3600e3], ["d", 86400e3], ["s", 604800e3], ["t", 0]];
const ventanaLabel = (id, lang) =>
  ({ h: "1 h", d: lang === "en" ? "1 day" : "1 día", s: lang === "en" ? "1 week" : "1 semana",
     t: lang === "en" ? "Anytime" : "Siempre" })[id];

// Tiempo: hoy por horas, el resto de la semana por días, lo anterior por mes+año.
function bloquesTiempo(lista, lang) {
  const loc = lang === "en" ? "en-US" : "es-ES";
  const ahora = new Date();
  const hoy0 = new Date(ahora.getFullYear(), ahora.getMonth(), ahora.getDate());
  const lunes = new Date(hoy0);
  lunes.setDate(hoy0.getDate() - ((hoy0.getDay() + 6) % 7)); // getDay(): 0=domingo
  const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
  const g = new Map();
  for (const r of lista) {           // lista ya viene de más nueva a más vieja,
    const d = instante(r);           // así que el primero de cada grupo lo ordena
    const [clave, etiq] = d >= hoy0
      ? [`h${d.getHours()}`, `${String(d.getHours()).padStart(2, "0")}:00`]
      : d >= lunes
        ? [`d${d.getMonth()}-${d.getDate()}`, cap(d.toLocaleDateString(loc, { weekday: "long", day: "numeric" }))]
        : [`m${d.getFullYear()}-${d.getMonth()}`, cap(d.toLocaleDateString(loc, { month: "long", year: "numeric" }))];
    if (!g.has(clave)) g.set(clave, { etiq, orden: d.getTime(), lista: [] });
    g.get(clave).lista.push(r);
  }
  return [...g.values()].sort((a, b) => b.orden - a.orden);
}

export function Memory({ onClose = null, onInsertar = null } = {}) {
  const s = useStore();
  const L = dict(s.lang);
  const [arbol, setArbol] = useState(null);
  const [abierto, setAbierto] = useState(null);      // grupo expandido en Temas
  const [todas, setTodas] = useState(null);          // todas las memorias (fecha desc)
  const [inboxItems, setInboxItems] = useState([]);
  const [sesiones, setSesiones] = useState([]);      // activas + archivadas
  const [lintCount, setLintCount] = useState(null);
  const [query, setQuery] = useState("");
  const [vista, setVista] = useState(V.REC);         // índice de V (arriba)
  const [tipos, setTipos] = useState([]);            // filtro por tipo; vacío = todas
  const [ventana, setVentana] = useState("t");       // fecha: arranca sin corte (ver VENTANAS)
  const [subjectClic, setSubjectClic] = useState(null);
  const [sintetizando, setSintetizando] = useState(false);
  const [lugarClic, setLugarClic] = useState(null);
  const [tiempoModo, setTiempoModo] = useState(0);   // Tiempo: 0 bloques · 1 línea temporal
  const [lugarModo, setLugarModo] = useState(0);     // Lugares: 0 lista · 1 mapa
  const [capaCaptura, setCapaCaptura] = useState(true);  // mapa: capa "desde dónde lo anoté"
  const [geo, setGeo] = useState(null);              // /memory/map (se pide al abrir el mapa)
  const [resultados, setResultados] = useState(null); // memorias que matchean la query
  // Filtros del buscador (pedido 2026-09-05). Solo acotan resultados de
  // búsqueda, no las vistas de abajo: buscar es justamente para encontrar lo
  // que NO está donde uno está parado, así que el proyecto arranca en "todos".
  // El buscador se abre al poner el cursor, no al escribir (pedido 2026-09-05):
  // igual que el de sesiones de Home. Con el foco puesto y sin texto, la lista
  // es la de siempre pasada por los filtros — escribir la reordena por
  // relevancia contra el server, no cambia de pantalla.
  const [foco, setFoco] = useState(false);
  // El proyecto de Memory SIGUE al del sidebar pero no lo manda (pedido
  // 2026-09-05): entrar acá, o cambiar de proyecto en la izquierda, lo pone en
  // ese; elegir otro acá mira ese otro sin mover dónde estás trabajando.
  const [fProy, setFProy] = useState(String(s.proyecto || ""));
  const [fTag, setFTag] = useState("");
  const [fSub, setFSub] = useState("");
  const [preview, setPreview] = useState(null);       // slug abierto en el vistazo
  const [reproc, setReproc] = useState(null);         // null | "todas" | slug en reproceso
  // picker de "llevar esto a una sesión" (pedido 2026-08-10): medios Y memorias
  // de texto van a la MISMA bandeja — el tipo distingue cómo se manda cada uno.
  const [seleccion, setSeleccion] = useState([]);     // {tipo:"medio"|"memoria", ruta?, slug?, titulo}
  const claveSel = (it) => (it.tipo === "medio" ? `m:${it.ruta}` : `e:${it.slug}`);
  const elegidos = useMemo(() => new Set(seleccion.map(claveSel)), [seleccion]);
  function alternarSeleccion(it) {
    const k = claveSel(it);
    setSeleccion((p) => (elegidos.has(k) ? p.filter((x) => claveSel(x) !== k) : [...p, it]));
  }
  const proyectos = useProyectos();
  const { procesando } = useProcesando();   // el mismo estado que ve la pantalla Inbox
  // candado de lo privado: en el celular sin verificar no se muestra lo privado.
  // Memorias y sesiones se miden igual (pedido 2026-08-23): lo que cuelga de un
  // proyecto privado se tapa, lo diga su campo `privada` o su proyecto de hoy.
  // Las dos dependen de /projects, así que hasta que conteste se ocultan TODAS
  // (mejor un parpadeo vacío que uno que muestra).
  const { oculto } = usePrivado();
  const privs = privadosDe(proyectos);
  const memPriv = (r) => esMemoriaPrivada(r, privs);
  const sesPriv = (x) => esSesionPrivada(x, privs);
  const ocultarMem = (r) => oculto && (!proyectosListos() || memPriv(r));
  const ocultarSes = (x) => oculto && (!proyectosListos() || sesPriv(x));

  // Lo que cambia cuando el procesador saca un item del inbox: la lista de
  // memorias, el árbol de temas (con sus contadores) y el propio inbox. El
  // primer render abre el grupo inicial; las recargas posteriores NO lo tocan,
  // que si no cada memoria procesada te devolvería al primer tema.
  function cargarMemorias(primera = false) {
    // sin catch, un server caído dejaba el árbol y su contador en "…" para siempre
    get("/memory/tree").then((t) => { setArbol(t); if (primera) setAbierto(t[0]?.nombre ?? null); }).catch(() => setArbol([]));
    get("/memory/search").then(setTodas).catch(() => setTodas([]));
    get("/inbox").then(setInboxItems).catch(() => {});
  }

  useEffect(() => {
    cargarMemorias(true);
    Promise.all([get("/sessions"), get("/sessions?archivadas=1")])
      .then(([a, b]) => setSesiones([...a, ...b])).catch(() => {});
    get("/lint").then((r) => { const ign = leerIgnorados(); setLintCount(r.problemas.filter((p) => !ign.has(p)).length); }).catch(() => {});
    // el procesador de fondo avisa cada vez que termina una memoria: con Memory
    // abierta la lista y el contador se ponen al día solos (pedido 2026-08-08)
    return onMemoriasCambian(() => cargarMemorias());
  }, [s.proyecto]);
  // cambiar de proyecto en el sidebar arrastra el de Memory; al revés no
  useEffect(() => setFProy(String(s.proyecto || "")), [s.proyecto]);

  // omnibox: memorias por el server (búsqueda híbrida: substring + BM25 +
  // embeddings, orden por relevancia); inbox y sesiones acá mismo (las listas
  // ya están cargadas, sin acentos)
  const q = norm(query.trim());
  useEffect(() => {
    if (!q) { setResultados(null); return; }
    const timer = setTimeout(() => {
      get(`/memory/search?texto=${encodeURIComponent(query.trim())}&orden=relevancia`)
        .then(setResultados).catch(() => setResultados([]));
    }, 250);
    return () => clearTimeout(timer);
  }, [query, s.proyecto]);
  const enBusqueda = foco || !!q;
  const enVentana = (r) => {
    const ms = VENTANAS.find(([id]) => id === ventana)?.[1] ?? 0;
    return !ms || (r.ts ? r.ts * 1000 : instante(r).getTime()) >= Date.now() - ms;
  };
  // inbox y sesiones se filtran acá: sus listas ya están en memoria
  const inboxHits = useMemo(() => !q ? [] : inboxItems.filter((it) =>
    norm([it.texto, it.tipo, it.contexto_usuario, (it.tags || []).join(" "), (it.subjects || []).join(" ")].join(" ")).includes(q)
    && pasaFiltros(it) && !ocultarMem(it)), [q, inboxItems, oculto, proyectos, fProy, fTag, fSub]);
  const sesionHits = useMemo(() => !q ? [] : sesiones.filter((x) =>
    norm([x.titulo, x.resumen, (x.subjects || []).join(" ")].join(" ")).includes(q)
    && pasaFiltros(x) && !ocultarSes(x)), [q, sesiones, oculto, proyectos, fProy, fTag, fSub]);

  // memorias con material que el LLM no pudo leer (spec: se ven en rojo y se
  // reprocesan cuando cambias de modelo). Solo salen en su propia vista: se
  // filtran una vez acá y así ninguna lista derivada las arrastra.
  const sinLeer = useMemo(() => (todas || []).filter((r) => (r.pendiente || []).length && !ocultarMem(r)),
    [todas, oculto, proyectos]);
  // los filtros por tipo, proyecto y candado se aplican UNA vez acá: todas las
  // vistas derivan de `visibles`
  const pasaTipo = (r) => !tipos.length || tipos.includes(tipoDe(r));
  // Proyecto · tema · tag: los tres chips de arriba acotan TODO lo que Memory
  // muestra, no solo el buscador — son los mismos controles, siempre a la vista.
  // Sirven igual para memorias, items de inbox y sesiones: los tres traen
  // `subjects`, y de ahí sale el proyecto igual que siempre.
  const pasaSubTag = (r) => (!fTag || (r.tags || []).includes(fTag))
    && (!fSub || (r.subjects || []).some((x) => x === fSub || String(x).startsWith(fSub + "/")));
  const pasaFiltros = (r) => (!fProy || proyectoDe(r) === fProy) && pasaSubTag(r);
  const visibles = useMemo(() => (todas || []).filter((r) => !(r.pendiente || []).length && pasaTipo(r) && pasaFiltros(r) && !ocultarMem(r)),
    [todas, tipos, oculto, proyectos, fProy, fSub, fTag]);
  // …salvo cuando el subject elegido YA es un proyecto (vista Proyectos): ahí el
  // filtro de arriba sobra y encima vaciaba la lista de todo proyecto que no
  // fuera el activo. Lo privado ajeno sigue afuera: eso lo acota el server.
  const visiblesSinProy = useMemo(() => (todas || []).filter((r) => !(r.pendiente || []).length && pasaTipo(r) && pasaSubTag(r) && !ocultarMem(r)),
    [todas, tipos, oculto, proyectos, fSub, fTag]);
  // Con texto manda el orden del server (relevancia); sin texto —el buscador
  // recién abierto— la base es todo lo accesible, que los mismos filtros acotan.
  const resultadosVis = useMemo(() => (q ? (resultados || []) : (todas || []).filter((r) => !(r.pendiente || []).length))
    .filter((r) => pasaTipo(r) && pasaFiltros(r) && enVentana(r) && !ocultarMem(r)),
    [q, resultados, todas, tipos, oculto, proyectos, fProy, fTag, fSub, ventana]);

  // vistas de exploración (todas en memoria, filtros al instante)
  const porSubject = useMemo(() => {
    if (!subjectClic) return [];
    const base = subjectClic.startsWith("Proyectos/") ? visiblesSinProy : visibles;
    return base.filter((r) => (r.subjects || []).some((x) => x === subjectClic || String(x).startsWith(subjectClic + "/")));
  }, [visibles, visiblesSinProy, subjectClic]);
  const recientes = useMemo(() => visibles.filter(enVentana), [visibles, ventana]);
  const bloques = useMemo(() => bloquesTiempo(
    [...visibles].sort((a, b) => instante(b) - instante(a)), s.lang), [visibles, s.lang]);
  const lugares = useMemo(() => {
    const g = new Map();
    for (const r of visibles) if (r.lugar) g.set(r.lugar, (g.get(r.lugar) || 0) + 1);
    return [...g.entries()].sort((a, b) => b[1] - a[1]);
  }, [visibles]);
  const porLugar = useMemo(() => !lugarClic ? [] : visibles.filter((r) => r.lugar === lugarClic), [visibles, lugarClic]);
  // slugs vedados por el candado: grafo/mapas los filtran (el server no sabe de privacidad)
  const ocultas = useMemo(() => new Set((todas || []).filter(ocultarMem).map((r) => r.slug)),
    [todas, oculto, proyectos]);

  // el mapa geográfico se pide recién al abrirlo (geocodifica hasta 5 lugares nuevos por pasada)
  useEffect(() => {
    if ((vista === V.LUGARES && lugarModo === 1) && geo === null)
      get("/memory/map").then(setGeo).catch(() => setGeo({ lugares: [], sin_geo: [] }));
  }, [vista, lugarModo]);
  const geoVisibles = useMemo(() => !geo ? [] : geo.lugares
    .map((l) => ({ ...l, memorias: l.memorias.filter((m) => !ocultas.has(m.slug)) }))
    .map((l) => ({ ...l, n: l.memorias.length })).filter((l) => l.n), [geo, ocultas]);
  // Desde dónde se capturó cada memoria: sale del propio /memory/search (las
  // coordenadas ya vienen en la ficha), así que no hace falta ni pedirle nada al
  // server ni geocodificar — y al armarse desde `visibles` respeta el candado de
  // lo privado igual que todo lo demás. Se agrupa por coordenada redondeada a
  // ~11 m: dos capturas en la misma mesa son un punto, no dos.
  const capturas = useMemo(() => {
    const g = new Map();
    for (const r of visibles) {
      const [lat, lon] = String(r.coords_captura || "").split(",").map(Number);
      if (!isFinite(lat) || !isFinite(lon)) continue;
      const clave = `${lat.toFixed(4)},${lon.toFixed(4)}`;
      const punto = g.get(clave) || { lat, lon, lugar: r.lugar_captura || clave, memorias: [] };
      punto.memorias.push({ slug: r.slug, titulo: r.titulo });
      g.set(clave, punto);
    }
    return [...g.values()];
  }, [visibles]);

  function elegirVista(i) { setVista(i); setSubjectClic(null); setLugarClic(null); }
  // si el último pendiente se resuelve estando en su vista, el tab desaparece:
  // sin esto quedaría una pantalla sin forma de salir.
  const vistaReal = vista === V.PEND && !sinLeer.length ? V.REC : vista;

  // vista Proyectos: un dropdown elige el proyecto y ESO es la lista (más rápido
  // que la grilla de chips + drill de antes — pedido 2026-08-05)
  const proyectosVis = proyectos.filter((p) => !oculto || !p.privado);
  const proyecto = fProy;    // la galería mira lo mismo que el resto de Memory
  const proyPriv = (n) => proyectos.some((p) => p.nombre === n && p.privado);

  async function reprocesar(slugs) {
    if (reproc) return;
    setReproc(slugs ? slugs[0] : "todas");
    try {
      await post("/memory/reprocess", { slugs: slugs || [] });
      setTodas(await get("/memory/search"));
    } catch { /* el detalle del fallo queda en el log del procesador */ }
    finally { setReproc(null); }
  }

  const cerrarBusca = () => { setQuery(""); setFoco(false); };
  const memorySub = arbol ? `${contarEntradas(arbol)} ${L.entriesWord} · ${contarSubjects(arbol)} ${L.categoriesWord}` : "…";
  const pendientes = inboxItems.filter((x) => x.estado === "pendiente").length;
  const conError = inboxItems.filter((x) => x.estado === "error").length;
  const inboxN = pendientes + conError;   // 0 = bandeja limpia: el card se apaga
  // grid (no lista suelta): el espaciado lo pone .mem-fichas, no cada Ficha
  const fichas = (lista) => html`
    <div class="mem-fichas">
      ${lista.map((r) => html`<${Ficha} key=${r.slug} r=${r} L=${L} lang=${s.lang} privada=${memPriv(r)} onOpen=${() => setPreview(r.slug)}
                                         seleccionado=${elegidos.has(`e:${r.slug}`)}
                                         onSeleccionar=${() => alternarSeleccion({ tipo: "memoria", slug: r.slug, titulo: r.titulo })} />`)}
    </div>`;
  // De qué se puede filtrar una búsqueda: lo que las memorias YA tienen. Sin
  // catálogo aparte — si un tag no está en ninguna, tampoco tiene por qué estar
  // en el menú. El proyecto sale de /projects (los privados que el candado deja).
  const tagsTodos = useMemo(() => [...new Set((todas || []).flatMap((r) => r.tags || []))].sort(), [todas]);
  const subsTodos = useMemo(() => [...new Set((todas || []).flatMap((r) =>
    (r.subjects || []).filter((x) => !String(x).startsWith("Proyectos/"))))].sort(), [todas]);
  // el candado se cerró con el vistazo de una memoria privada abierto: no se muestra
  const previewBloqueado = preview && oculto && (todas || []).some((r) => r.slug === preview && memPriv(r));
  // Página de síntesis del tema: la escribe el LLM una vez y de ahí en más el
  // procesador de inbox la mantiene al día sola. Es una memoria más, así que
  // termina en su ficha de siempre.
  async function sintetizar() {
    setSintetizando(true);
    try {
      const r = await post("/memory/synthesize", { subject: subjectClic });
      go("entry", r.slug);
    } catch { /* el aviso ya lo da el fetch; el tema sigue ahí */ }
    setSintetizando(false);
  }
  // Temas y Proyectos comparten el detalle: un proyecto ES un subject
  const filtradas = html`
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
      <div role="button" tabindex="0" onClick=${() => setSubjectClic(null)} style="font-family:var(--font-mono);font-size:11px;cursor:pointer;opacity:.6">‹ ${s.lang === "en" ? "back" : "volver"}</div>
      <span style="font-size:12px;opacity:.7">${subjectClic} · ${porSubject.length}</span>
      <!-- la acción más valiosa de la pantalla (llama al LLM y crea una memoria)
           iba en gris al 80% y a 28px: se leía como un detalle. Ahora es lo único
           con color en la fila, que es lo que es. -->
      ${!!porSubject.length && html`
        <div role="button" tabindex="0" onClick=${() => !sintetizando && sintetizar()}
             style="margin-left:auto;height:34px;padding:0 13px;border-radius:var(--radius-md);border:1px solid color-mix(in srgb,var(--color-accent) 55%,transparent);background:color-mix(in srgb,var(--color-accent) 10%,transparent);color:var(--color-accent-700);display:flex;align-items:center;font-family:var(--font-mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;opacity:${sintetizando ? 0.5 : 1}">
          ${sintetizando ? "…" : (s.lang === "en" ? "✦ Synthesize" : "✦ Sintetizar")}
        </div>`}
    </div>
    ${!porSubject.length && html`<div style="opacity:.5;font-size:13px">${L.tNoRes}</div>`}
    ${fichas(porSubject)}`;

  return html`
    <div class="mem-screen ancha" style="position:relative;flex:1;display:flex;flex-direction:column;animation:scIn .42s cubic-bezier(.22,1,.36,1);min-height:0">
      <${ScreenHead} titulo="Memory" sub=${memorySub} onBack=${onClose || (() => go("home"))} />
      <div style="padding:4px 20px 12px">
        <!-- La bandeja va ARRIBA del buscador y solo existe cuando hay algo en
             ella (pedido 2026-09-05): vacía era una fila apagada que ocupaba el
             primer renglón de la pantalla para decir "cero". Y si está, trae su
             propio "Procesar ahora" — entrar a Inbox solo para apretar el botón
             era un viaje de ida y vuelta por nada. -->
        ${!!inboxN && html`
          <div role="button" tabindex="0" onClick=${() => go("inbox")}
               style="position:relative;border-radius:var(--radius-md);background:var(--color-surface);border:1px solid var(--color-divider);padding:11px 12px;display:flex;gap:12px;align-items:center;cursor:pointer;box-shadow:var(--shadow-sm);margin-bottom:10px">
            <div style="width:34px;height:34px;border-radius:var(--radius-md);background:color-mix(in srgb,var(--color-accent) 22%,transparent);display:flex;align-items:center;justify-content:center;font-family:var(--font-mono);font-size:14px;font-weight:700;color:var(--color-accent-700)">${inboxN > 99 ? "99+" : inboxN}</div>
            <div style="flex:1"><div style="font-size:14.5px;font-weight:600">${L.tInbox}</div>
              ${!!conError && html`
                <div style="font-family:var(--font-mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--color-accent-700)">${conError} ${L.errWord}</div>`}</div>
            <div role="button" tabindex="0" onClick=${(e) => { e.stopPropagation(); if (!procesando) procesarInbox(); }}
                 class=${procesando ? "mem-btn-procesando" : "mem-btn-accent"}
                 style="height:30px;padding:0 13px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:11.5px;font-weight:600;cursor:${procesando ? "default" : "pointer"}">
              ${procesando ? "…" : L.tProcessNow}
            </div>
            <span style="opacity:.35">›</span>
          </div>`}
        <div style="height:44px;border-radius:var(--radius-md);background:var(--color-surface);border:1px solid ${enBusqueda ? "color-mix(in srgb,var(--color-accent) 55%,var(--color-divider))" : "var(--color-divider)"};display:flex;align-items:center;padding:0 12px;gap:9px;box-shadow:var(--shadow-sm)">
          <span style="font-family:var(--font-mono);opacity:.5;font-size:13px">⌕</span>
          <input value=${query} onInput=${(e) => setQuery(e.target.value)} placeholder=${L.phMemSearch}
                 onFocus=${() => setFoco(true)} onClick=${() => setFoco(true)}
                 onKeyDown=${(e) => { if (e.key === "Escape") cerrarBusca(); }}
                 style="flex:1;min-width:0;border:0;background:transparent;outline:none;font-family:var(--font-body);font-size:15px;color:var(--color-text)" />
          ${enBusqueda && html`<span role="button" tabindex="0" onClick=${cerrarBusca} style="cursor:pointer;opacity:.45;font-size:12px">✕</span>`}
        </div>
        <!-- Izquierda: CÓMO se mira (un solo menú, con la palabra a la vista).
             Derecha, contra el borde y separado por una línea: con qué se ACOTA
             —proyecto, tema, tag, tipo, fecha— y Pendientes. Todos arrancan en
             "Todos" y lo dicen: un glifo solo no distingue "sin filtrar" de
             "filtrando por algo que no entra en el chip" (pedido 2026-09-06). -->
        <div class="mem-fila-acota">
          <${ChipMenu} etiqueta=${`${glifoVista(vistaReal)} ${L.memViewNames[vistaReal]}`}
                       on=${!enBusqueda} ancho=${210} titulo=${L.tVista} haciaDerecha=${true}
                       items=${VISTAS.map(([i, g]) => ({ id: i, glyph: g, label: L.memViewNames[i], on: !enBusqueda && vistaReal === i }))}
                       onPick=${(i) => { setFoco(false); elegirVista(i); }} />
          <div class="mem-acota">
            <${ChipMenu} etiqueta=${`${G_PROY} ${fProy || L.tSinFiltro}`} on=${!!fProy} ancho=${230}
                         titulo=${L.tSesProyAll} clase=${proyPriv(fProy) ? "mem-privada" : ""}
                         items=${itemsDeProyectos(proyectosVis, fProy, [{ id: "", label: L.tSesProyAll }], s.lang)}
                         onPick=${setFProy} />
            <${ChipMenu} etiqueta=${`${G_SUB} ${fSub || L.tSinFiltro}`} on=${!!fSub} ancho=${250}
                         titulo=${L.tMemSubAll} buscador=${L.tMemSubAll}
                         items=${[{ id: "", glyph: G_SUB, label: L.tMemSubAll, on: !fSub },
                                  ...subsTodos.map((x) => ({ id: x, glyph: G_SUB, label: x, on: fSub === x }))]}
                         onPick=${setFSub} />
            <${ChipMenu} etiqueta=${`${G_TAG} ${fTag || L.tSinFiltro}`} on=${!!fTag} ancho=${230}
                         titulo=${L.tMemTagAll} buscador=${L.tMemTagAll}
                         items=${[{ id: "", glyph: G_TAG, label: L.tMemTagAll, on: !fTag },
                                  ...tagsTodos.map((x) => ({ id: x, glyph: G_TAG, label: x, on: fTag === x }))]}
                         onPick=${setFTag} />
            <${ChipMenu} etiqueta=${`${G_TIPO} ${tipos.length ? tipos.map((t) => L.memTypes[t]).join(", ") : L.tSinFiltro}`}
                         on=${!!tipos.length} multi=${true} ancho=${220} titulo=${L.tTipo}
                         items=${[{ id: "", glyph: G_TIPO, label: L.tSinFiltro, on: !tipos.length },
                                  ...TIPOS_MEM.map((t) => ({ id: t, glyph: GLIFO_TIPO[t], label: L.memTypes[t], on: tipos.includes(t) }))]}
                         onPick=${(id) => setTipos((p) => (!id ? [] : p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))} />
            <${ChipMenu} etiqueta=${`${G_FECHA} ${ventanaLabel(ventana, s.lang)}`}
                         on=${ventana !== "t"} ancho=${190} titulo=${L.tFecha}
                         items=${VENTANAS.map(([id]) => ({ id, glyph: G_FECHA, label: ventanaLabel(id, s.lang), on: ventana === id }))}
                         onPick=${setVentana} />
            <!-- sin pendientes no hay chip; con ellos, late para que se note -->
            ${!!sinLeer.length && html`
              <div role="button" tabindex="0" onClick=${() => { setFoco(false); elegirVista(V.PEND); }}
                   class="mem-tira ${!enBusqueda && vistaReal === V.PEND ? "on" : "mem-vista-pend"}">
                ${L.memViewNames[V.PEND]}<span style="font-weight:700">${sinLeer.length}</span>
              </div>`}
          </div>
        </div>
      </div>
      <!-- padding-left/right sueltos y NO el atajo "padding": el atajo fijaba
           padding-bottom:0 inline y le ganaba a .mem-pb-mem, así que la lista
           corría por debajo de la tabbar sin despejarla (pedido 2026-08-08). -->
      <div class="mem-pb-mem" style="flex:1;overflow:auto;padding-left:20px;padding-right:20px">
        ${enBusqueda ? html`
          ${q && resultados === null && html`<div style="opacity:.5;font-size:13px">…</div>`}
          ${(!q || resultados !== null) && !resultadosVis.length && !inboxHits.length && !sesionHits.length && html`
            <div style="opacity:.5;font-size:13px">${L.tNoRes}</div>`}
          ${!!resultadosVis.length && html`
            <div style="${TITULO_SEC};margin-bottom:8px">${L.tMemories} · ${resultadosVis.length}</div>
            ${fichas(resultadosVis)}`}
          ${!!inboxHits.length && html`
            <div style="${TITULO_SEC};margin:14px 0 8px">${L.tInbox} · ${inboxHits.length}</div>
            ${inboxHits.map((it) => html`<${FilaInbox} key=${it.id} it=${it} L=${L} lang=${s.lang} onOpen=${() => go("inbox", it.id)} />`)}`}
          ${!!sesionHits.length && html`
            <div style="${TITULO_SEC};margin:14px 0 8px">${L.tSessionsWord} · ${sesionHits.length}</div>
            ${sesionHits.map((x) => html`<${FilaSesion} key=${x.id} ses=${x} lang=${s.lang} privada=${sesPriv(x)}
                                                         onOpen=${() => go("chat", x.id)} />`)}`}
        ` : html`
          ${vistaReal === V.REC && html`
            <!-- la ventana de fecha ahora es un chip de la fila de arriba: acá
                 solo queda el contador de lo que esa ventana deja pasar -->
            <div style="${TITULO_SEC};margin-bottom:8px">${L.tMemories} · ${recientes.length}</div>
            ${todas === null && html`<div style="opacity:.5;font-size:13px">…</div>`}
            ${todas && !recientes.length && html`<div style="opacity:.5;font-size:13px">${L.tNoRes}</div>`}
            ${fichas(recientes)}`}

          ${vistaReal === V.TEMAS && (subjectClic ? filtradas : html`
            ${arbol === null && html`<div style="opacity:.5;font-size:13px">…</div>`}
            ${(arbol || []).filter((g) => sumaNodo(g) > 0).map((g, gi) => {
              const dot = COLORES[gi % COLORES.length];
              const open = abierto === g.nombre;
              const hijosConEntradas = g.hijos.filter((h) => sumaNodo(h) > 0);
              return html`
                <div style="margin-bottom:10px">
                  <div role="button" tabindex="0" onClick=${() => setAbierto(open ? null : g.nombre)}
                       style="display:flex;align-items:center;gap:11px;padding:12px 13px;border-radius:var(--radius-md);cursor:pointer;background:${open ? `color-mix(in srgb, ${dot} ${s.theme === "dark" ? 18 : 10}%, var(--color-surface))` : "var(--color-surface)"};border:1px solid var(--color-divider);box-shadow:var(--shadow-sm)">
                    <span style="width:9px;height:9px;background:${dot};flex-shrink:0"></span>
                    <span style="flex:1;font-size:15px;font-weight:700;letter-spacing:.01em">${g.nombre}</span>
                    <span style="font-family:var(--font-mono);font-size:10.5px;opacity:.55">${sumaNodo(g)}</span>
                    <span style="font-size:13px;opacity:.5;transform:rotate(${open ? 90 : 0}deg);transition:transform .3s">›</span>
                  </div>
                  ${open && html`
                    <div style="margin-top:5px;padding:7px 8px 7px 16px;border-radius:var(--radius-md);background:var(--color-bg);border:1px solid var(--color-divider);display:flex;flex-direction:column;gap:2px">
                      ${hijosConEntradas.map((h) => html`
                        <div role="button" tabindex="0" onClick=${() => setSubjectClic(`${g.nombre}/${h.nombre}`)}
                             style="display:flex;align-items:center;gap:9px;padding:10px 12px;border-left:2px solid var(--color-divider);cursor:pointer">
                          <span style="flex:1;min-width:0">
                            <span style="display:block;font-size:14.5px">${h.nombre}</span>
                          </span>
                          <span style="font-family:var(--font-mono);font-size:10.5px;opacity:.5">${sumaNodo(h)}</span>
                        </div>`)}
                      ${!hijosConEntradas.length && html`<div style="padding:10px 12px;font-size:12.5px;opacity:.5">—</div>`}
                    </div>`}
                </div>`;
            })}`)}

          ${vistaReal === V.TIEMPO && html`
            ${todas === null && html`<div style="opacity:.5;font-size:13px">…</div>`}
            <${ChipsModo} opciones=${s.lang === "en" ? ["Blocks", "Timeline"] : ["Bloques", "Línea"]}
                          valor=${tiempoModo} onPick=${setTiempoModo} />
            ${tiempoModo === 1 ? html`
              <${TimelineGlobal} items=${visibles} lang=${s.lang} />
            ` : bloques.map((b) => html`
              <div style="${TITULO_SEC};margin:4px 0 8px;display:flex;align-items:center;gap:8px">
                <span>${b.etiq}</span>
                <span style="flex:1;height:1px;background:var(--color-divider)"></span>
                <span style="opacity:.7">${b.lista.length}</span>
              </div>
              ${fichas(b.lista)}`)}`}

          ${vistaReal === V.LUGARES && (lugarClic ? html`
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
              <div role="button" tabindex="0" onClick=${() => setLugarClic(null)} style="font-family:var(--font-mono);font-size:11px;cursor:pointer;opacity:.6">‹ ${s.lang === "en" ? "back" : "volver"}</div>
              <span style="font-size:12px;opacity:.7">${lugarClic} · ${porLugar.length}</span>
            </div>
            ${!porLugar.length && html`<div style="opacity:.5;font-size:13px">${L.tNoRes}</div>`}
            ${fichas(porLugar)}
          ` : html`
            ${todas === null && html`<div style="opacity:.5;font-size:13px">…</div>`}
            ${todas && !lugares.length && !capturas.length && html`<div style="opacity:.5;font-size:13px">${L.tNoPlaces}</div>`}
            ${!!(lugares.length || capturas.length) && html`
              <${ChipsModo} opciones=${s.lang === "en" ? ["List", "Map"] : ["Lista", "Mapa"]}
                            valor=${lugarModo} onPick=${setLugarModo} />`}
            ${lugarModo === 1 ? html`
              ${geo === null && html`<div style="opacity:.5;font-size:13px">…</div>`}
              <!-- capa de capturas: se prende y se apaga, como la capa bitemporal
                   de la línea temporal. Solo aparece el switch si hay algo que mostrar -->
              ${!!capturas.length && html`
                <div role="button" tabindex="0" onClick=${() => setCapaCaptura(!capaCaptura)}
                     style="display:inline-flex;align-items:center;gap:8px;margin-bottom:10px;height:32px;padding:0 12px;cursor:pointer;background:var(--color-surface);border:1px solid var(--color-divider);opacity:${capaCaptura ? 1 : 0.5}">
                  <span style="color:var(--color-accent-2);font-size:13px">◇</span>
                  <span style="font-size:12.5px">${L.tCaptureLayer}</span>
                  <span style="font-family:var(--font-mono);font-size:10.5px;opacity:.55">${capturas.length}</span>
                </div>`}
              ${!!(geoVisibles.length || (capaCaptura && capturas.length)) && html`
                <${GeoMap} lugares=${geoVisibles} capturas=${capaCaptura ? capturas : []} />`}
              ${geo && !geoVisibles.length && !capturas.length && html`
                <div style="opacity:.5;font-size:13px">${s.lang === "en" ? "No geocoded places yet." : "Todavía no hay lugares geocodificados."}</div>`}
              ${!!(geo?.sin_geo || []).length && html`
                <div style="${TITULO_SEC};margin:14px 0 8px">${s.lang === "en" ? "No coordinates" : "Sin coordenadas"}</div>
                <div style="display:flex;flex-wrap:wrap;gap:8px">
                  ${geo.sin_geo.map((lugar) => html`
                    <div role="button" tabindex="0" onClick=${() => { setLugarModo(0); setLugarClic(lugar); }}
                         style="height:34px;padding:0 13px;border-radius:var(--radius-md);display:flex;align-items:center;gap:7px;cursor:pointer;background:var(--color-surface);border:1px dashed var(--color-divider)">
                      <span style="font-size:12.5px;opacity:.8">◌ ${lugar}</span>
                    </div>`)}
                </div>`}
            ` : html`
            <div style="display:flex;flex-wrap:wrap;gap:8px">
              ${lugares.map(([lugar, n]) => html`
                <div role="button" tabindex="0" onClick=${() => setLugarClic(lugar)}
                     style="height:38px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;gap:8px;cursor:pointer;background:var(--color-surface);border:1px solid var(--color-divider);box-shadow:var(--shadow-sm)">
                  <span style="font-size:13.5px">◍ ${lugar}</span>
                  <span style="font-family:var(--font-mono);font-size:10.5px;opacity:.55">${n}</span>
                </div>`)}
            </div>`}`)}

          ${vistaReal === V.GRAFO && html`<${GraphView} lang=${s.lang} ocultas=${ocultas} />`}

          ${vistaReal === V.MEDIA && html`
            <${MediaTab} lang=${s.lang} sesionActiva=${s.sesionActiva} proyecto=${proyecto}
                         elegidos=${elegidos} L=${L} onToggle=${alternarSeleccion} oculta=${ocultarMem} />`}

          ${vistaReal === V.PEND && html`
            ${!!sinLeer.length && html`
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
                <span style="${TITULO_SEC}">${sinLeer.length} · ${L.tUnprocessed}</span>
                <div role="button" tabindex="0" onClick=${() => reprocesar(null)}
                     class=${reproc ? "mem-btn-procesando" : "mem-btn-accent"}
                     style="height:32px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:12.5px;font-weight:600;cursor:${reproc ? "default" : "pointer"}">
                  ${reproc === "todas" ? "…" : `⟳ ${L.tReprocessAll}`}</div>
              </div>
              <div style="font-size:12.5px;opacity:.6;margin-bottom:12px">${L.tPendingHint}</div>
              <div class="mem-fichas">
                ${sinLeer.map((r) => html`
                  <${Ficha} key=${r.slug} r=${r} L=${L} lang=${s.lang} onOpen=${() => setPreview(r.slug)}
                            onReprocesar=${() => reprocesar([r.slug])} reprocesando=${reproc === r.slug || reproc === "todas"} />`)}
              </div>`}`}

          ${!!lintCount && !subjectClic && !lugarClic && html`
            <div role="button" tabindex="0" onClick=${() => go("lint")}
                 style="margin-top:14px;display:flex;gap:10px;align-items:center;padding:13px 14px;border-radius:var(--radius-md);border:1px dashed color-mix(in srgb,var(--color-accent) 50%,transparent);background:var(--color-bg);cursor:pointer">
              <span style="font-family:var(--font-mono);font-size:14px;color:var(--color-accent-700)">${lintCount}</span>
              <span style="flex:1;font-size:13.5px">${L.lintLine}</span>
              <span style="opacity:.4">›</span>
            </div>`}
        `}
      </div>
      <${BandejaSeleccion} seleccion=${seleccion} sesionActiva=${s.sesionActiva} proyecto=${s.proyecto} L=${L}
                           onLimpiar=${() => setSeleccion([])} onEnviado=${onClose} onInsertar=${onInsertar} />
      ${preview && !previewBloqueado && html`<${Vistazo} slug=${preview} lang=${s.lang} onClose=${() => setPreview(null)} />`}
    </div>`;
}
