// Proyectos: el chip que dice dónde estás parado y el editor que los maneja.
//
// Reemplaza al SelectorProyecto (un dropdown que solo elegía, escondido en la
// cabecera del chatbox) y a la gestión que vivía dispersa en el panel de
// búsqueda de Home, con un window.prompt para renombrar (pedido 2026-08-12).
// Vive fuera de ui.js porque ui.js ya pasó las 1700 líneas y esto es una
// pantalla entera, no una primitiva compartida.
import { html, useState } from "../vendor/preact-htm.js";
import { patch, del } from "./api.js";
import { dict } from "./i18n.js";
import { usePrivado, ambitoDe } from "./privado.js";
import { Sheet, ChipMenu, IconoAmbito, AMBITO_COLOR, useProyectos, cargarProyectos,
         crearProyecto, useEscape } from "./ui.js";

/** Los proyectos que se pueden mostrar: con el candado puesto, los privados no
 *  existen para nadie (ni en el editor, ni en los pickers de unir/destino). */
export function useProyectosVisibles() {
  const { oculto } = usePrivado();
  const todos = useProyectos();
  return oculto ? todos.filter((p) => p.ambito !== "privado") : todos;
}

/** Items de ChipMenu para los filtros de búsqueda — Home, Memory y Media arman
 *  exactamente los mismos. `extras` van primero (Todos, Sin proyecto). */
export function itemsDeProyectos(lista, valor, extras = [], lang = "es") {
  const L = dict(lang);
  return [...extras.map((e) => ({ glyph: "◈", ...e, on: e.id === valor })),
          ...lista.map((p) => ({
            id: p.nombre, label: p.nombre, sub: L.ambitos[p.ambito] || p.ambito,
            glyph: html`<span style="color:${AMBITO_COLOR[p.ambito] || AMBITO_COLOR.personal}"><${IconoAmbito} ambito=${p.ambito} /></span>`,
            on: p.nombre === valor }))];
}

/** El proyecto activo, siempre en el mismo lugar de las tres pantallas (Home,
 *  Memory y la sesión) para que se lea como "estás DENTRO de esto". Un solo
 *  toque abre el editor: elegir otro es lo primero que ofrece. */
export function ProyectoActivo({ valor, onPick, lang, titulo = "", estilo = "" }) {
  const L = dict(lang);
  const todos = useProyectos();
  const { oculto } = usePrivado();
  const [abierto, setAbierto] = useState(false);
  const amb = ambitoDe(todos, valor);
  // con el candado puesto ni el NOMBRE de un proyecto privado se muestra (el
  // chip viejo filtraba la lista del menú pero igual lo escribía acá)
  const tapado = oculto && amb === "privado";
  return html`
    <span style="display:inline-flex;flex-shrink:0;${estilo}">
      <span role="button" tabindex="0" class="mem-proy-chip" aria-haspopup="dialog" aria-expanded=${abierto}
            title=${titulo || L.tProjects} onClick=${() => setAbierto(true)}>
        ${valor
          ? html`<span style="color:${AMBITO_COLOR[amb] || AMBITO_COLOR.personal}"><${IconoAmbito} ambito=${amb} /></span>`
          : "◈"}
        <span style="display:inline-block;max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom">${tapado ? "⚿" : (valor || L.tNoProject)}</span> ✎</span>
      ${abierto && html`
        <${EditorProyectos} valor=${valor} lang=${lang} onPick=${onPick} onClose=${() => setAbierto(false)} />`}
    </span>`;
}

/** Editor: elegir el activo, renombrar, cambiar ámbito, unir y borrar. Todo en
 *  un Sheet — el back de hardware lo cierra solo (pilaSheets en ui.js).
 *  Cuando la mutación toca al proyecto activo, el sync lo hace ACÁ (sabe
 *  viejo→nuevo) y no cada pantalla que monta el chip. */
