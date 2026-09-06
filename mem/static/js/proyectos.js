// Proyectos: el editor que los maneja (crear/renombrar/privado/unir/borrar).
//
// Reemplaza al SelectorProyecto (un dropdown que solo elegía, escondido en la
// cabecera del chatbox) y a la gestión que vivía dispersa en el panel de
// búsqueda de Home, con un window.prompt para renombrar (pedido 2026-08-12).
// El chip por pantalla que lo abría (ProyectoActivo) se fue con el sidebar y
// la tabbar (pedido 2026-08-31): ahora son ellos quienes abren EditorProyectos
// directo. Vive fuera de ui.js porque ui.js ya pasó las 1700 líneas y esto es
// una pantalla entera, no una primitiva compartida.
import { html, useState, useEffect } from "../vendor/preact-htm.js";
import { patch, post, del } from "./api.js";
import { dict } from "./i18n.js";
import { GENERAL } from "./state.js";

/** General es fijo: no se borra, no se renombra, no se une ni se hace privado
 *  (el server lo rechaza igual; acá es para no ofrecer lo que va a fallar). */
const esFijo = (n) => String(n || "").toLowerCase() === GENERAL.toLowerCase();
import { usePrivado, AvisoNube } from "./privado.js";
import { Sheet, ChipMenu, useProyectos, cargarProyectos, useAgentes,
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

/** Picker de destino que respeta el candado (pedido 2026-09-06): sacando cosas
 *  de un proyecto PRIVADO solo se ofrecen otros privados, porque mover a uno
 *  público es publicar — en silencio y en bloque. Para abrirlo a los públicos
 *  hay un botón y, detrás, un popup que dice qué significa. Desde un proyecto
 *  público no hay nada que cuidar y se comporta como siempre.
 *
 *  `onPick` recibe el nombre; el llamador se queda con el estado del destino.
 *  Devuelve también la lista efectiva por `onDestinos` para que el llamador sepa
 *  si hay a dónde mover (sin eso, "borrar" sería la única salida sin decirlo). */
function DestinoProyecto({ privado, otros, destino, onPick, lang, etiqueta }) {
  const L = dict(lang);
  const [abiertos, setAbiertos] = useState(false);   // ya aceptó publicar
  const [pidiendo, setPidiendo] = useState(false);   // popup de confirmación a la vista
  const acota = privado && !abiertos;
  const opciones = acota ? otros.filter((x) => x.privado) : otros;
  // el destino guardado puede no estar en la lista efectiva (recién se acotó, o
  // el llamador arrancó en otro): se deriva en vez de sincronizarse con un effect.
  const valor = opciones.some((x) => x.nombre === destino) ? destino : (opciones[0]?.nombre || "");
  useEffect(() => { if (valor !== destino) onPick(valor); }, [valor]);
  return html`
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      ${etiqueta && html`<span style="color:var(--text-2)">${etiqueta}</span>`}
      ${opciones.length
        ? html`<${ChipMenu} etiqueta=${valor} ancho=${230}
                            clase=${(opciones.find((x) => x.nombre === valor) || {}).privado ? "mem-privada" : ""}
                            items=${itemsDeProyectos(opciones, valor, [], lang)} onPick=${onPick} />`
        : html`<span style="font-size:12.5px;color:var(--text-3)">${L.tPrivNoDest}</span>`}
      ${acota && html`
        <span role="button" tabindex="0" class="mem-tog peligro" onClick=${() => setPidiendo(true)}>${L.tPrivAllow}</span>
        <span style="flex-basis:100%;font-family:var(--font-mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--text-3)">${L.tPrivDestOnly}</span>`}
      ${pidiendo && html`
        <${Sheet} onClose=${() => setPidiendo(false)} ancho=${560}>
          <div style="padding:8px 22px 26px">
            <h3 style="margin:0 0 10px;font-family:var(--font-heading);font-size:22px">${L.tPrivAllowQ}</h3>
            <p style="margin:0 0 18px;font-size:14px;line-height:1.6;color:var(--text-2)">${L.tPrivAllowBody}</p>
            <div role="button" tabindex="0" class="mem-btn-danger"
                 style="height:48px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-family:var(--font-heading);font-size:15px;cursor:pointer;margin-bottom:10px"
                 onClick=${() => { setAbiertos(true); setPidiendo(false); }}>${L.tPrivAllowGo}</div>
            <div role="button" tabindex="0" onClick=${() => setPidiendo(false)}
                 style="height:46px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:14.5px;cursor:pointer;opacity:.7">${L.tCancel}</div>
          </div>
        <//>`}
    </div>`;
}

/** Editor: elegir el activo, renombrar, togglear privado, unir y borrar. Todo
 *  en un Sheet — el back de hardware lo cierra solo (pilaSheets en ui.js).
 *  Cuando la mutación toca al proyecto activo, el sync lo hace ACÁ (sabe
 *  viejo→nuevo) y no cada pantalla que monta el chip. */
export function EditorProyectos({ valor, onPick, lang, onClose, abrirEnNuevo = false, editar = "" }) {
  const L = dict(lang);
  const lista = useProyectosVisibles();
  const enUso = useAgentes();                     // para el aviso de nube al hacer algo privado
  const [abierta, setAbierta] = useState(editar); // proyecto con sus acciones desplegadas (el ✎ abre con el activo ya desplegado)
  const [modo, setModo] = useState("");           // "" | renombrar | privado | unir | borrar | clave
  const [nombre, setNombre] = useState(editar);   // el input de renombrar arranca con el nombre de ese proyecto
  const [destino, setDestino] = useState("");
  const [privadoNuevo, setPrivadoNuevo] = useState(false);
  const [claveNueva, setClaveNueva] = useState("");
  const [creando, setCreando] = useState(abrirEnNuevo);  // el ＋ del sidebar abre ya en "nuevo"
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
    <${Sheet} onClose=${onClose} ancho=${640}>
      <div style="padding:8px 18px 26px;overflow:auto">
        <h3 style="margin:0 0 10px;font-family:var(--font-heading);font-size:23px">${L.tProjects}</h3>

        ${lista.map((p) => html`
          <div key=${p.nombre} style="border-top:1px solid var(--color-divider)">
            <div style="display:flex;align-items:center;gap:2px">
              ${fila(html`
                <span style="width:13px;flex-shrink:0;text-align:center">${p.privado ? html`<span class="mem-privada">⚿</span>` : "◈"}</span>
                <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${p.nombre === valor ? "color:var(--color-accent-700);font-weight:700" : ""}">${p.nombre}</span>
                ${p.privado && html`<span style="opacity:.5;flex-shrink:0;font-size:11px">${L.tPrivado}</span>`}`,
                { onClick: () => elegir(p.nombre), style: "min-height:46px;flex:1;min-width:0" })}
              <!-- General no tiene ⋯: las cuatro acciones que hay detrás (renombrar,
                   privado, unir, borrar) están prohibidas para él, así que un menú
                   vacío sería peor que ninguno. En su lugar, por qué (pedido 2026-09-06). -->
              ${esFijo(p.nombre) ? html`
                <span title=${L.tProjFixedWhy}
                      style="width:34px;height:44px;display:flex;align-items:center;justify-content:center;font-family:var(--font-mono);font-size:8.5px;letter-spacing:.06em;text-transform:uppercase;opacity:.45">${L.tProjFixed}</span>`
                : html`
                <span role="button" tabindex="0" class="mem-hit" title=${L.tEdit}
                      onClick=${() => (abierta === p.nombre ? cerrarAcciones() : (cerrarAcciones(), setAbierta(p.nombre), setNombre(p.nombre)))}
                      style="width:34px;height:44px;display:flex;align-items:center;justify-content:center;cursor:pointer;opacity:${abierta === p.nombre ? 1 : 0.55};font-size:16px">⋯</span>`}
            </div>

            <!-- también !esFijo y no solo el nombre abierto: el ✎ del sidebar
                 abre por nombre, y sin esto General llegaba con sus acciones
                 desplegadas aunque su fila no tenga ⋯. -->
            ${abierta === p.nombre && !esFijo(p.nombre) && html`
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
                    ${!p.privado && html`<${AvisoNube} lang=${lang} agentes=${enUso} />`}
                    <div style="display:flex;gap:8px">
                      <span role="button" tabindex="0" onClick=${() => togglearPrivado(p)} class="mem-btn-accent"
                            style="height:34px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;cursor:pointer;font-size:13px">${L.tConfirm}</span>
                      <span role="button" tabindex="0" onClick=${cerrarAcciones}
                            style="height:34px;padding:0 14px;display:flex;align-items:center;cursor:pointer;font-size:13px;opacity:.7">${L.tCancel}</span>
                    </div>
                  </div>`
                  : modo === "unir" ? html`
                  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                    <${DestinoProyecto} privado=${p.privado} lang=${lang} etiqueta="→"
                                        otros=${lista.filter((x) => x.nombre !== p.nombre)}
                                        destino=${destino} onPick=${setDestino} />
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
            ${privadoNuevo && html`<${AvisoNube} lang=${lang} agentes=${enUso} />`}
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
          privado=${!!(lista.find((x) => x.nombre === abierta) || {}).privado}
          onClose=${() => setModo("")}
          onBorrar=${(qs, dest) => mutar(() => del(`/projects/${encodeURIComponent(abierta)}?${qs}`),
                                         valor === abierta ? dest : undefined)} />`}
    <//>`;
}

/** Borrar un proyecto: qué pasa con sus memorias y con sus sesiones. Dos
 *  opciones excluyentes por clase —mover a otro proyecto o borrar— y no una
 *  casilla "borrar" a secas (pedido 2026-09-06): con la casilla, no tildar nada
 *  era una respuesta implícita, y "a dónde va lo que sobrevive" no se leía como
 *  parte de la misma pregunta. Borrar manda a la papelera, no destruye. */
function ConfirmarBorradoProyecto({ nombre, otros, privado, lang, onClose, onBorrar }) {
  const L = dict(lang);
  // sin NINGÚN otro proyecto no hay a dónde mover: la única salida es la
  // papelera. Que desde un privado solo se ofrezcan privados lo resuelve
  // DestinoProyecto, no acá: si no hay otro privado, la salida no es forzar el
  // borrado sino su botón de "permitir públicos".
  const soloUno = !otros.length;
  const posibles = privado ? otros.filter((x) => x.privado) : otros;
  const [borrarSes, setBorrarSes] = useState(soloUno);
  const [borrarMem, setBorrarMem] = useState(soloUno);
  // ojo con arrancar en GENERAL: borrando General mismo no está en `otros` y el
  // server rechaza el destino. El primero de la lista siempre es válido.
  const [destino, setDestino] = useState(() => (posibles.some((x) => x.nombre === GENERAL) ? GENERAL : posibles[0]?.nombre || ""));
  const [borrando, setBorrando] = useState(false);
  const conserva = !borrarSes || !borrarMem;   // ¿queda algo que reubicar?

  /** Un par de botones excluyentes: mover | borrar. */
  const eleccion = (que, borra, set) => html`
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:9px">
      <span style="flex:1;min-width:90px;font-size:13.5px;font-weight:600">${que}</span>
      <span role="radio" tabindex="0" aria-checked=${!borra} class="mem-tog ${!borra ? "on" : ""}"
            style=${soloUno ? "opacity:.4;pointer-events:none" : ""}
            onClick=${() => !soloUno && set(false)}>⇢ ${L.tDelProjKeep}</span>
      <span role="radio" tabindex="0" aria-checked=${borra} class="mem-tog peligro ${borra ? "on" : ""}"
            onClick=${() => set(true)}>✕ ${L.tDelProjDrop}</span>
    </div>`;

  // el mismo resumen que el botón de borrar una sesión: qué se va a la papelera
  // y a dónde cae lo que sobrevive, dicho ANTES de tocar el botón rojo.
  const papelera = [borrarSes && L.tSessionsWord, borrarMem && L.tMemories].filter(Boolean).join(" + ");
  const sub = [papelera && `${L.tToTrash.replace(/^✕\s*/, "")}: ${papelera}`,
               conserva && `${L.tMoveTo} ${destino}`].filter(Boolean).join(" · ");
  return html`
    <${Sheet} onClose=${onClose} ancho=${620}>
      <div style="padding:8px 22px 26px">
        <h3 style="margin:0 0 8px;font-family:var(--font-heading);font-size:24px">${L.tDelProjQ}</h3>
        <div style="font-size:15px;font-weight:600;margin-bottom:8px">${nombre}</div>
        <p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:var(--text-2)">${L.tDelProjBody}</p>
        ${eleccion(L.tMemories, borrarMem, setBorrarMem)}
        ${eleccion(L.tSessionsWord, borrarSes, setBorrarSes)}
        ${soloUno && html`
          <p style="margin:2px 0 14px;font-size:12.5px;color:var(--text-3)">${L.tDelProjOnly}</p>`}
        ${conserva && html`
          <div style="margin:14px 0 18px;font-size:13px">
            <${DestinoProyecto} privado=${privado} otros=${otros} destino=${destino}
                                onPick=${setDestino} lang=${lang} etiqueta=${L.tMoveTo} />
          </div>`}
        <div role="button" tabindex="0" class="mem-btn-danger" style="min-height:52px;padding:8px 16px;border-radius:var(--radius-md);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;font-family:var(--font-heading);font-size:16px;cursor:pointer;margin-bottom:10px;opacity:${borrando || (conserva && !destino) ? 0.45 : 1}"
             onClick=${borrando || (conserva && !destino) ? null : () => {
               setBorrando(true);
               const qs = new URLSearchParams({ sesiones: borrarSes ? "papelera" : "mover",
                                                memorias: borrarMem ? "papelera" : "mover",
                                                destino: conserva ? destino : "" });
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
