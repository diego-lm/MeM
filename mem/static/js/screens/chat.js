// SESIÓN — spec §5.2: 5 vistas sobre los mismos datos (historial + memorias
// vinculadas). Cambiar de vista no pierde nada — solo cambia `modo` (PATCH).
import { html, useState, useEffect, useLayoutEffect, useMemo, useRef } from "../../vendor/preact-htm.js";
import { useStore, getState, setState, back, go } from "../state.js";
import { dict, MODE_FALLBACK } from "../i18n.js";
import { get, patch, post, del, streamMessage, capturar as encolarCaptura } from "../api.js";
import { Sheet, MicButton, ToolChips, useDictado, dictadoSoportado, SelectorModo,
         ConfirmarBorradoSesion, BotonCompartir, useProyectos, proyectosListos,
         Adjunto, ChipMenu, Camara, camaraSoportada, IMG_EXT, AccionesAdjunto,
         useAdjuntos, TiraAdjuntos, archivosDelPortapapeles, BTN_ICONO } from "../ui.js";
import { itemsDeProyectos, useProyectosVisibles } from "../proyectos.js";
import { usePrivado, privadosDe, esSesionPrivada, esMemoriaPrivada, PantallaPrivada } from "../privado.js";
import { Markdown } from "../md.js";
import { GraphView } from "../vis/grafo.js";
import { TimelineGlobal } from "../vis/timeline.js";

// las citas del motor pueden apuntar a índices (00_INDICE_*.md) además de entradas;
// solo las entradas reales tienen pantalla propia (#entry/slug) — el resto se
// muestra como etiqueta simple, sin enlace roto.
function esEntrada(path) {
  return /(?:^|\/)Entradas\/[\w-]+\.md$/.test(path);
}
// mismo botón cuadrado que la barra de Home (44px = piso táctil del brief §7):
// micrófono · pegar · adjuntar · cámara van juntos y en ese orden en las dos
// pantallas, para que el gesto se aprenda una sola vez (pedido 2026-08-06)

function slugDe(path) {
  return path.split("/").pop().replace(/\.md$/, "");
}

// El error de un turno cuyo agente local está apagado ofrece arrancarlo — el
// clic ES el permiso (POST /services/lmstudio/start) y al levantar, reintenta.
function BotonArrancarServicio({ error, onListo }) {
  const [fase, setFase] = useState("idle");
  if (!/connect|conexi|connection|refused|unreachable/i.test(error || "")) return null;
  async function arrancar() {
    setFase("arrancando");
    try {
      await post("/services/lmstudio/start");
      setFase("idle");
      onListo?.();
    } catch (e) {
      setFase("fallo");
    }
  }
  if (fase === "fallo") return html`<span style="font-size:11.5px;opacity:.7;align-self:center">no se pudo arrancar LM Studio</span>`;
  return html`
    <div role="button" tabindex="0" onClick=${fase === "idle" ? arrancar : undefined}
         style="height:36px;padding:0 16px;border-radius:var(--radius-md);display:inline-flex;align-items:center;gap:7px;font-size:13.5px;cursor:pointer;border:1px solid var(--color-divider);background:var(--color-surface)">
      ${fase === "arrancando" ? "arrancando…" : "▶ Arrancar LM Studio"}
    </div>`;
}

// El turno guarda el adjunto como markdown ![nombre](/attach/ruta) al final del
// mensaje de Diego (chat.py): acá se separa para renderizarlo como MEDIO (imagen
// visible, video/audio reproducibles) en vez de texto — mismo trato que los
// generados por el asistente. Los mensajes viejos (descripción pegada) no
// matchean y siguen saliendo como texto, como siempre.
const RX_ADJ_MD = /!\[[^\]]*\]\(\/attach\/([^)\s]+)\)/g;
/** El mensaje partido EN ORDEN: cada medio se ve donde Diego lo puso. Antes se
 *  extraían todos y se pintaban arriba, así que "mirá esta ![a] contra esta ![b]"
 *  quedaba con las dos fotos primero y el texto abajo, sin saber cuál era cuál.
 *  `.texto` (el mensaje sin medios) es lo que recorren las flechas ↑/↓. */
function partesMensaje(texto) {
  const partes = [];
  let i = 0;
  for (const m of texto.matchAll(RX_ADJ_MD)) {
    const antes = texto.slice(i, m.index).trim();
    if (antes) partes.push({ tipo: "texto", valor: antes });
    partes.push({ tipo: "media", valor: m[1] });
    i = m.index + m[0].length;
  }
  const resto = texto.slice(i).trim();
  if (resto) partes.push({ tipo: "texto", valor: resto });
  return { texto: texto.replace(RX_ADJ_MD, "").trim(), partes };
}
// Lo que pasó durante el turno (tools, tiempos, fallos) viaja pegado al final del
// mensaje del asistente detrás de un marcador (chat.py MARCA_PROCESO). Acá se
// separa: la respuesta arriba, el registro plegado abajo — "que se oculte pero se
// pueda expandir" (pedido 2026-08-07). Y todo lo que consume el texto del turno
// (ideas del mindmap, compartir) usa la parte limpia.
const RX_PROCESO = /\n*···proceso\n([\s\S]*)$/;
export function partirProceso(texto) {
  const m = RX_PROCESO.exec(texto || "");
  return { texto: (texto || "").replace(RX_PROCESO, "").trim(),
           pasos: m ? m[1].split("\n").map((l) => l.replace(/^- /, "").trim()).filter(Boolean) : [] };
}

/** Registro plegado de un turno: una línea por paso, la última primero en importar
 *  cuando algo falló. Cerrado por defecto — abrirlo es un clic. */
export function Proceso({ pasos, L, abiertoInicial = false }) {
  const [abierto, setAbierto] = useState(abiertoInicial);
  if (!pasos.length) return null;
  return html`
    <div style="margin-top:10px;padding-top:9px;border-top:1px solid var(--color-divider)">
      <div role="button" tabindex="0" onClick=${() => setAbierto(!abierto)}
           style="display:flex;align-items:center;gap:6px;cursor:pointer;font-family:var(--font-mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--text-3)">
        ${abierto ? "▾" : "▸"} ${L.tProcess} · ${pasos.length}
      </div>
      ${abierto && html`
        <div style="margin-top:8px;display:flex;flex-direction:column;gap:5px">
          ${pasos.map((p, i) => html`
            <div key=${i} style="font-family:var(--font-mono);font-size:11px;line-height:1.5;color:${p.startsWith("⚠") ? "var(--color-accent-700)" : "var(--text-2)"};overflow-wrap:anywhere">${p}</div>`)}
        </div>`}
    </div>`;
}

export function adjuntoMd(texto, adjuntos) {
  return [texto, ...(adjuntos || []).map((a) => `![${a.split("/").pop()}](/attach/${a})`)]
    .filter(Boolean).join("\n\n").trim();
}


// Cierre del tramo compactado, al estilo /compact: lo de arriba YA está en la
// memoria y salió del contexto del modelo (chat.turno recarga desde el corte),
// pero se sigue viendo. La banda lo dice con todas las letras y cada memoria
// creada es un botón que la abre.
function MarcaMemoria({ entradas, memorias, L, n }) {
  // memorias = [{slug,titulo}] del server (títulos reales); entradas = paths del
  // frontmatter `destiladas`, respaldo para sesiones destiladas antes de eso
  const fichas = (memorias || []).length
    ? memorias.map((m) => ({ slug: m.slug, label: m.titulo }))
    : (entradas || []).filter(esEntrada).map((p) => ({ slug: slugDe(p), label: slugDe(p) }));
  return html`
    <div style="display:flex;flex-direction:column;gap:7px;padding:9px 11px;margin:2px 0;border-radius:var(--radius-md);border:1px dashed color-mix(in srgb,var(--color-accent-2) 55%,transparent);background:color-mix(in srgb,var(--color-accent-2) 9%,transparent);animation:fadeUp .3s both">
      <div style="display:flex;align-items:center;gap:8px;font-family:var(--font-mono);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--color-accent-2-700)">
        <span style="flex-shrink:0">⌸ ${n} ${L.tMsgs}</span>
        <span style="flex:1;height:1px;background:color-mix(in srgb,var(--color-accent-2) 45%,transparent)"></span>
        <span style="flex-shrink:0;opacity:.8">${L.tInMemoryLong}</span>
      </div>
      ${!!fichas.length && html`
        <div style="display:flex;flex-wrap:wrap;gap:6px">
          ${fichas.map((f) => html`
            <span role="button" tabindex="0" title=${L.tOpenMemory} onClick=${() => go("entry", f.slug)}
                  style="max-width:100%;height:26px;padding:0 10px;border-radius:var(--radius-md);display:flex;align-items:center;gap:5px;font-size:11.5px;cursor:pointer;background:var(--color-accent-2-700);color:var(--color-bg);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">▤ ${f.label} ›</span>`)}
        </div>`}
    </div>`;
}

/** Aviso de contexto lleno + botón de compactar. Vive abajo, junto al input; al
 *  destilar baja el % y desaparece solo.
 *  `libera` = qué parte de la ventana ocupa la conversación, o sea lo único que
 *  el botón puede sacar. Sin ese segundo umbral el aviso salía en sesiones ya
 *  pasadas a memoria, donde lo que llena la ventana es el system prompt del modo
 *  (bug reportado 2026-08-04): pedía destilar algo que no bajaba nada. */
export function AvisoContexto({ pct, libera = 100, L, destilando, onDestilar, estilo = "" }) {
  if (!(pct >= 75 && libera >= 15)) return null;
  return html`
    <div style="display:flex;align-items:center;gap:9px;padding:7px 11px;border-radius:var(--radius-md);border:1px solid color-mix(in srgb,var(--color-accent) 45%,transparent);background:color-mix(in srgb,var(--color-accent) 8%,transparent);${estilo}">
      <span class="mem-pend" style="font-family:var(--font-mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;flex:1;min-width:0">⚠ ${L.tCtxWarn} ${pct}% — ${L.tCtxHint}</span>
      <div role="button" tabindex="0" onClick=${destilando ? null : onDestilar} class="mem-btn-accent"
           style="height:28px;padding:0 12px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:11.5px;cursor:pointer;flex-shrink:0;opacity:${destilando ? 0.6 : 1}">
        ${destilando ? "…" : L.tToMemory}</div>
    </div>`;
}

/** Diálogo de una tool que gasta plata (Higgsfield y cualquier otra nube). El turno
 *  del servidor está FRENADO esperando esta respuesta: mientras no se conteste, la
 *  generación no arranca. Si no se contesta en 3 min el server la cancela solo, así
 *  que cerrar sin elegir nunca gasta créditos.
 *
 *  Las tres salidas se eligen de una lista con casilla, como pregunta Claude Code
 *  (pedido 2026-08-07): la del medio —"no, hacelo en local"— existe porque decir
 *  que no y después escribir "hacelo con ComfyUI" eran dos mensajes para lo mismo.
 *  Contestada, la pregunta y la respuesta quedan escritas en el turno (chat.py). */
const OPCIONES_NUBE = [
  { id: "nube", ok: true, local: false, icono: "☁" },
  { id: "local", ok: false, local: true, icono: "▣" },
  { id: "no", ok: false, local: false, icono: "✕" },
];