export function EditorProyectos({ valor, onPick, lang, onClose }) {
  const L = dict(lang);
  const lista = useProyectosVisibles();
  const [abierta, setAbierta] = useState("");     // proyecto con sus acciones desplegadas
  const [modo, setModo] = useState("");           // "" | renombrar | unir | borrar
  const [nombre, setNombre] = useState("");
  const [destino, setDestino] = useState("");
  const [ambito, setAmbito] = useState("personal");
  const [creando, setCreando] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");
  useEscape(true, onClose);

  const elegir = (n) => { onPick(n); onClose(); };
  const cerrarAcciones = () => { setAbierta(""); setModo(""); setNombre(""); setDestino(""); setError(""); };

  async function mutar(fn, nuevoActivo) {
    if (ocupado) return;
    setOcupado(true);
    setError("");
    try {
      await fn();
      await cargarProyectos(true);
      if (nuevoActivo !== undefined) onPick(nuevoActivo);
      cerrarAcciones();
    } catch (e) {
      setError(String(e?.message || e));
    }
    setOcupado(false);
  }

  const renombrar = (p) => {
    const n = nombre.trim();
    if (!n || n === p.nombre) return cerrarAcciones();
    mutar(() => patch(`/projects/${encodeURIComponent(p.nombre)}`, { nombre: n }),
          valor === p.nombre ? n : undefined);
  };
  const unir = (p) => mutar(
    () => patch(`/projects/${encodeURIComponent(p.nombre)}`, { nombre: destino, fusionar: true }),
    valor === p.nombre ? destino : undefined);
  const crear = () => {
    const n = nombre.trim();
    if (!n) return;
    mutar(async () => { await crearProyecto(n, ambito); }, n);
  };

  const fila = (contenido, props = {}) => html`
    <div role="button" tabindex="0" class="mem-proy-item" style="min-height:46px" ...${props}>${contenido}</div>`;

  return html`
    <${Sheet} onClose=${onClose}>
      <div style="padding:8px 18px 26px;overflow:auto">
        <h3 style="margin:0 0 10px;font-family:var(--font-heading);font-size:23px">${L.tProjects}</h3>

        <!-- "Sin proyecto" ES un proyecto: se elige como cualquier otro, pero no
             se renombra ni se borra, así que no lleva ⋯ -->
        ${fila(html`
          <span style="width:13px;flex-shrink:0;text-align:center">◈</span>
          <span style="flex:1;min-width:0;${valor ? "" : "color:var(--color-accent-700);font-weight:700"}">${L.tNoProject}</span>`,
          { onClick: () => elegir("") })}

        ${lista.map((p) => html`
          <div key=${p.nombre} style="border-top:1px solid var(--color-divider)">
            <div style="display:flex;align-items:center;gap:2px">
              ${fila(html`
                <span style="color:${AMBITO_COLOR[p.ambito] || AMBITO_COLOR.personal}"><${IconoAmbito} ambito=${p.ambito} /></span>
                <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${p.nombre === valor ? "color:var(--color-accent-700);font-weight:700" : ""}">${p.nombre}</span>
                <span style="opacity:.5;flex-shrink:0;font-size:11px">${L.ambitos[p.ambito] || p.ambito}</span>`,
                { onClick: () => elegir(p.nombre), style: "min-height:46px;flex:1;min-width:0" })}
              <span role="button" tabindex="0" class="mem-hit" title=${L.tEdit}
                    onClick=${() => (abierta === p.nombre ? cerrarAcciones() : (cerrarAcciones(), setAbierta(p.nombre), setNombre(p.nombre)))}
                    style="width:34px;height:44px;display:flex;align-items:center;justify-content:center;cursor:pointer;opacity:${abierta === p.nombre ? 1 : 0.55};font-size:16px">⋯</span>
            </div>

            ${abierta === p.nombre && html`
              <div style="padding:2px 0 10px 15px;display:flex;flex-direction:column;gap:8px">
                ${modo === "renombrar" ? html`
                  <div style="display:flex;gap:6px">
                    <input value=${nombre} autofocus class="mem-proy-input" style="flex:1"
                           onInput=${(e) => setNombre(e.target.value)}
                           onKeyDown=${(e) => { if (e.key === "Enter") renombrar(p); if (e.key === "Escape") setModo(""); }} />
                    <span role="button" tabindex="0" onClick=${() => renombrar(p)} class="mem-btn-accent"
                          style="height:40px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;cursor:pointer;font-size:13px">${L.tSave}</span>
                  </div>`
                  : modo === "unir" ? html`
                  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                    <${ChipMenu} etiqueta=${`→ ${destino || "…"}`} ancho=${230}
                                 items=${itemsDeProyectos(lista.filter((x) => x.nombre !== p.nombre), destino, [], lang)}
                                 onPick=${setDestino} />
                    ${destino && html`
                      <span role="button" tabindex="0" onClick=${() => unir(p)} class="mem-btn-accent"
                            style="height:34px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;cursor:pointer;font-size:13px">${L.tConfirm}</span>`}
                    <span style="flex-basis:100%;font-size:12px;line-height:1.5;color:var(--text-2)">${L.tMergeBody}</span>
                  </div>`
                  : html`
                  <!-- acciones: pastillas que abrazan su texto. El ámbito de
                       abajo SÍ es un segmentado (tres estados de una cosa), y
                       si las dos filas se ven iguales no se distingue una de otra. -->
                  <div style="display:flex;gap:6px;flex-wrap:wrap">
                    <span role="button" tabindex="0" class="mem-tog" onClick=${() => setModo("renombrar")}>${L.tRename}</span>
                    <span role="button" tabindex="0" class="mem-tog" onClick=${() => setModo("unir")}>${L.tProjMerge}</span>
                    <span role="button" tabindex="0" class="mem-tog peligro" onClick=${() => setModo("borrar")}>${L.tDelete}</span>
                  </div>
                  <div style="display:flex;gap:5px;flex-wrap:wrap">
                    ${Object.keys(L.ambitos).map((a) => html`
                      <span key=${a} role="button" tabindex="0" class="mem-proy-amb ${p.ambito === a ? "on" : ""}"
                            onClick=${() => mutar(() => crearProyecto(p.nombre, a))}>${L.ambitos[a]}</span>`)}
                  </div>`}
                ${error && html`<div style="font-size:12px;color:var(--color-priv)">${error}</div>`}
              </div>`}
          </div>`)}

        ${!lista.length && html`<div class="mem-proy-item" style="opacity:.5">${L.tNoProjects}</div>`}

        ${creando ? html`
          <div style="border-top:1px solid var(--color-divider);padding:10px 0 0;display:flex;flex-direction:column;gap:8px">
            <input value=${nombre} autofocus placeholder=${L.phProjectName} class="mem-proy-input"
                   onInput=${(e) => setNombre(e.target.value)}
                   onKeyDown=${(e) => { if (e.key === "Enter") crear(); if (e.key === "Escape") setCreando(false); }} />
            <div style="display:flex;gap:5px">
              ${Object.keys(L.ambitos).map((a) => html`
                <span key=${a} role="button" tabindex="0" onClick=${() => setAmbito(a)}
                      class="mem-proy-amb ${ambito === a ? "on" : ""}">${L.ambitos[a]}</span>`)}
            </div>
            <div role="button" tabindex="0" onClick=${crear} class="mem-btn-accent"
                 style="height:40px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:13px;cursor:pointer">${L.tSave}</div>
            ${error && html`<div style="font-size:12px;color:var(--color-priv)">${error}</div>`}
          </div>`
          : fila(html`<span style="color:var(--color-accent-700)">＋ ${L.tNewProject}</span>`,
                 { onClick: () => { cerrarAcciones(); setNombre(""); setCreando(true); },
                   style: "min-height:46px;border-top:1px solid var(--color-divider)" })}
      </div>

      ${modo === "borrar" && abierta && html`
        <${ConfirmarBorradoProyecto} nombre=${abierta} lang=${lang} otros=${lista.filter((x) => x.nombre !== abierta)}
          onClose=${() => setModo("")}
          onBorrar=${(qs, dest) => mutar(() => del(`/projects/${encodeURIComponent(abierta)}?${qs}`),
                                         valor === abierta ? dest : undefined)} />`}
    <//>`;
}

