// Proyectos: el editor que los maneja (crear/renombrar/privado/unir/borrar).
//
// Reemplaza al SelectorProyecto (un dropdown que solo elegía, escondido en la
// cabecera del chatbox) y a la gestión que vivía dispersa en el panel de
// búsqueda de Home, con un window.prompt para renombrar (pedido 2026-08-12).
// El chip por pantalla que lo abría (ProyectoActivo) se fue con el sidebar y
// la tabbar (pedido 2026-08-31): ahora son ellos quienes abren EditorProyectos
// directo. Vive fuera de ui.js porque ui.js ya pasó las 1700 líneas y esto es
// una pantalla entera, no una primitiva compartida.
import { html, useState } from "../vendor/preact-htm.js";
import { patch, post, del } from "./api.js";
import { dict } from "./i18n.js";
import { usePrivado } from "./privado.js";
import { Sheet, ChipMenu, useProyectos, cargarProyectos,
         crearProyecto, useEscape, itemsDeProyectos } from "./ui.js";

// El sidebar y las tres pantallas la piden con este import: vive en ui.js
// (junto a ChipMenu, de la que depende) y se reexporta acá porque
// EditorProyectos también la usa (picker de "unir").
export { itemsDeProyectos };

/** Los proyectos que se pueden mostrar: con el candado puesto, los privados no
 *  existen para nadie (ni en el editor, ni en los pickers de unir/destino). */
export function useProyectosVisibles() {
  const { oculto } = usePrivado();
  const todos = useProyectos();
  return oculto ? todos.filter((p) => !p.privado) : todos;
}

/** Editor: elegir el activo, renombrar, togglear privado, unir y borrar. Todo
 *  en un Sheet — el back de hardware lo cierra solo (pilaSheets en ui.js).
 *  Cuando la mutación toca al proyecto activo, el sync lo hace ACÁ (sabe
 *  viejo→nuevo) y no cada pantalla que monta el chip. */