function ConfirmarNube({ pedido, lang, onResponder }) {
  const L = dict(lang);
  const [yendo, setYendo] = useState(false);
  const [elegida, setElegida] = useState("nube");
  const args = pedido.args || {};
  // Si Ajustes › Media no fija un job_type, el server manda el catálogo y se elige
  // ACÁ: es la misma pregunta que ya autoriza el gasto (pedido 2026-08-07 — antes
  // se le pedía al modelo que lo buscara y ahí se perdía el pedido).
  const modelos = pedido.modelos || [];
  const [modelo, setModelo] = useState(args.modelo || "");
  // Lo que este job_type deja elegir y nadie fijó: se pregunta acá, antes de gastar,
  // en vez de aceptar el default a ciegas (pedido 2026-08-07). Sale del propio CLI
  // de Higgsfield, así que un modelo nuevo trae sus opciones sin tocar nada.
  const [opciones, setOpciones] = useState(pedido.opciones || []);
  const [params, setParams] = useState(() =>
    Object.fromEntries((pedido.opciones || []).map((o) => [o.nombre, o.valor])));
  // el modelo elegido acá trae SUS opciones (cada job_type tiene las suyas)
  useEffect(() => {
    if (!modelo || args.modelo) return;
    setOpciones([]);
    get(`/media/cloud-options?modelo=${encodeURIComponent(modelo)}`)
      .then((r) => {
        setOpciones(r.opciones || []);
        setParams(Object.fromEntries((r.opciones || []).map((o) => [o.nombre, o.valor])));
      }).catch(() => {});
  }, [modelo]);
  function responder(id) {
    const op = OPCIONES_NUBE.find((o) => o.id === id) || OPCIONES_NUBE[2];
    setYendo(true);
    onResponder(op.ok, op.ok ? params : {}, op.local, op.ok ? modelo : "");
  }
  return html`
    <${Sheet} onClose=${() => responder("no")}>
      <div style="padding:8px 22px 26px">
        <h3 style="margin:0 0 8px;font-family:var(--font-heading);font-size:24px">${L.tCloudQ}</h3>
        <p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:var(--text-2)">${L.tCloudBody}</p>
        ${!!args.modelo && html`
          <div style="display:flex;gap:8px;align-items:baseline;margin-bottom:10px;font-family:var(--font-mono);font-size:11.5px">
            <span style="opacity:.55;text-transform:uppercase;letter-spacing:.08em">${L.tCloudModel}</span>
            <span style="font-weight:600">${args.modelo}</span>
          </div>`}
        ${!args.modelo && elegida === "nube" && html`
          <div style="margin-bottom:14px">
            <div style="font-family:var(--font-mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;opacity:.55;margin-bottom:7px">${L.tCloudModel}</div>
            ${modelos.length ? html`
              <select value=${modelo} onChange=${(e) => setModelo(e.target.value)} class="mem-select"
                      style="width:100%;height:44px;font-family:var(--font-body);font-size:var(--fs-3)">
                <option value="">${L.tCloudPick}</option>
                ${modelos.map((m) => html`<option value=${m.job_type}>${m.nombre}</option>`)}
              </select>`
              : html`<div style="font-size:12.5px;color:var(--text-3);line-height:1.5">${L.tCloudNoModels}</div>`}
          </div>`}
        ${!!args.prompt && html`
          <div style="max-height:150px;overflow:auto;padding:10px 12px;border-radius:var(--radius-md);background:var(--color-surface);border:1px solid var(--color-divider);font-size:13px;line-height:1.5;margin-bottom:18px">${args.prompt}</div>`}
        ${!!opciones.length && elegida === "nube" && html`
          <div style="margin-bottom:18px">
            <div style="font-family:var(--font-mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;opacity:.55;margin-bottom:9px">${L.tCloudParams}</div>
            ${opciones.map((o) => html`
              <div key=${o.nombre} style="margin-bottom:11px">
                <div style="font-size:12px;opacity:.65;margin-bottom:5px">${o.nombre.replace(/_/g, " ")}</div>
                <!-- sin lista cerrada es un número (la duración de minimax_h3): campo -->
                ${!o.opciones.length ? html`
                  <input type="number" value=${params[o.nombre] ?? o.valor} min="1"
                         onInput=${(e) => setParams((p) => ({ ...p, [o.nombre]: e.target.value }))}
                         style="width:96px;height:32px;padding:0 10px;border-radius:var(--radius-md);border:1px solid var(--color-divider);background:var(--color-surface);color:var(--color-text);font-size:13px" />` : html`
                <div style="display:flex;flex-wrap:wrap;gap:6px">
                  ${o.opciones.map((v) => html`
                    <span role="button" tabindex="0" onClick=${() => setParams((p) => ({ ...p, [o.nombre]: v }))}
                          class=${params[o.nombre] === v ? "mem-btn-accent" : ""}
                          style="height:30px;padding:0 13px;border-radius:var(--radius-md);display:inline-flex;align-items:center;font-size:12.5px;cursor:pointer;${params[o.nombre] === v ? "" : "border:1px solid var(--color-divider)"}">${v}</span>`)}
                </div>`}
              </div>`)}
          </div>`}
        <!-- lista con casilla: se marca una y se confirma, como las preguntas de
             Claude Code. Un solo toque también sirve: marcar ya deja la respuesta
             lista y el botón de abajo la manda. -->
        <div style="display:flex;flex-direction:column;gap:7px;margin-bottom:16px">
          ${OPCIONES_NUBE.map((o, i) => html`
            <div key=${o.id} role="radio" tabindex="0" aria-checked=${String(elegida === o.id)}
                 onClick=${() => setElegida(o.id)}
                 style="display:flex;align-items:center;gap:11px;padding:12px 14px;border-radius:var(--radius-md);cursor:pointer;border:1.5px solid ${elegida === o.id ? "var(--color-accent)" : "var(--color-divider)"};background:${elegida === o.id ? "color-mix(in srgb,var(--color-accent) 10%,transparent)" : "var(--color-surface)"}">
              <span style="flex-shrink:0;width:19px;height:19px;border-radius:var(--radius-md);border:1.5px solid ${elegida === o.id ? "var(--color-accent)" : "var(--color-divider)"};background:${elegida === o.id ? "var(--color-accent)" : "transparent"};color:var(--color-bg);display:flex;align-items:center;justify-content:center;font-size:11px">${elegida === o.id ? "✓" : ""}</span>
              <span style="flex:1;min-width:0">
                <span style="display:block;font-size:14.5px;line-height:1.3">${o.icono} ${L.tCloudOpts[o.id]}</span>
                <span style="display:block;font-size:11.5px;color:var(--text-3);margin-top:2px">${L.tCloudOptsSub[o.id]}</span>
              </span>
              <span style="flex-shrink:0;font-family:var(--font-mono);font-size:10.5px;color:var(--text-3)">${i + 1}</span>
            </div>`)}
        </div>
        <!-- sin modelo no se puede generar en la nube: el botón espera, en vez de
             mandar un job_type vacío que muere del otro lado -->
        ${(() => { const bloq = yendo || (elegida === "nube" && !modelo); return html`
        <div role="button" tabindex="0" class="mem-btn-accent" onClick=${bloq ? null : () => responder(elegida)}
             style="min-height:52px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-family:var(--font-heading);font-size:16px;cursor:${bloq ? "default" : "pointer"};opacity:${bloq ? 0.55 : 1}">${L.tCloudSend}</div>`; })()}
      </div>
    <//>`;
}

// ------------------------------------------------------- recordar escribiendo
// Mientras se escribe en una sesión, en una tira fina sobre el compositor: las
// memorias MUY cercanas a lo que se está tecleando (pedido 2026-08-09). El
// filtro de "muy cercanas" lo hace el server (indice.sugerencias: match léxico
// Y z ≥ 2 sobre la base) — acá no se recorta un top-N de cualquier cosa, así
// que un borrador sin nada que ver no muestra la tira en absoluto.
// Cada sugerencia se puede mirar (⊙, pop-up con la memoria entera), aceptar (＋,
// se enlaza con [[slug|Título]] al mandar el mensaje) o descartar (✕, no vuelve
// a aparecer) — pedido 2026-08-10: interrumpir lo menos posible al que escribe.
const MIN_RECUERDO = 12;   // el mismo piso que aplica indice.sugerencias
// El ✕ vive en el navegador y no en el vault: es ruido al teclear, no un juicio
// sobre la memoria (ese es el lint, con su 09_Sistema/lint_ignorar.json).
const NO_RECORDAR = "mem.norecordar";
export function noRecordadas() {
  try { return JSON.parse(localStorage.getItem(NO_RECORDAR) || "[]"); } catch { return []; }
}
export function noRecordarMas(slugs) {
  localStorage.setItem(NO_RECORDAR, JSON.stringify(slugs));
}

function Recuerdos({ draft, lang, oculto, privs, enlazadas, nunca, onEnlazar, onVer, onNunca }) {
  const L = dict(lang);
  const [items, setItems] = useState([]);
  useEffect(() => {
    const q = draft.trim();
    if (q.length < MIN_RECUERDO) { setItems([]); return; }
    const timer = setTimeout(() => {
      get(`/memory/suggest?texto=${encodeURIComponent(q)}`)
        .then((r) => setItems(r || [])).catch(() => setItems([]));
    }, 450);   // se teclea rápido: una consulta por pausa, no por tecla
    return () => clearTimeout(timer);
  }, [draft]);
  // el candado de lo privado vale también acá; lo ya enlazado no se re-ofrece
  // (enlazadas vive aparte del texto — pedido 2026-08-09: nunca más markup crudo
  // [[slug|Título]] a la vista del compositor) y lo descartado tampoco
  const vivos = items.filter((m) => !(oculto && esMemoriaPrivada(m, privs)) && !nunca.includes(m.slug)
                                    && !enlazadas.some((e) => e.slug === m.slug));
  if (!vivos.length) return null;
  const btn = "flex-shrink:0;width:19px;align-self:stretch;display:flex;align-items:center;justify-content:center;cursor:pointer;font-family:var(--font-mono)";
  return html`
    <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap;padding-bottom:7px;animation:fadeUp .3s both">
      <span title=${L.tRecall} style="flex-shrink:0;font-size:10px;color:var(--color-accent-2-700)">✦</span>
      ${vivos.map((m) => html`
        <span key=${m.slug} title=${m.titulo}
              style="max-width:100%;height:23px;padding-left:7px;display:inline-flex;align-items:center;font-size:11px;border-radius:var(--radius-md);background:var(--color-surface);border:1px solid color-mix(in srgb,var(--color-accent-2) 45%,transparent)">
          <span role="button" tabindex="0" title=${L.tRecallView} onClick=${() => onVer(m)}
                style="max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer">▤ ${m.titulo}</span>
          <span role="button" tabindex="0" title=${L.tRecallAdd} onClick=${() => onEnlazar(m)}
                style="${btn};font-size:11px;color:var(--color-accent-2-700)">＋</span>
          <span role="button" tabindex="0" title=${L.tRecallNever} onClick=${() => onNunca(m.slug)}
                style="${btn};font-size:9.5px;opacity:.45;padding-right:2px">✕</span>
        </span>`)}
    </div>`;
}

/** El ⊙ de una sugerencia: la memoria entera en un pop-up, para decidir sin
 *  perder de vista lo que se estaba escribiendo. Se agrega o se cancela y el
 *  compositor sigue exactamente como estaba (pedido 2026-08-10). */