/** Borrar un proyecto: qué se va con él y a dónde va lo que sobrevive. Antes
 *  eran dos botones (solo / todo) sin destino posible. */
function ConfirmarBorradoProyecto({ nombre, otros, lang, onClose, onBorrar }) {
  const L = dict(lang);
  const [borrarSes, setBorrarSes] = useState(false);
  const [borrarMem, setBorrarMem] = useState(false);
  const [destino, setDestino] = useState("");
  const [borrando, setBorrando] = useState(false);
  const conserva = !borrarSes || !borrarMem;   // ¿queda algo que reubicar?
  const casilla = (on, label, toggle) => html`
    <span role="button" tabindex="0" aria-checked=${on} class="mem-tog check ${on ? "on" : ""}"
          onClick=${toggle}>${label}</span>`;
  // el mismo resumen que el botón de borrar una sesión: qué se va a la papelera
  // y a dónde cae lo que sobrevive, dicho ANTES de tocar el botón rojo. Acá van
  // las palabras sueltas y no las etiquetas de las casillas: bajo un botón que
  // dice "Borrar", "Borrar sus sesiones · Borrar sus memorias" es puro eco.
  const papelera = [borrarSes && L.tSessionsWord, borrarMem && L.tMemories].filter(Boolean).join(" + ");
  const sub = [papelera && `${L.tToTrash.replace(/^✕\s*/, "")}: ${papelera}`,
               conserva && `${L.tMoveTo} ${destino || L.tNoProject}`].filter(Boolean).join(" · ");
  return html`
    <${Sheet} onClose=${onClose} maxHeight="70%">
      <div style="padding:8px 22px 26px">
        <h3 style="margin:0 0 8px;font-family:var(--font-heading);font-size:24px">${L.tDelProjQ}</h3>
        <div style="font-size:15px;font-weight:600;margin-bottom:8px">${nombre}</div>
        <p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:var(--text-2)">${L.tDelProjBody}</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
          ${casilla(borrarSes, L.tDelProjSes, () => setBorrarSes(!borrarSes))}
          ${casilla(borrarMem, L.tDelProjMem, () => setBorrarMem(!borrarMem))}
        </div>
        ${conserva && html`
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:18px;font-size:13px">
            <span style="color:var(--text-2)">${L.tMoveTo}</span>
            <${ChipMenu} etiqueta=${destino || L.tNoProject} ancho=${230}
                         items=${itemsDeProyectos(otros, destino, [{ id: "", label: L.tNoProject }], lang)}
                         onPick=${setDestino} />
          </div>`}
        <div role="button" tabindex="0" class="mem-btn-danger" style="min-height:52px;padding:8px 16px;border-radius:var(--radius-md);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;font-family:var(--font-heading);font-size:16px;cursor:pointer;margin-bottom:10px;opacity:${borrando ? 0.6 : 1}"
             onClick=${borrando ? null : () => {
               setBorrando(true);
               const qs = new URLSearchParams({ sesiones: borrarSes ? "papelera" : "mover",
                                                memorias: borrarMem ? "papelera" : "mover", destino });
               onBorrar(qs.toString(), destino);
             }}>
          ${L.tDelete}
          <span style="font-family:var(--font-mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;opacity:.75">${sub}</span>
        </div>
        <div role="button" tabindex="0" onClick=${onClose}
             style="height:48px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:14.5px;cursor:pointer;opacity:.7">${L.tCancel}</div>
      </div>
    <//>`;
}