export function EditorProyectos({ valor, onPick, lang, onClose }) {
  const L = dict(lang);
  const lista = useProyectosVisibles();
  const [abierta, setAbierta] = useState("");     // proyecto con sus acciones desplegadas
  const [modo, setModo] = useState("");           // "" | renombrar | privado | unir | borrar | clave
  const [nombre, setNombre] = useState("");
  const [destino, setDestino] = useState("");
  const [privadoNuevo, setPrivadoNuevo] = useState(false);
  const [claveNueva, setClaveNueva] = useState("");
  const [creando, setCreando] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");
  useEscape(true, onClose);

  const elegir = (n) => { onPick(n); onClose(); };
  const cerrarAcciones = () => { setAbierta(""); setModo(""); setNombre(""); setDestino(""); setClaveNueva(""); setError(""); };

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
  const togglearPrivado = (p) => mutar(
    () => patch(`/projects/${encodeURIComponent(p.nombre)}`, { privado: !p.privado }));
  const crear = () => {
    const n = nombre.trim();
    if (!n) return;
    mutar(async () => { await crearProyecto(n, privadoNuevo); }, n);
  };
  async function generarClave(p) {
    if (ocupado) return;
    setOcupado(true);
    setError("");
    try { setClaveNueva((await post(`/projects/${encodeURIComponent(p.nombre)}/clave`, {})).clave); }
    catch (e) { setError(String(e?.message || e)); }
    setOcupado(false);
  }

  const fila = (contenido, props = {}) => html`
    <div role="button" tabindex="0" class="mem-proy-item" style="min-height:46px" ...${props}>${contenido}</div>`;

  return html`
    <${Sheet} onClose=${onClose}>
      <div style="padding:8px 18px 26px;overflow:auto">
        <h3 style="margin:0 0 10px;font-family:var(--font-heading);font-size:23px">${L.tProjects}</h3>

        <!-- "Todo" — la Biblioteca compartida, siempre pública — se elige como
             cualquier otro, pero no se renombra ni se borra, así que no lleva ⋯ -->
        ${fila(html`
          <span style="width:13px;flex-shrink:0;text-align:center">◈</span>
          <span style="flex:1;min-width:0;${valor ? "" : "color:var(--color-accent-700);font-weight:700"}">${L.tProjAll}</span>`,
          { onClick: () => elegir("") })}

        ${lista.map((p) => html`
          <div key=${p.nombre} style="border-top:1px solid var(--color-divider)">
            <div style="display:flex;align-items:center;gap:2px">
              ${fila(html`
                <span style="width:13px;flex-shrink:0;text-align:center">${p.privado ? html`<span class="mem-privada">⚿</span>` : "◈"}</span>
                <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${p.nombre === valor ? "color:var(--color-accent-700);font-weight:700" : ""}">${p.nombre}</span>
                ${p.privado && html`<span style="opacity:.5;flex-shrink:0;font-size:11px">${L.tPrivado}</span>`}`,
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
                  : modo === "privado" ? html`
                  <div style="display:flex;flex-direction:column;gap:8px">
                    <span style="font-size:13px;line-height:1.5;color:var(--text-2)">${L.tPrivToggleQ}</span>
                    <div style="display:flex;gap:8px">
                      <span role="button" tabindex="0" onClick=${() => togglearPrivado(p)} class="mem-btn-accent"
                            style="height:34px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;cursor:pointer;font-size:13px">${L.tConfirm}</span>
                      <span role="button" tabindex="0" onClick=${cerrarAcciones}
                            style="height:34px;padding:0 14px;display:flex;align-items:center;cursor:pointer;font-size:13px;opacity:.7">${L.tCancel}</span>
                    </div>
                  </div>`
                  : modo === "unir" ? html`
                  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                    <${ChipMenu} etiqueta=${`→ ${destino || "…"}`} ancho=${230}
                                 items=${itemsDeProyectos(lista.filter((x) => x.nombre !== p.nombre), destino, [], lang)}
                                 onPick=${setDestino} />
                    ${destino && html`
                      <span role="button" tabindex="0" onClick=${() => unir(p)} class="mem-btn-accent"
                            style="height:34px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;cursor:pointer;font-size:13px">${L.tConfirm}</span>`}
                    <span style="flex-basis:100%;font-size:12px;line-height:1.5;color:var(--text-2)">${L.tMergeBody}
                      ${destino && ` ${L.tMergePriv} ${destino}: ${(lista.find((x) => x.nombre === destino) || {}).privado ? L.tPrivado : L.tPublico}.`}</span>
                  </div>`
                  : modo === "clave" ? html`
                  <div style="display:flex;flex-direction:column;gap:8px">
                    ${claveNueva ? html`
                      <span style="font-size:12px;line-height:1.5;color:var(--text-2)">${L.tProjKeyBody}</span>
                      <code style="font-size:12.5px;padding:8px;border-radius:var(--radius-md);background:var(--color-surface);word-break:break-all;user-select:all">${claveNueva}</code>`
                      : html`<span role="button" tabindex="0" onClick=${() => generarClave(p)} class="mem-btn-accent"
                                  style="height:34px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:13px">${L.tProjKey}</span>`}
                  </div>`
                  : html`
                  <!-- acciones: pastillas que abrazan su texto. -->
                  <div style="display:flex;gap:6px;flex-wrap:wrap">
                    <span role="button" tabindex="0" class="mem-tog" onClick=${() => setModo("renombrar")}>${L.tRename}</span>
                    <span role="button" tabindex="0" class="mem-tog ${p.privado ? "on" : ""}" onClick=${() => setModo("privado")}>⚿ ${L.tPrivado}</span>
                    <span role="button" tabindex="0" class="mem-tog" onClick=${() => setModo("unir")}>${L.tProjMerge}</span>
                    ${p.privado && html`<span role="button" tabindex="0" class="mem-tog" onClick=${() => setModo("clave")}>${L.tProjKey}</span>`}
                    <span role="button" tabindex="0" class="mem-tog peligro" onClick=${() => setModo("borrar")}>${L.tDelete}</span>
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
            <span role="button" tabindex="0" aria-checked=${privadoNuevo} class="mem-tog check ${privadoNuevo ? "on" : ""}"
                  style="align-self:flex-start" onClick=${() => setPrivadoNuevo((v) => !v)}>⚿ ${L.tPrivado}</span>
            <div role="button" tabindex="0" onClick=${crear} class="mem-btn-accent"
                 style="height:40px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:13px;cursor:pointer">${L.tSave}</div>
            ${error && html`<div style="font-size:12px;color:var(--color-priv)">${error}</div>`}
          </div>`
          : fila(html`<span style="color:var(--color-accent-700)">＋ ${L.tNewProject}</span>`,
                 { onClick: () => { cerrarAcciones(); setNombre(""); setPrivadoNuevo(false); setCreando(true); },
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
               conserva && `${L.tMoveTo} ${destino || L.tProjAll}`].filter(Boolean).join(" · ");
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
            <${ChipMenu} etiqueta=${destino || L.tToAllPublic} ancho=${230}
                         items=${itemsDeProyectos(otros, destino, [{ id: "", label: L.tToAllPublic }], lang)}
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