function VistaRecuerdo({ item, lang, onAgregar, onNunca, onClose }) {
  const L = dict(lang);
  const [entrada, setEntrada] = useState(null);   // null = todavía cargando
  useEffect(() => {
    let vivo = true;
    get(`/memory/entry/${item.slug}`)
      .then((r) => vivo && setEntrada(r)).catch(() => vivo && setEntrada({}));
    return () => { vivo = false; };
  }, [item.slug]);
  const boton = "flex:1;height:46px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-family:var(--font-heading);font-size:15px;cursor:pointer";
  return html`
    <${Sheet} onClose=${onClose}>
      <div style="padding:6px 22px 24px;overflow:auto">
        <div style="font-family:var(--font-mono);font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--text-3);margin-bottom:6px">${L.tRecall}</div>
        <h3 style="margin:0 0 12px;font-family:var(--font-heading);font-size:21px;line-height:1.25">${item.titulo}</h3>
        ${entrada === null
          ? html`<div style="font-size:13px;opacity:.5">…</div>`
          : html`<${Markdown} texto=${entrada.contenido || ""} style="font-size:14.5px;line-height:1.6" />`}
        <div style="display:flex;gap:8px;margin-top:20px">
          <div role="button" tabindex="0" onClick=${onClose}
               style="${boton};border:1.5px solid var(--color-divider);color:var(--text-2)">${L.tCancel}</div>
          <div role="button" tabindex="0" onClick=${onAgregar} class="mem-btn-accent" style=${boton}>${L.tRecallAdd}</div>
        </div>
        <div role="button" tabindex="0" onClick=${onNunca}
             style="margin-top:12px;text-align:center;font-family:var(--font-mono);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--text-3);cursor:pointer">✕ ${L.tRecallNever}</div>
      </div>
    <//>`;
}

// ---------------------------------------------------------------- vista CHAT
// marcador={corte, entradas}: hasta qué mensaje la sesión ya fue destilada.
export function ChatView({ mensajes, phase, activeTools, ultimoTurno, falla, onRetry, L, marcador = null,
                           alterno = null, onCancel = null, elegidas = null, onElegir = null }) {
  const marca = marcador && marcador.corte > 0 ? marcador : null;
  const [citasAbiertas, setCitasAbiertas] = useState(false);
  const scrollRef = useRef(null);
  useEffect(() => {
    const el = scrollRef.current;
    // solo si ya estabas mirando el final: si subiste a releer algo, cada evento
    // del turno te devolvía al fondo de un salto y perdías dónde estabas.
    // scrollTop === 0 es el primer render de la sesión: ahí sí, al final.
    if (el && (el.scrollTop === 0 || el.scrollHeight - el.scrollTop - el.clientHeight < 220))
      el.scrollTop = el.scrollHeight;
  }, [mensajes.length, phase]);
  return html`
    <!-- "ancha": la conversación usa el ancho de la ventana como el resto de las
         pantallas (pedido 2026-08-08). Con los 640px de la medida de lectura, en
         escritorio quedaba una columna angosta en el medio y media pantalla
         vacía a los costados — se notaba hasta en dónde caía la barra de scroll. -->
    <div ref=${scrollRef} class="mem-screen ancha" style="flex:1;overflow:auto;padding:14px 16px 18px;display:flex;flex-direction:column;gap:14px">
      ${mensajes.map((m, i) => {
        const esUltimoAsistente = m.rol === "Asistente" && i === mensajes.length - 1 && ultimoTurno;
        const archivado = marca && i < marca.corte;
        // el registro del turno viaja pegado al texto: se separa para plegarlo
        const proc = m.rol === "Asistente" ? partirProceso(m.texto) : null;
        return html`
          ${marca && i === marca.corte && html`
            <${MarcaMemoria} entradas=${marca.entradas} memorias=${marca.memorias} L=${L} n=${marca.corte} />`}
          <!-- archivado: sigue visible pero se lee como "ya no está en el contexto"
               (velo + barra de la Biblioteca al costado) -->
          <div class=${m.rol === "Diego" ? "mem-yo mem-burb-yo" : "mem-burb-mem"}
               style="${archivado ? "opacity:.55;border-left:3px solid var(--color-accent-2-700)" : ""}">
            ${m.rol === "Diego"
              ? partesMensaje(m.texto).partes.map((p) => p.tipo === "media"
                  ? html`<${Adjunto} ruta=${p.valor} />`
                  // markdown también en lo que escribe Diego: pegar un link o
                  // **algo** salía con los asteriscos y el link sin abrir
                  : html`<${Markdown} texto=${p.valor} style="font-size:15.5px;line-height:1.5" />`)
              : html`
                <div style="font-family:var(--font-mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--text-3);margin-bottom:8px">MeM</div>
                <!-- si la respuesta trae varias imágenes, cada una sale numerada y
                     con casilla: lo marcado cae en la misma bandeja que el Media
                     Manager y viaja con el próximo mensaje (pedido 2026-08-07) -->
                <${Markdown} texto=${proc.texto} elegidas=${elegidas} onElegir=${onElegir} />
                <!-- qué pasó durante el turno: plegado, y abierto de entrada si algo
                     falló — es justo cuando hace falta mirarlo (pedido 2026-08-07) -->
                <${Proceso} pasos=${proc.pasos} L=${L} abiertoInicial=${proc.texto.startsWith("⚠")} />
                ${esUltimoAsistente && !!(ultimoTurno.paginas || []).length && html`
                  <div style="padding-top:10px;margin-top:10px;border-top:1px solid var(--color-divider)">
                    <div role="button" tabindex="0" onClick=${() => setCitasAbiertas(!citasAbiertas)}
                         style="display:flex;align-items:center;gap:6px;cursor:pointer">
                      <span style="font-family:var(--font-mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--text-3)">${citasAbiertas ? "▾" : "▸"} ${L.tCited} · ${ultimoTurno.paginas.length}</span>
                      <span style="margin-left:auto;font-family:var(--font-mono);font-size:10.5px;color:var(--text-3)">${ultimoTurno.tokens} tok</span>
                    </div>
                    ${citasAbiertas && html`
                      <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px">
                        ${ultimoTurno.paginas.map((p) => esEntrada(p) ? html`
                          <span role="button" tabindex="0" onClick=${() => go("entry", slugDe(p))}
                                style="max-width:180px;height:26px;padding:0 9px;border-radius:var(--radius-md);display:flex;align-items:center;font-family:var(--font-mono);font-size:11px;cursor:pointer;background:color-mix(in srgb,var(--color-accent-2) 20%,transparent);color:var(--color-accent-2-700);overflow:hidden"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${slugDe(p)}</span></span>`
                          : html`
                          <span style="max-width:180px;height:26px;padding:0 9px;border-radius:var(--radius-md);display:flex;align-items:center;font-family:var(--font-mono);font-size:11px;opacity:.6;overflow:hidden"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p}</span></span>`)}
                      </div>`}
                  </div>`}`}
          </div>`;
      })}
      ${marca && marca.corte >= mensajes.length && html`
        <${MarcaMemoria} entradas=${marca.entradas} memorias=${marca.memorias} L=${L} n=${mensajes.length} />`}
      ${phase === "working" && html`
        <div style="align-self:flex-start;max-width:88%;animation:fadeUp .35s both">
          <${ToolChips} tools=${activeTools} onCancel=${onCancel} />
        </div>`}
      ${phase === "failed" && html`
        <div style="align-self:flex-start;max-width:88%;border:1px solid color-mix(in srgb,var(--color-accent) 55%,transparent);background:color-mix(in srgb,var(--color-accent) 12%,transparent);border-radius:var(--radius-md);padding:12px 14px">
          <!-- qué pasó y qué hacer, en dos líneas (mem/fallas.py). El volcado crudo
               vive plegado abajo: en el chat no entra nunca (pedido 2026-08-07) -->
          <div style="font-size:14px;font-weight:600;margin-bottom:4px">${falla?.problema || L.tErrTitle}</div>
          <div style="font-size:13px;line-height:1.5;color:var(--text-2);margin-bottom:10px">${falla?.sugerencia || L.tErrSub}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <div role="button" tabindex="0" onClick=${() => onRetry()} class="mem-btn-accent" style="height:36px;padding:0 16px;border-radius:var(--radius-md);display:inline-flex;align-items:center;font-size:13.5px;cursor:pointer">${L.tRetry}</div>
            <!-- que un proveedor se caiga (o se quede sin crédito) no puede dejar
                 la pregunta sin respuesta: el otro agente configurado la contesta -->
            ${alterno && html`
              <div role="button" tabindex="0" onClick=${() => onRetry(alterno.id)}
                   style="height:36px;padding:0 16px;border-radius:var(--radius-md);display:inline-flex;align-items:center;gap:7px;font-size:13.5px;cursor:pointer;border:1px solid var(--color-divider);background:var(--color-surface)">
                ${alterno.icono} ${L.tRetryWith} ${alterno.nombre}</div>`}
            <${BotonArrancarServicio} error=${falla?.detalle || ""} onListo=${onRetry} />
          </div>
          ${!!falla?.detalle && falla.detalle !== falla.problema && html`
            <${Proceso} pasos=${[falla.detalle]} L=${L} />`}
        </div>`}
      ${!mensajes.length && phase === "idle" && html`<div style="opacity:.4;font-size:13px;text-align:center;margin-top:40px">${L.phAsk}</div>`}
    </div>`;
}

// ideas discutidas: bullets de nivel superior en respuestas del asistente,
// sin importar el modo (chat.md/mindmap.md pueden producirlos igual). Antes
// era exclusivo del modo brainstorm (retirado); ahora alimenta el Mindmap.
export function extraerIdeas(textosAsistente) {
  // sobre el texto limpio: los pasos del registro también son bullets y no son ideas
  return [...new Set(textosAsistente.flatMap((t) =>
    [...partirProceso(t).texto.matchAll(/^- (.+)$/gm)].map((x) => x[1].trim())))];
}

// -------------------------------------------------------------- vista MINDMAP
// El mismo grafo con física de la vista Grafo de Memory, pero acotado a ESTA
// sesión: las memorias que citó y su vecindario semántico, más dos tipos de nodo
// que no viven en la base — el centro (la sesión) y las ideas que salieron
// conversando. Clic en una idea la lleva al cuadro de "expandir" con su texto
// completo, listo para mandarla y que la respuesta profundice esa rama; clic en
// una memoria abre su ficha, donde ya se listan las sesiones conectadas a ella.
// (Era un radial fijo sin física; se unificó con vis/grafo.js el 2026-08-07 para
// no mantener dos mapas distintos de la misma idea.)
const MAX_IDEAS = 10;

export function MindmapView({ titulo, subjects, paginas, ideas = [], onExpand, lang }) {
  const [expandiendo, setExpandiendo] = useState(false);
  const [texto, setTexto] = useState("");
  const slugs = useMemo(() => paginas.filter(esEntrada).map(slugDe), [paginas.join(",")]);
  const vivas = ideas.slice(0, MAX_IDEAS);
  // centro e ideas se inyectan al grafo: no están en mem.db y no deben estarlo
  // (mem.db es derivado de los .md; una idea suelta de un chat no es una memoria)
  const extra = useMemo(() => ({
    nodos: [{ id: "c:sesion", tipo: "centro", label: titulo, grupo: "—" },
            ...vivas.map((t, i) => ({ id: `i:${i}`, tipo: "idea", label: t, grupo: "—" }))],
    aristas: [...slugs.map((s) => ({ a: "c:sesion", b: `e:${s}`, tipo: "sesion" })),
              ...vivas.map((_, i) => ({ a: "c:sesion", b: `i:${i}`, tipo: "sesion" }))],
  }), [titulo, slugs.join(","), vivas.join("|")]);

  function enviarExpand() {
    if (!texto.trim()) return;
    onExpand(texto.trim()); setTexto(""); setExpandiendo(false);
  }
  return html`
    <div style="flex:1;min-height:0;display:flex;flex-direction:column;padding:10px 12px 18px">
      <div style="flex:1;min-height:0">
        <${GraphView} lang=${lang} slugs=${slugs} extra=${extra} alto="100%"
                      onNodo=${(n) => {
                        if (n.tipo === "idea") { setTexto(n.label); setExpandiendo(true); }
                        return n.tipo === "idea" || n.tipo === "centro";
                      }} />
      </div>
      <div style="margin-top:10px;display:flex;flex-direction:column;align-items:center;gap:8px">
        ${expandiendo && html`
          <input value=${texto} onInput=${(e) => setTexto(e.target.value)}
                 onKeyDown=${(e) => { if (e.key === "Enter") enviarExpand(); if (e.key === "Escape") setExpandiendo(false); }}
                 placeholder="expandir sobre…" autofocus
                 style="width:100%;max-width:280px;height:40px;border-radius:var(--radius-md);border:1px solid var(--color-accent);background:var(--color-bg);color:var(--color-text);padding:0 12px;outline:none" />`}
        <div role="button" tabindex="0"
             onClick=${() => (expandiendo && texto.trim() ? enviarExpand() : setExpandiendo((p) => !p))}
             style="height:38px;padding:0 16px;border-radius:var(--radius-md);display:flex;align-items:center;gap:7px;font-family:var(--font-mono);font-size:11px;letter-spacing:.09em;text-transform:uppercase;cursor:pointer;border:1px solid var(--color-divider);background:var(--color-surface)">
          ${expandiendo && texto.trim() ? "↵ Expandir" : "＋ Agregar nodo"}</div>
      </div>
    </div>`;
}

// ------------------------------------------------------------- vista TIMELINE
// La misma línea temporal de la vista Tiempo de Memory (eje real, zoom, capa
// bitemporal), alimentada con las memorias de los subjects de esta sesión.
export function TimelineView({ subjects, lang }) {
  const [eventos, setEventos] = useState(null);
  useEffect(() => {
    if (!subjects.length) { setEventos([]); return; }
    Promise.all(subjects.map((s) => get(`/memory/search?subject=${encodeURIComponent(s)}`)))
      .then((rs) => {
        const vistos = new Map();
        rs.flat().forEach((r) => vistos.set(r.slug, r));
        setEventos([...vistos.values()]);
      })
      .catch(() => setEventos([]));   // si no, la vista se quedaba en "…"
  }, [subjects.join(",")]);
  return html`
    <div style="flex:1;overflow:auto;padding:16px 20px 18px">
      ${eventos === null && html`<div style="opacity:.5;font-size:13px">…</div>`}
      ${eventos && !eventos.length && html`<div style="opacity:.5;font-size:13px">—</div>`}
      ${!!eventos?.length && html`
        <${TimelineGlobal} items=${eventos} lang=${lang} />`}
    </div>`;
}

// ------------------------------------------------------------------ detalles
/** La sesión como texto plano para mandarla por WhatsApp/mail: se lee igual en
 *  cualquier app, sin markdown de burbujas ni chips. */
function transcripcion(meta, mensajes) {
  // sin el registro técnico del turno: lo que se comparte es la conversación
  return [meta.titulo || "", ...(mensajes || []).map((m) => `${m.rol}:\n${partirProceso(m.texto).texto}`)]
    .filter(Boolean).join("\n\n");
}

function DetailsSheet({ sid, meta, mensajes, onClose, onRenombrar, onBorrar, onMoverProyecto, onGuardar, lang }) {
  const L = dict(lang);
  const [fase, setFase] = useState("idle"); // idle|confirm|running|done
  const [archivar, setArchivar] = useState(false);   // casilla "y archivar la sesión" del confirm
  const [moviendo, setMoviendo] = useState(false);
  const [destinoProy, setDestinoProy] = useState(null);   // null = nada elegido aún ("" ES "Todo")
  const [moviendoBusy, setMoviendoBusy] = useState(false);
  const [errorMover, setErrorMover] = useState("");
  const proyectosVis = useProyectosVisibles();
  const proyectoActual = String(meta.proyecto || "");

  // Un solo verbo, "Guardar en memoria" — con o sin archivar es la misma acción
  // (pedido 2026-09-05). Sin la casilla es exactamente lo que ya hace el banner
  // de contexto (onGuardar = destilarAhora, del root): sin teatro de running/done,
  // que es apropiado para lo consecuente — archivar saca la sesión de la lista.
  async function confirmarGuardar() {
    if (!archivar) { await onGuardar(); onClose(); return; }
    setFase("running");
    await post(`/sessions/${sid}/archive`, {});
    setFase("done");
  }

  function toggleMover() {
    setMoviendo((m) => !m);
    setDestinoProy(null);
    setErrorMover("");
  }
  async function confirmarMover() {
    if (moviendoBusy || destinoProy === null) return;
    setMoviendoBusy(true);
    setErrorMover("");
    try { await onMoverProyecto(destinoProy); onClose(); }
    catch (e) { setErrorMover(String(e?.message || e)); }
    setMoviendoBusy(false);
  }

  // si el procesado de fondo ya terminó antes de que Diego llegue acá, el item
  // ya salió del inbox hacia la Biblioteca — ir directo a esa entrada en vez de
  // mandarlo a un inbox vacío que no dice dónde quedó.
  async function verInbox() {
    const s = await get(`/sessions/${sid}`).catch(() => null);
    if (s?.inbox_estado === "procesada" && s.memorias?.length) go("entry", s.memorias[0].slug);
    else go("inbox", `sesion-${sid}`);
  }

  // back durante la destilación = salir honestamente: el proceso sigue en el
  // server. Un onClose vacío dejaba el sheet fuera de la pila pero visible, y
  // el siguiente back navegaba la app por debajo del modal.
  if (fase === "running") return html`
    <${Sheet} onClose=${() => go("sessions")}>
      <div style="padding:34px 22px 60px;text-align:center">
        <div style="width:96px;height:96px;margin:0 auto 22px;position:relative">
          <span style="position:absolute;inset:0;border-radius:var(--radius-md);background:color-mix(in srgb,var(--color-accent) 30%,transparent);animation:breathe 1.9s ease-in-out infinite"></span>
          <span style="position:absolute;inset:16px;border-radius:var(--radius-md);background:color-mix(in srgb,var(--color-accent-2) 40%,transparent);animation:breathe 1.9s ease-in-out infinite .4s"></span>
          <span style="position:absolute;inset:34px;border-radius:var(--radius-md);background:var(--color-accent)"></span>
        </div>
        <div style="font-family:var(--font-heading);font-size:20px;margin-bottom:6px">${L.tDistilling}</div>
        <div style="font-size:13.5px;font-family:var(--font-mono);opacity:.55">${L.tDistillingSub}</div>
      </div>
    <//>`;

  if (fase === "done") return html`
    <${Sheet} onClose=${() => go("sessions")}>
      <div style="padding:8px 22px 30px">
        <div style="width:66px;height:66px;border-radius:var(--radius-md);background:var(--color-accent-2-700);color:var(--color-bg);display:flex;align-items:center;justify-content:center;font-size:28px;margin-bottom:18px">✓</div>
        <h3 style="margin:0 0 8px;font-family:var(--font-heading);font-size:24px">${L.tArchivedTitle}</h3>
        <p style="margin:0 0 20px;font-size:14.5px;line-height:1.6;color:var(--text-2)">${L.tArchivedBody}</p>
        <div role="button" tabindex="0" onClick=${verInbox}
             style="height:52px;border-radius:var(--radius-md);border:1.5px solid var(--color-accent);color:var(--color-accent-700);display:flex;align-items:center;justify-content:center;gap:8px;font-family:var(--font-heading);font-size:16px;cursor:pointer;margin-bottom:10px">${L.tOpenInbox}</div>
        <div role="button" tabindex="0" onClick=${() => go("sessions")} class="mem-btn-accent"
             style="height:52px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-family:var(--font-heading);font-size:16px;cursor:pointer">${L.tDone}</div>
      </div>
    <//>`;

  if (fase === "confirm") return html`
    <${Sheet} onClose=${onClose}>
      <div style="padding:8px 22px 26px">
        <h3 style="margin:0 0 8px;font-family:var(--font-heading);font-size:24px">${L.tArchiveQ}</h3>
        <p style="margin:0 0 16px;font-size:14.5px;line-height:1.6;color:var(--text-2)">${L.tArchiveBody}</p>
        <div role="button" tabindex="0" aria-checked=${archivar} class="mem-tog check ${archivar ? "on" : ""}"
             style="margin-bottom:18px" onClick=${() => setArchivar((v) => !v)}>${L.tAndArchive}</div>
        <div role="button" tabindex="0" onClick=${confirmarGuardar} class="mem-btn-accent"
             style="height:52px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-family:var(--font-heading);font-size:16px;cursor:pointer;margin-bottom:10px">${L.tSaveMemory}</div>
        <div role="button" tabindex="0" onClick=${onClose} style="height:48px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:14.5px;cursor:pointer;opacity:.7">${L.tCancel}</div>
      </div>
    <//>`;

  const kt = meta.tokens_entrada_total > 999 ? `${(meta.tokens_entrada_total / 1000).toFixed(1)}k` : String(meta.tokens_entrada_total || 0);
  return html`
    <${Sheet} onClose=${onClose}>
      <div style="padding:8px 22px 24px">
        <h3 style="margin:0 0 12px;font-family:var(--font-heading);font-size:24px">${L.tDetails}</h3>
        <div style="display:flex;gap:8px;margin-bottom:16px">
          <div style="flex:1;border-radius:var(--radius-md);background:var(--color-surface);padding:12px"><div style="font-family:var(--font-heading);font-size:21px">${meta.turnos || 0}</div><div style="font-size:11.5px;opacity:.6">${L.tTurns}</div></div>
          <div style="flex:1;border-radius:var(--radius-md);background:var(--color-surface);padding:12px"><div style="font-family:var(--font-heading);font-size:21px">${kt}</div><div style="font-size:11.5px;opacity:.6">tokens</div></div>
          <div style="flex:1;border-radius:var(--radius-md);background:var(--color-surface);padding:12px"><div style="font-family:var(--font-heading);font-size:21px">${(meta.paginas_usadas || []).length}</div><div style="font-size:11.5px;opacity:.6">${L.tPages}</div></div>
        </div>
        ${meta.resumen && html`
          <div style="font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;opacity:.45;margin-bottom:6px">${L.tSummary}</div>
          <p style="margin:0 0 16px;font-size:14.5px;line-height:1.6;color:color-mix(in srgb,var(--color-text) 80%,transparent)">${meta.resumen}</p>`}
        ${!!(meta.paginas_usadas || []).length && html`
          <div style="font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;opacity:.45;margin-bottom:8px">${L.tUsedPages}</div>
          <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:22px">
            ${meta.paginas_usadas.filter(esEntrada).map((p) => html`
              <span role="button" tabindex="0" onClick=${() => go("entry", slugDe(p))}
                    style="max-width:220px;height:30px;padding:0 11px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:12px;cursor:pointer;background:color-mix(in srgb,var(--color-accent-2) 18%,transparent);color:var(--color-accent-2-700);font-family:var(--font-mono);overflow:hidden"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${slugDe(p)}</span></span>`)}
          </div>`}
        <div role="button" tabindex="0" onClick=${() => { setArchivar(false); setFase("confirm"); }}
             style="height:52px;border-radius:var(--radius-md);border:1.5px solid var(--color-accent);color:var(--color-accent-700);display:flex;align-items:center;justify-content:center;gap:8px;font-family:var(--font-heading);font-size:16px;cursor:pointer;margin-bottom:12px">${L.tSaveMemory}</div>
        <!-- mover ESTA sesión de proyecto: a propósito en dos pasos (elegir +
             confirmar), separado del chip de arriba que solo cambia de dónde
             estás parado (pedido 2026-08-31) -->
        <div role="button" tabindex="0" onClick=${toggleMover}
             style="height:46px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;justify-content:center;gap:7px;font-size:13.5px;cursor:pointer;margin-bottom:${moviendo ? 8 : 10}px">→ ${L.tMoveProject}</div>
        ${moviendo && html`
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px;font-size:13px">
            <span style="color:var(--text-2)">${proyectoActual || L.tProjAll} →</span>
            <${ChipMenu} etiqueta=${destinoProy === null ? "…" : (destinoProy || L.tProjAll)} ancho=${230}
                         items=${itemsDeProyectos(proyectosVis.filter((p) => p.nombre !== proyectoActual), destinoProy,
                                                  proyectoActual ? [{ id: "", label: L.tProjAll }] : [], lang)}
                         onPick=${setDestinoProy} />
            ${destinoProy !== null && html`
              <span role="button" tabindex="0" onClick=${confirmarMover} class="mem-btn-accent"
                    style="height:34px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;cursor:pointer;font-size:13px;opacity:${moviendoBusy ? 0.6 : 1}">${L.tConfirm}</span>`}
            ${errorMover && html`<div style="flex-basis:100%;font-size:12px;color:var(--color-priv)">${errorMover}</div>`}
          </div>`}
        <div style="display:flex;gap:8px;margin-bottom:10px">
          <div role="button" tabindex="0" onClick=${onRenombrar} style="flex:1;height:46px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;justify-content:center;font-size:13.5px;cursor:pointer">${L.tRename}</div>
          <div role="button" tabindex="0" onClick=${onBorrar} class="mem-btn-danger"
               style="flex:1;height:46px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;gap:7px;font-size:13.5px;cursor:pointer">✕ ${L.tDelete}</div>
        </div>
        <!-- la conversación entera como texto, a la hoja de compartir del móvil -->
        <${BotonCompartir} lang=${lang} etiqueta=${true} titulo=${meta.titulo || sid}
                           texto=${transcripcion(meta, mensajes)}
                           estilo="width:100%;height:46px;border-radius:var(--radius-md);justify-content:center" />
      </div>
    <//>`;
}

// ------------------------------------------------------------------- Chat (root)
export function Chat() {
  const s = useStore();
  const L = dict(s.lang);
  const sid = s.param;
  const [meta, setMeta] = useState(null);
  const [mensajes, setMensajes] = useState([]);
  const [modos, setModos] = useState([]);
  const [phase, setPhase] = useState("idle");
  const [activeTools, setActiveTools] = useState([]);
  const [ultimoTurno, setUltimoTurno] = useState(null);
  // {problema, sugerencia, detalle} — el server ya lo manda traducido (mem/fallas.py)
  const [falla, setFalla] = useState(null);
  const [ultimoEnviado, setUltimoEnviado] = useState("");
  const [ultimosAdjuntos, setUltimosAdjuntos] = useState([]);
  // Lo que venía escrito y subido en el chatbox de Home cuando Diego empezó a
  // teclear: la sesión se abrió sola y el borrador sigue acá, sin mandar nada
  // (pedido 2026-08-10). Se lee al construir el estado —no en un efecto— porque
  // el texto y los adjuntos son el valor INICIAL de sus hooks.
  const [inicial] = useState(() => {
    const crudo = sessionStorage.getItem(`mem.draft.${sid}`);
    if (!crudo) return {};
    sessionStorage.removeItem(`mem.draft.${sid}`);
    try { return JSON.parse(crudo) || {}; } catch { return {}; }
  });
  const [draft, setDraft] = useState(inicial.texto || "");
  const [sheet, setSheet] = useState(null);
  const [stored, setStored] = useState(false);
  const [ags, setAgs] = useState({ agentes: [], asignaciones: {} });
  const [memorias, setMemorias] = useState([]);   // entradas nacidas en esta sesión
  const [adjuntos, setAdjuntos] = useState([]);   // /attach/ vivos citados en los mensajes
  const [enlazadas, setEnlazadas] = useState(inicial.enlazadas || []);   // [{slug,titulo}] elegidas en "Recuerdos", va como chip
  const [viendoRecuerdo, setViendoRecuerdo] = useState(null);   // sugerencia abierta en su pop-up
  const [nunca, setNunca] = useState(noRecordadas);             // sugerencias descartadas ("no me la recomiendes")
  const [porBorrar, setPorBorrar] = useState(null);
  const [editandoTitulo, setEditandoTitulo] = useState(false);
  const [tituloTmp, setTituloTmp] = useState("");
  const [porConfirmar, setPorConfirmar] = useState(null);   // tool de nube esperando el ok
  // adjuntar dentro de la sesión (pedido 2026-08-06): mismos botones que el
  // chatbox de Home — pegar, archivo, cámara — porque a media conversación hace
  // falta pasarle una foto igual que al empezarla. Varios a la vez y con la misma
  // ficha (miniatura + ✕) que Home: el hook es el mismo (pedido 2026-08-07).
  const adjs = useAdjuntos(inicial.adjuntos || []);
  const [subiendo, setSubiendo] = useState(false);
  const [camara, setCamara] = useState(false);
  const [arrastrando, setArrastrando] = useState(false);   // drag&drop, paridad con Home
  const dragDepth = useRef(0);
  const fileRef = useRef(null);
  const camRef = useRef(null);
  // Medios elegidos de una respuesta del asistente (casillas en Markdown) o
  // traídos desde Memory al abrir la sesión: viajan con el próximo mensaje
  // como referencia (enviarDraft). La galería para BUSCARLOS vive en Memory
  // (pedido 2026-08-10) — acá solo queda la bandeja de selección.
  const [seleccionMedia, setSeleccionMedia] = useState([]);
  const dictado = useDictado(s.lang === "en" ? "en-US" : "es-ES", (t) => setDraft((p) => (p ? p + " " : "") + t));
  const taRef = useRef(null);
  const hist = useRef({ i: -1, vivo: "" });   // ↑/↓ en el input; i = -1: no estoy navegando
  const acRef = useRef(null);                 // aborta el SSE al salir (el turno sigue en el server)
  const tituloRef = useRef(null);
  useEffect(() => { if (editandoTitulo) { tituloRef.current?.focus(); tituloRef.current?.select(); } }, [editandoTitulo]);
  // se venía tecleando en Home: el cursor cae al final de lo escrito y la frase
  // se sigue sin tocar nada (la sesión se abrió sola en medio de la palabra).
  // useLayoutEffect y no useEffect: corre en el mismo bloque que el montaje del
  // textarea, antes de pintar. useEffect llega un frame tarde y para entonces el
  // celu ya decidió bajar el teclado, porque el input que lo tenía (el de Home)
  // acaba de desaparecer y ninguno tomó la posta.
  //
  // Dos veces y no una: el marco de abajo (sin meta) y la sesión entera son
  // árboles distintos, así que al llegar meta el textarea se reemplaza por otro
  // nodo y el foco se queda con el que ya no existe. Por eso la dependencia es
  // `!!meta` — y por eso no se le roba el foco a nada: si Diego tocó otra cosa
  // mientras tanto, esa cosa manda.
  useLayoutEffect(() => {
    if (!inicial.texto) return;
    const t = taRef.current;
    const a = document.activeElement;
    if (!t || t === a || a instanceof HTMLInputElement || a instanceof HTMLTextAreaElement) return;
    t.focus();
    t.selectionStart = t.selectionEnd = t.value.length;
  }, [!!meta]);
  // cambiar de sesión sin desmontar Chat (desde el sidebar/tabbar — pedido
  // 2026-08-31, antes desde el selector de arriba) no reejecuta el useState
  // de `inicial` —solo corre al montar—, así que un draft que llega DESPUÉS,
  // para un sid DISTINTO del que ya se leyó, cae acá. En el primer montaje no
  // hay nada que leer: `inicial` ya se comió esa misma clave.
  useEffect(() => {
    const crudo = sessionStorage.getItem(`mem.draft.${sid}`);
    if (!crudo) return;
    sessionStorage.removeItem(`mem.draft.${sid}`);
    let p = {};
    try { p = JSON.parse(crudo) || {}; } catch { return; }
    if (p.texto) setDraft((d) => (d ? `${d}\n${p.texto}` : p.texto));
    if (p.enlazadas?.length) setEnlazadas((e) => [...e, ...p.enlazadas.filter((x) => !e.some((y) => y.slug === x.slug))]);
    if (p.adjuntos?.length) setSeleccionMedia((m) => [...m, ...p.adjuntos
      .filter((r) => !m.some((x) => x.ruta === r))
      .map((r) => ({ ruta: r, titulo: r.split("/").pop() }))]);
  }, [sid]);
  const { oculto } = usePrivado();
  const privsProy = privadosDe(useProyectos());

  useEffect(() => { setSeleccionMedia([]); }, [sid]);

  /** Un turno que no llegó a destino: se muestra el problema y qué hacer, más los
   *  botones de reintento. Acepta un texto suelto (los avisos que arma el cliente). */
  function fallar(f) { setFalla(typeof f === "string" ? { problema: f } : f); setPhase("failed"); }

  // (el ResizeObserver que medía el pie flotante y publicaba --foot-h se fue el
  // 2026-08-08: el pie ya no flota, es un hermano más de la columna flex y el
  // panel de scroll termina solo donde el pie empieza — no hay hueco que calcular)

  useEffect(() => {
    let vivo = true, poll = 0;
    get(`/sessions/${sid}`).then((r) => {
      if (!vivo) return;
      setMeta(r.meta); setMensajes(r.mensajes); setMemorias(r.memorias || []); setAdjuntos(r.adjuntos || []);
      // entrar a una sesión ES mudarse a su proyecto: el chip de las tres
      // pantallas tiene que decir lo mismo, y sobre todo el server necesita
      // saber desde dónde se pregunta (X-Proyecto) para no filtrar de más ni
      // de menos lo privado. Antes solo se sincronizaba al cambiarlo a mano.
      const suyo = String(r.meta?.proyecto || "");
      if (suyo !== String(getState().proyecto || "")) setState({ proyecto: suyo });
      const ult = r.mensajes[r.mensajes.length - 1];
      if (!ult || ult.rol !== "Diego") return;
      setUltimoEnviado(ult.texto);
      // El mensaje de Diego está sin contestar, pero eso no quiere decir que el
      // turno se haya perdido: vive en el server, no en esta pestaña. Salir del
      // chat mientras se genera un video de 30 min (o cerrar la app entera) ya no
      // lo tira — se pregunta cómo va y se sigue mirando desde donde iba.
      get(`/sessions/${sid}/turn`).then((t) => {
        if (!vivo) return;
        if (t.estado !== "working" && t.estado !== "confirmando") {
          fallar(L.tErrPending); return;
        }
        setPhase("working"); setActiveTools(t.tools || []);
        if (t.pedido) setPorConfirmar(t.pedido);
        // el SSE de este turno se lo llevó el unmount: acá el canal es preguntar
        // cada tanto. Con la pestaña de fondo no se pregunta (vuelve al enfocar).
        poll = setInterval(async () => {
          if (document.visibilityState !== "visible") return;
          const u = await get(`/sessions/${sid}/turn`).catch(() => null);
          if (!u || !vivo) return;
          setActiveTools(u.tools || []);
          if (u.estado === "working" || u.estado === "confirmando") { setPorConfirmar(u.pedido || null); return; }
          clearInterval(poll);
          if (u.estado === "done") aplicarDone(u.resultado);
          else fallar(u.estado === "cancelado" ? L.tCancelled : (u.falla || u.detalle || L.tErrPending));
        }, 2500);
      }).catch(() => fallar(L.tErrPending));
    }).catch(() => { setState({ sesionActiva: null }); go("home"); });  // borrada en otro lado
    get("/modes").then(setModos).catch(() => {});
    // primer turno escrito en el chatbox de Home: viaja como {texto, adjunto}
    // (las versiones viejas dejaban solo el texto suelto — se acepta igual)
    const pendiente = sessionStorage.getItem(`mem.pending.${sid}`);
    if (pendiente) {
      sessionStorage.removeItem(`mem.pending.${sid}`);
      let p = { texto: pendiente, adjuntos: [] };
      try { const j = JSON.parse(pendiente); if (j && typeof j === "object") p = j; } catch { /* texto plano */ }
      // `adjunto` suelto = lo que dejó un shell viejo (o el share del sistema)
      setTimeout(() => enviarTexto(p.texto, p.adjuntos || (p.adjunto ? [p.adjunto] : [])), 300);
    }
    return () => { vivo = false; clearInterval(poll); acRef.current?.abort(); };
  }, [sid]);

  useEffect(() => { get("/agents").then(setAgs).catch(() => {}); }, []);
  // esta ES la sesión en uso: sobrevive a cambiar de pantalla y a recargar la app
  useEffect(() => { if (meta) setState({ sesionActiva: { id: sid, titulo: meta.titulo || sid, proyecto: meta.proyecto || "" } }); },
            [sid, meta?.titulo, meta?.proyecto]);

  /** Cierre del turno, venga por SSE (lo normal) o por GET /turn al reengancharse
   *  después de haber salido del chat: el resultado es el mismo objeto. */
  function aplicarDone(d) {
    setMensajes((p) => [...p, { rol: "Asistente", texto: d.texto }]);
    setUltimoTurno({ paginas: d.paginas, tokens: d.tokens_entrada });
    setPhase("idle"); setActiveTools([]);
    // la meta real la escribe el server (título auto, proyecto fijado por el
    // agente, subjects, contexto): se relee en vez de adivinarla en el cliente
    get(`/sessions/${sid}`).then((r) => { setMeta(r.meta); setMemorias(r.memorias || []); setAdjuntos(r.adjuntos || []); }).catch(() => {});
  }

  /** reintento=true: la burbuja de Diego ya está en pantalla (y en el servidor),
   *  no se agrega de nuevo. agente: fuerza otro agente solo para este turno. */
  async function enviarTexto(texto, adjuntos = [], { reintento = false, agente = "" } = {}) {
    const limpio = texto.trim();
    if ((!limpio && !adjuntos.length) || phase === "working") return;
    // la burbuja optimista lleva el mismo markdown de adjuntos que va a guardar
    // el server: los medios se ven al instante, no recién al releer la sesión
    if (!reintento) setMensajes((p) => [...p, { rol: "Diego", texto: adjuntoMd(limpio, adjuntos) }]);
    setUltimoEnviado(limpio); setUltimosAdjuntos(adjuntos);
    setDraft(""); setPhase("working"); setActiveTools([]); setFalla(null);
    acRef.current = new AbortController();
    await streamMessage(sid, limpio, {
      adjuntos, agente, signal: acRef.current.signal,
      // t0/fin: la tarjeta de generación muestra cuánto lleva y se apaga recién
      // cuando el server avisa que esa tool terminó (pedido 2026-08-06)
      onTool: (d) => setActiveTools((p) => [...p, { ...d, t0: Date.now() }]),
      onToolFin: (d) => setActiveTools((p) => {
        const i = p.findLastIndex((t) => t.tool === d.tool && !t.fin);
        return i < 0 ? p : p.map((t, k) => (k === i ? { ...t, fin: Date.now() } : t));
      }),
      onConfirm: (d) => setPorConfirmar(d),
      onDone: aplicarDone,
      onCancel: () => fallar(L.tCancelled),
      onError: fallar,
    });
  }
  /** Detener: el server corta el turno donde pueda y, si hay una generación en
   *  curso, además interrumpe ComfyUI — si no, seguiría los 20 min hasta el final
   *  aunque ya nadie la espere. El mensaje de Diego queda sin contestar, así que
   *  la sesión lo muestra como pendiente y se puede reintentar. */
  async function cancelarTurno() {
    const comfy = activeTools.some((t) => !t.fin && t.tool?.startsWith("crear_"));
    try { await post(`/sessions/${sid}/cancel`, { comfy }); }
    catch { fallar(L.tCancelled); }   // ya no había turno vivo
  }
  /** El turno sigue abierto del otro lado: se contesta y el diálogo se va. Si el
   *  POST falla, el server lo cancela solo al vencer su espera (nunca gasta). */
  async function responderConfirmacion(ok, params = {}, local = false, modelo = "") {
    setPorConfirmar(null);
    try { await post(`/sessions/${sid}/confirm`, { ok, params, local, modelo }); } catch { /* vence solo */ }
  }
  function toggleSeleccionMedia(item) {
    setSeleccionMedia((p) => (p.some((x) => x.ruta === item.ruta) ? p.filter((x) => x.ruta !== item.ruta) : [...p, item]));
  }
  // una sola bandeja para las dos formas de elegir un medio (Media Manager y las
  // casillas de una respuesta): así la tira de chips de abajo y el ↑ ya sirven
  // para ambas sin duplicar nada.
  const rutasElegidas = new Set(seleccionMedia.map((m) => m.ruta));
  /** El compositor de siempre manda el comando; si hay medios elegidos —en el
   *  Media Manager o con las casillas de una respuesta— sus rutas van pegadas al
   *  texto como markdown (se ven en la burbuja, igual que un adjunto normal) + una
   *  nota que le dice al modelo que las puede pasar como `referencia` a
   *  crear_imagen/crear_video sin buscarlas. */
  async function enviarDraft() {
    if (subiendo || phase === "working") return;
    let texto = draft.trim();
    if (enlazadas.length) {
      // recién acá se vuelven [[slug|Título]] de verdad — md.js ya las renderiza
      // como chip en la burbuja, así que el markup crudo nunca llega a pantalla
      texto = `${texto}\n\n${enlazadas.map((e) => `[[${e.slug}|${e.titulo}]]`).join(" ")}`.trim();
    }
    if (seleccionMedia.length) {
      const bloques = seleccionMedia.map((m) => `![${m.titulo || m.ruta.split("/").pop()}](/attach/${m.ruta})`).join("\n");
      const rutas = seleccionMedia.map((m) => m.ruta).join(", ");
      texto = `${texto}\n\n${bloques}\n\n(Referencias elegidas — usa la que corresponda como \`referencia\` de crear_imagen/crear_video: ${rutas})`.trim();
    }
    // los adjuntos se suben una vez y viajan con el turno; el server los lee con
    // el mismo lector del inbox y los pega al mensaje, igual que el primero de Home
    let rutas = [];
    if (adjs.items.length) {
      setSubiendo(true);
      try { rutas = await adjs.subir(); }
      catch (e) { fallar(String(e.message || e)); return; }
      finally { setSubiendo(false); }
    }
    if (!texto && !rutas.length) return;
    hist.current = { i: -1, vivo: "" };   // lo enviado ya es historial: se empieza de nuevo
    adjs.limpiar();
    enviarTexto(texto, rutas);
    setSeleccionMedia([]);
    setEnlazadas([]);
  }
  /** ↑/↓ recorren lo que Diego ya escribió en la sesión, como el historial de una
   *  terminal — para repetir un prompt o editarlo sin volver a tipearlo.
   *  Solo cuando el cursor está en el borde del texto y no hay nada seleccionado:
   *  dentro de un mensaje largo las flechas tienen que seguir moviendo el cursor.
   *  Al entrar al historial se guarda lo que estabas escribiendo y vuelve al
   *  bajar hasta el final. Se descarta el markdown del adjunto (![](/attach/…)):
   *  reenviar esa línea le pegaría el medio de nuevo al mensaje. */
  function onKey(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); enviarDraft(); return; }
    const arriba = e.key === "ArrowUp";
    if (!arriba && e.key !== "ArrowDown") { hist.current.i = -1; return; }
    const ta = e.target;
    if (ta.selectionStart !== ta.selectionEnd) return;
    if (arriba ? ta.selectionStart !== 0 : ta.selectionEnd !== ta.value.length) return;

    const previos = mensajes.filter((m) => m.rol === "Diego")
      .map((m) => partesMensaje(m.texto).texto).filter(Boolean);
    const h = hist.current;
    if (arriba ? h.i + 1 >= previos.length : h.i < 0) return;   // ya en el extremo
    e.preventDefault();
    if (h.i < 0) h.vivo = draft;                                // el borrador en curso
    h.i += arriba ? 1 : -1;
    setDraft(h.i < 0 ? h.vivo : previos[previos.length - 1 - h.i]);
    // el cursor al borde por el que se sigue navegando: así ↑↑↑ sigue yendo atrás
    // en vez de quedarse moviéndose dentro del mensaje que acaba de cargar.
    requestAnimationFrame(() => {
      const t = taRef.current;
      if (t) t.selectionStart = t.selectionEnd = arriba ? 0 : t.value.length;
    });
  }
  /** Enlazar una memoria sugerida: se agrega como chip en su propia tira, no
   *  como [[slug|Título]] crudo en el textarea (pedido 2026-08-09 — el chip
   *  del mensaje ya enviado se veía bien, pero mientras se escribía era "una
   *  serie extraña de caracteres"). Va al final del texto recién al enviar. */
  function enlazarMemoria(m) {
    setEnlazadas((p) => (p.some((e) => e.slug === m.slug) ? p : [...p, { slug: m.slug, titulo: m.titulo }]));
    setViendoRecuerdo(null);   // venga del chip o del pop-up, la decisión ya está tomada
  }
  function quitarEnlazada(slug) { setEnlazadas((p) => p.filter((e) => e.slug !== slug)); }
  /** "No me la recomiendes": se cae de la tira y no vuelve en ninguna sesión. */
  function nuncaRecordar(slug) {
    const lista = [...new Set([...nunca, slug])];
    noRecordarMas(lista); setNunca(lista); setViendoRecuerdo(null);
  }

  // el reintento repite el turno CON sus adjuntos: sin ellos, el server guardaba el
  // texto de nuevo (no matcheaba el mensaje pendiente) y el modelo perdía los medios
  function retry(agente = "") { setPhase("idle"); enviarTexto(ultimoEnviado, ultimosAdjuntos, { reintento: true, agente }); }

  async function cambiarModo(nuevo) {
    setMeta((p) => ({ ...p, modo: nuevo }));
    await patch(`/sessions/${sid}`, { modo: nuevo });
  }
  /** Mover ESTA sesión a otro proyecto: paso aparte, explícito y con
   *  confirmación — ⋯ → "Mover a otro proyecto" (pedido 2026-08-31: el chip de
   *  arriba que hacía esto sin avisar se fue, ver DetailsSheet). */
  async function moverProyectoSesion(nombre) {
    setMeta((p) => ({ ...p, proyecto: nombre }));
    setState({ proyecto: nombre });   // "entrar a una sesión es mudarse a su proyecto" también al revés
    await patch(`/sessions/${sid}`, { proyecto: nombre });
  }
  /** Renombrar in situ (pedido 2026-08-09: nada de prompt()) — el título se
   *  vuelve un input en el mismo lugar. iniciarRenombre también cierra el
   *  sheet de detalles cuando se dispara desde ahí. */
  function iniciarRenombre() { setSheet(null); setTituloTmp(meta.titulo || sid); setEditandoTitulo(true); }
  async function commitTitulo() {
    const nuevo = tituloTmp.trim();
    setEditandoTitulo(false);
    if (nuevo && nuevo !== (meta.titulo || sid)) {
      await patch(`/sessions/${sid}`, { titulo: nuevo }).catch(() => {});
      setMeta((p) => ({ ...p, titulo: nuevo }));
    }
  }
  const [destilando, setDestilando] = useState(false);
  /** "Pasar a memoria" sin archivar: destila lo aún no destilado, marca el corte
   *  y el próximo turno arranca liviano (solo resumen + lo posterior al corte). */
  async function destilarAhora() {
    if (destilando || phase === "working") return;
    setDestilando(true);
    try {
      await post(`/sessions/${sid}/distill`, {});
      const r = await get(`/sessions/${sid}`);
      setMeta(r.meta); setMemorias(r.memorias || []); setAdjuntos(r.adjuntos || []);
    } catch (e) { fallar(String(e.message || e)); }
    finally { setDestilando(false); }
  }

  /** ✕: salir de la sesión — Home vuelve a estar en blanco para capturar. Es la
   *  única forma (con borrarla) de soltar la sesión activa: cambiar de pantalla no. */
  function cerrarSesion() { setState({ sesionActiva: null }); go("home"); }

  async function borrarSesion(conContenido) {
    await del(`/sessions/${sid}${conContenido ? "?contenido=1" : ""}`).catch(() => {});
    setPorBorrar(null);
    setState({ sesionActiva: null });
    go("home");
  }
  function storeDraft() {
    if (!draft.trim()) return;
    encolarCaptura({ contenido: draft, tipo: "nota", contexto: "", tags: [], subjects: meta?.subjects || [],
                     proyecto: String(meta?.proyecto || ""), adjunto: "", origen: "chat" });
    setDraft(""); setStored(true); setTimeout(() => setStored(false), 2200);
  }

  // pegar / adjuntar / cámara — mismos gestos que el chatbox de Home
  /** El botón de pegar: primero mira si el portapapeles trae un ARCHIVO (una
   *  captura de pantalla, un audio) y lo adjunta; si no, pega el texto. */
  async function pegar() {
    try {
      const items = await navigator.clipboard.read();
      const files = [];
      for (const it of items) {
        const tipo = it.types.find((t) => /^(image|audio|video)\//.test(t));
        if (tipo) files.push(new File([await it.getType(tipo)], `pegado-${files.length + 1}.${tipo.split("/")[1].replace("jpeg", "jpg")}`, { type: tipo }));
      }
      if (adjs.agregar(files)) return;
    } catch { /* sin permiso o sin clipboard.read(): se intenta el texto */ }
    try {
      const t = await navigator.clipboard.readText();
      if (t) setDraft((p) => (p ? p + "\n" : "") + t);
    } catch { /* sin permiso de portapapeles: no-op */ }
  }
  function onArchivo(e) { adjs.agregar(e.target.files); e.target.value = ""; }

  // drag&drop de toda la pantalla (pedido 2026-08-09: paridad con Home, que ya
  // lo soporta sobre su chatbox) — mismo patrón dragDepth/arrastrando de
  // home.js, el highlight se pinta en .mem-chat-foot. window.__memDragGuard
  // (ui.js) ya evita que soltar afuera navegue a file://.
  function onDragEnter(e) {
    e.preventDefault();
    dragDepth.current++;
    setArrastrando(true);
  }
  function onDragOver(e) { e.preventDefault(); }
  function onDragLeave(e) {
    e.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (!dragDepth.current) setArrastrando(false);
  }
  function onDrop(e) {
    e.preventDefault();
    dragDepth.current = 0; setArrastrando(false);
    adjs.agregar(e.dataTransfer?.files);
  }
  // sin getUserMedia (PWA por http:// en la LAN) el input con capture abre la
  // cámara nativa del celular igual: mismo botón, un camino u otro
  function abrirCamara() { if (camaraSoportada()) setCamara(true); else camRef.current?.click(); }

  // Mientras GET /sessions/{sid} viaja ya se puede SEGUIR ESCRIBIENDO. Antes acá
  // había un <div> vacío, así que el compositor no existía todavía cuando corría
  // el efecto de foco de arriba (que solo corre al montar): la primera letra
  // tecleada en Home llegaba, pero el cursor no, y en el celu el teclado se
  // cerraba. Este marco es el mismo .mem-chat-foot de siempre, con el mismo
  // taRef, y sin nada de la sesión — el candado de privacidad corre abajo, en
  // cuanto llega meta, y hasta entonces esto solo muestra lo que Diego escribió.
  if (!meta) return html`
    <div style="flex:1;display:flex;flex-direction:column;min-height:0">
      <div style="flex:1"></div>
      <div class="mem-chat-foot">
        <div class="mem-comp">
          <div class="mem-comp-in">
            <textarea ref=${taRef} value=${draft} onInput=${(e) => setDraft(e.target.value)}
                      placeholder=${L.phAsk} rows=${Math.min(4, draft.split("\n").length)}
                      style="width:100%;display:block;padding:0;border:0;background:transparent;resize:none;outline:none;font-family:var(--font-body);font-size:16px;line-height:1.45;color:var(--color-text);max-height:96px"></textarea>
          </div>
          <div class="mem-btn-soft" style="${BTN_ICONO};font-size:18px;opacity:.5">↑</div>
        </div>
      </div>
    </div>`;
  // sesión de un proyecto privado: en el celular sin verificar no se muestra
  // NADA (ni el título); hasta cargar /projects se asume privada
  const sesPrivada = esSesionPrivada(meta, privsProy);
  if (oculto && (!proyectosListos() || sesPrivada)) return html`
    <div style="flex:1;display:flex;flex-direction:column"><${PantallaPrivada} lang=${s.lang} /></div>`;
  const modoActual = modos.find((m) => m.nombre === meta.modo) || MODE_FALLBACK[meta.modo] || MODE_FALLBACK.chat;
  // Quién contesta ESTE modo, no el de "chat": el taller lo atiende el agente de
  // «crear» (media.md lo pide en su frontmatter) y el chip decía Smart mientras
  // cargaba el qwen local — la etiqueta mentía (2026-08-07).
  const tareaAgente = modoActual.agente || "chat";
  const agenteChat = ags.agentes.find((a) => a.id === ags.asignaciones?.[tareaAgente]) || ags.agentes[0] || null;
  const agenteAlterno = ags.agentes.find((a) => a.id !== agenteChat?.id) || null;
  /** Cambiar quién contesta este modo, desde la propia cabecera. Un modo que
   *  genera necesita tool-calling por API: los que no lo tienen se ven, pero
   *  dicen por qué no se pueden elegir (si no, la generación queda sin salida). */
  async function elegirAgente(id) {
    if (modoActual.crea && ags.tools?.[id] === false) return;
    const asignaciones = { ...(ags.asignaciones || {}), [tareaAgente]: id };
    setAgs((a) => ({ ...a, asignaciones }));
    try { setAgs(await post("/agents", { agentes: ags.agentes, asignaciones })); }
    catch { setAgs(await get("/agents")); }
  }
  const subjects = meta.subjects || [];
  const ideasSesion = extraerIdeas(mensajes.filter((m) => m.rol === "Asistente").map((m) => m.texto));
  // % de la ventana del modelo que usó el último turno (0 = ventana desconocida)
  const ctxMax = Number(meta.contexto_max || 0);
  const ctxPct = ctxMax ? Math.min(100, Math.round(100 * Number(meta.tokens_entrada_ultimo_turno || 0) / ctxMax)) : 0;
  const liberaPct = ctxMax ? Math.round(100 * Number(meta.tokens_historial_ultimo_turno || 0) / ctxMax) : 0;
  const marcador = { corte: Number(meta.destilado_hasta || 0), entradas: meta.destiladas || [], memorias };
  // mismo tratamiento que bordeCard/fondoCard de home.js — el pie es el destino
  // visible del drop aunque el listener viva en la pantalla entera
  const bordeFoot = arrastrando ? "1px dashed var(--color-accent)" : "1px solid var(--color-divider)";
  const fondoFoot = arrastrando ? "color-mix(in srgb,var(--color-accent) 10%,var(--color-bg))" : "var(--color-bg)";

  return html`
    <div onDragEnter=${onDragEnter} onDragOver=${onDragOver} onDragLeave=${onDragLeave} onDrop=${onDrop}
         style="position:relative;flex:1;display:flex;flex-direction:column;animation:scIn .4s cubic-bezier(.22,1,.36,1);min-height:0">
      <div class="mem-chat-head ${sesPrivada ? "mem-chat-head-priv" : ""}">
        <!-- FILA 1: volver · título (pedido 2026-08-04; el buscador de sesiones
             se fue al sidebar/tabbar — pedido 2026-08-31) -->
        <div style="display:flex;align-items:center;gap:8px">
          <div role="button" tabindex="0" onClick=${back} style="width:44px;height:44px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:19px;cursor:pointer;flex-shrink:0;margin-left:-8px">‹</div>
          <div style="flex:1;min-width:0">
            ${editandoTitulo
              ? html`<input ref=${tituloRef} value=${tituloTmp} onInput=${(e) => setTituloTmp(e.target.value)}
                       onKeyDown=${(e) => { if (e.key === "Enter") commitTitulo(); if (e.key === "Escape") setEditandoTitulo(false); }}
                       onBlur=${commitTitulo}
                       style="width:100%;font-size:15px;font-weight:700;font-family:inherit;padding:0;border:0;border-bottom:1px solid var(--color-accent);background:transparent;color:var(--color-text);outline:none" />`
              : html`<div role="button" tabindex="0" title=${L.tRename} onClick=${iniciarRenombre}
                 style="font-size:15px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:text">${meta.titulo || sid}</div>`}
            <div style="display:flex;align-items:center;gap:6px;font-family:var(--font-mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--text-3)">
              <span style="flex-shrink:0;width:6px;height:6px;border-radius:var(--radius-md);background:var(--color-accent-2)"></span>
              <span style="flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${subjects.join(" · ") || "—"}</span>
            </div>
          </div>
          <div role="button" tabindex="0" onClick=${() => setSheet("details")} style="width:44px;height:44px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:17px;flex-shrink:0;margin-right:-8px">⋯</div>
        </div>
        <!-- FILA 2: modo (dropdown) y proyecto en el mismo renglón; a la derecha
             papelera · cerrar (una sesión se trabaja siempre a pantalla completa,
             ya no hay vista reducida en Home — pedido 2026-08-04) -->
        <div class="mem-chat-head-row2" style="margin-top:8px">
          <${SelectorModo} valor=${meta.modo} modos=${modos} lang=${s.lang} onPick=${cambiarModo} />
          ${sesPrivada && html`
            <span class="mem-proy-chip mem-privada" title=${L.tPriv}>⚿ ${L.tPrivado}</span>`}
          ${agenteChat && html`
            <${ChipMenu} etiqueta=${`${agenteChat.icono} ${agenteChat.nombre}`} ancho=${230}
                         titulo=${`${L.tAgentFor} ${modoActual.nombre}`} estilo="max-width:none"
                         items=${ags.agentes.map((a) => ({
                           id: a.id, label: `${a.icono} ${a.nombre}`, on: a.id === agenteChat.id,
                           sub: modoActual.crea && ags.tools?.[a.id] === false ? L.tAgentNoTools : a.modelo,
                         }))}
                         onPick=${elegirAgente} />`}
          <span style="margin-left:auto;display:inline-flex;align-items:center;gap:6px">
            <!-- el ⌸ "Grabar como sesión" se quitó (pedido 2026-08-06): el primer
                 turno ya pasa la temporal a activa solo (chat.turno).
                 Borrar se mudó al sheet de "⋯": era un chip de 32px pegado al ✕
                 de cerrar, o sea la acción destructiva a un dedo de distancia de
                 la inocua. En el sheet tiene su propio espacio y su rótulo. -->
            <span role="button" tabindex="0" title=${L.tCloseSession} class="mem-proy-chip" style="padding:0 7px"
                  onClick=${cerrarSesion}>✕</span>
          </span>
        </div>
      </div>

      ${meta.modo === "mindmap" ? html`<${MindmapView} titulo=${meta.titulo || sid} subjects=${subjects} paginas=${meta.paginas_usadas || []} ideas=${ideasSesion} lang=${s.lang} onExpand=${(t) => enviarTexto(`Expande sobre: ${t}`)} />`
        : meta.modo === "timeline" ? html`<${TimelineView} subjects=${subjects} lang=${s.lang} />`
        : html`<${ChatView} mensajes=${mensajes} phase=${phase} activeTools=${activeTools} ultimoTurno=${ultimoTurno} falla=${falla} onRetry=${retry} L=${L} marcador=${marcador} alterno=${agenteAlterno} onCancel=${cancelarTurno}
                            elegidas=${rutasElegidas} onElegir=${(ruta, titulo) => toggleSeleccionMedia({ ruta, titulo })} />`}

      <!-- arrastrar un archivo lo adjunta desde cualquier punto de la pantalla
           (los handlers viven en el contenedor raíz); acá solo el highlight,
           igual que en Home: el gesto no puede existir solo en una de las dos -->
      <div class="mem-chat-foot" style="border-top:${bordeFoot};background:${fondoFoot};transition:background .15s,border-color .15s">
        <!-- el aviso de contexto acompaña al input, no a la cabecera; al pasar a
             memoria el server pone el contador en cero y el aviso se va solo -->
        <${AvisoContexto} pct=${ctxPct} libera=${liberaPct} L=${L}
                          destilando=${destilando} onDestilar=${destilarAhora} estilo="margin-bottom:8px" />
        ${!!draft.trim() && !stored && html`
          <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
            <div role="button" tabindex="0" onClick=${storeDraft}
                 style="height:34px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;gap:7px;font-family:var(--font-mono);font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;cursor:pointer;border:1px solid color-mix(in srgb,var(--color-accent-2) 55%,transparent);color:var(--color-accent-2-700);background:color-mix(in srgb,var(--color-accent-2) 14%,transparent)">${L.tStore}</div>
          </div>`}
        ${stored && html`
          <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
            <span style="height:34px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;gap:7px;font-family:var(--font-mono);font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;background:var(--color-accent-2-700);color:var(--color-bg)">✓ ${L.tSavedToast}</span>
          </div>`}
        <!-- lo elegido en Media viaja con el próximo mensaje del compositor de
             siempre — no hace falta un envío aparte (pedido 2026-08-06) -->
        ${!!seleccionMedia.length && html`
          <div style="display:flex;align-items:center;gap:6px;overflow-x:auto;padding-bottom:8px">
            ${seleccionMedia.map((m) => html`
              <span key=${m.ruta} style="display:inline-flex;align-items:center;gap:5px;height:28px;padding:0 8px 0 4px;border-radius:var(--radius-md);background:var(--color-surface);border:1px solid var(--color-divider);flex-shrink:0;font-size:11px;max-width:170px">
                <span style="width:20px;height:20px;border-radius:var(--radius-md);overflow:hidden;flex-shrink:0;background:var(--color-bg);display:flex;align-items:center;justify-content:center">
                  ${IMG_EXT.test(m.ruta) ? html`<img src="/attach/${m.ruta}" style="width:100%;height:100%;object-fit:cover" />` : "▦"}
                </span>
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${m.titulo || m.ruta.split("/").pop()}</span>
                <span role="button" tabindex="0" class="mem-hit" onClick=${() => toggleSeleccionMedia(m)} style="opacity:.5;cursor:pointer;padding:0 3px">✕</span>
              </span>`)}
            <span role="button" tabindex="0" onClick=${() => setSeleccionMedia([])}
                  style="flex-shrink:0;font-size:10.5px;opacity:.5;cursor:pointer;padding:0 4px">${L.tMediaClear}</span>
          </div>`}
        <!-- lo adjuntado, antes de mandarlo: solo miniatura y ✕ (pedido 2026-08-07) -->
        <${TiraAdjuntos} items=${adjs.items} onQuitar=${adjs.quitar} subiendo=${subiendo} />
        <!-- memorias enlazadas desde "Recuerdos": mismo look que el chip [[…]] ya
             renderizado en la burbuja (.md-entry en app.css) — nunca markup crudo -->
        ${!!enlazadas.length && html`
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding-bottom:8px">
            ${enlazadas.map((e) => html`
              <span key=${e.slug} style="display:inline-flex;align-items:center;gap:6px;font-family:var(--font-mono);font-size:11px;background:var(--color-accent-100,color-mix(in srgb,var(--color-accent) 14%,transparent));color:var(--color-accent-700);padding:2px 9px;border-radius:var(--radius-md)">
                ▤ ${e.titulo}
                <span role="button" tabindex="0" onClick=${() => quitarEnlazada(e.slug)} style="cursor:pointer;opacity:.6">✕</span>
              </span>`)}
          </div>`}
        <!-- "recordar" mientras se escribe: lo que ya está en la memoria y viene
             al caso, en una tira fina — mirar (⊙), agregar (＋) o descartar (✕) -->
        ${phase !== "working" && html`
          <${Recuerdos} draft=${draft} lang=${s.lang} oculto=${oculto} privs=${privsProy} enlazadas=${enlazadas} nunca=${nunca}
                        onEnlazar=${enlazarMemoria} onVer=${setViendoRecuerdo} onNunca=${nuncaRecordar} />`}
        <!-- micrófono · pegar · adjuntar · cámara · escribir · enviar, todo en UNA
             fila (pedido 2026-08-06): los botones a la altura del input, y el ancho
             se lo cede el input. En el celular el input se lleva su propia línea y
             los botones bajan debajo (.mem-comp en ui.js): en un renglón se quedaba
             con ~90px. -->
        <div class="mem-comp">
          ${dictadoSoportado && html`<${MicButton} active=${dictado.activo} onClick=${dictado.toggle} size=${44} />`}
          <${AccionesAdjunto} onPegar=${pegar} onClip=${() => fileRef.current?.click()} onCamara=${abrirCamara}
                              estilo=${BTN_ICONO} tam=${17} lang=${s.lang} />
          <input type="file" multiple ref=${fileRef} style="display:none" onChange=${onArchivo} />
          <input type="file" ref=${camRef} accept="image/*,video/*" capture="environment" style="display:none" onChange=${onArchivo} />
          <div class="mem-comp-in">
            <!-- Ctrl+V de una imagen adjunta el archivo, igual que arrastrarla; si
                 el portapapeles trae texto, el navegador lo pega como siempre. -->
            <textarea ref=${taRef} value=${draft} onInput=${(e) => setDraft(e.target.value)} onKeyDown=${onKey} placeholder=${L.phAsk}
                      onPaste=${(e) => { if (adjs.agregar(archivosDelPortapapeles(e))) e.preventDefault(); }}
                      rows=${Math.min(4, draft.split("\n").length)}
                      style="width:100%;display:block;padding:0;border:0;background:transparent;resize:none;outline:none;font-family:var(--font-body);font-size:16px;line-height:1.45;color:var(--color-text);max-height:96px"></textarea>
          </div>
          <!-- mientras el turno corre el mismo botón detiene: es el sitio donde ya
               está el pulgar, y no hay forma de mandar otro mensaje igual -->
          ${phase === "working"
            ? html`<div role="button" tabindex="0" title=${L.tStop} onClick=${cancelarTurno} class="mem-btn-accent"
                        style=${BTN_ICONO + ";font-size:11px"}>■</div>`
            : html`<div role="button" tabindex="0" onClick=${enviarDraft} class=${draft.trim() || adjs.items.length || seleccionMedia.length ? "mem-btn-accent" : "mem-btn-soft"}
                        style=${BTN_ICONO + ";font-size:18px"}>${subiendo ? "…" : "↑"}</div>`}
        </div>
      </div>
      ${camara && html`
        <${Camara} lang=${s.lang} onClose=${() => setCamara(false)}
                   onListo=${(f) => { adjs.agregar([f]); setCamara(false); }} />`}
      ${sheet === "details" && html`<${DetailsSheet} sid=${sid} meta=${meta} mensajes=${mensajes} onClose=${() => setSheet(null)} onRenombrar=${iniciarRenombre}
                                                    onBorrar=${() => { setSheet(null); setPorBorrar({ id: sid, titulo: meta.titulo }); }}
                                                    onMoverProyecto=${moverProyectoSesion} onGuardar=${destilarAhora} lang=${s.lang} />`}
      ${porBorrar && html`
        <${ConfirmarBorradoSesion} ses=${porBorrar} memorias=${memorias} adjuntos=${adjuntos} lang=${s.lang}
                                   onClose=${() => setPorBorrar(null)} onBorrar=${borrarSesion} />`}
      ${porConfirmar && html`
        <${ConfirmarNube} pedido=${porConfirmar} lang=${s.lang} onResponder=${responderConfirmacion} />`}
      ${viendoRecuerdo && html`
        <${VistaRecuerdo} item=${viendoRecuerdo} lang=${s.lang} onClose=${() => setViendoRecuerdo(null)}
                          onAgregar=${() => enlazarMemoria(viendoRecuerdo)}
                          onNunca=${() => nuncaRecordar(viendoRecuerdo.slug)} />`}
    </div>`;
}
