// AJUSTES — spec §7. Lo real queda interactivo (proveedor, procesar, apariencia,
// idioma); lo no implementado en esta versión se muestra como dato, no como un
// control que finge funcionar (storage/voz/captura/seguridad son de solo lectura
// o locales — ver DESIGN_BRIEF / plan: "decorativos v1").
import { html, useState, useEffect, useRef } from "../../vendor/preact-htm.js";
import { useStore, setState, go, reemplazar, resolverAuto } from "../state.js";
import { dict, PALETTES } from "../i18n.js";
import { VERSION } from "../version.js";
import { get, patch, post, posicion } from "../api.js";
import { Modalidades, invalidarAgentes, useInboxPend, useProcesando, procesarInbox, ScreenHead,
         ChipMenu, BotonAgente, intervaloVisible, menuFijo, useEscape, useSistema,
         cargarInboxPend, useReiniciarServidor } from "../ui.js";
import { verificarSiMovil } from "../privado.js";

// Grilla de widgets: celdas iguales que LLENAN el hueco con TODOS los widgets
// visibles (una sola pantalla, sin scroll). 3:2 es la forma de referencia, pero
// antes que dejar media pantalla vacía la celda se estira dentro de la banda
// AR_MIN..AR_MAX; el contenido se compacta vía container queries (.mem-set-*).
const MAXW = 460;     // tope: con 4 tarjetas en desktop, esto llena el área libre sin desbordar
const MINW_FIT = 120; // ponytail: debajo de esto no se lee ni compacto; ahí sí se scrollea
const AR_MIN = 0.75, AR_MAX = 2.6;  // de 3:4 (columna) a 13:5 (fila ancha)

function useGrid32(n, activo, gap = 9) {
  const ref = useRef(null);
  const [g, setG] = useState({ cols: 2, w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!activo || !el) return;
    const calc = () => {
      const W = el.clientWidth, H = el.clientHeight;
      if (!W || !H) return;
      // gana el reparto de celda más grande: en el celu son filas a todo el ancho
      // (y ahí la tarjeta hasta muestra sus controles extra), en desktop 2×2. La
      // celda toma el hueco entero y solo se recorta el lado que se pasa de la
      // banda, así que ninguna combinación desborda la pantalla.
      let mejor = null;
      for (let cols = 1; cols <= n; cols++) {
        const filas = Math.ceil(n / cols);
        let w = Math.min((W - gap * (cols - 1)) / cols, MAXW);
        let h = (H - gap * (filas - 1)) / filas;
        if (w / h > AR_MAX) w = h * AR_MAX;
        if (w / h < AR_MIN) h = w / AR_MIN;
        if (w >= MINW_FIT && (!mejor || w * h > mejor.w * mejor.h)) mejor = { cols, w, h };
      }
      if (!mejor) { // pantalla enana: celdas legibles y que scrollee
        const cols = Math.max(1, Math.floor((W + gap) / (MINW_FIT + gap)));
        const w = Math.min((W - gap * (cols - 1)) / cols, MAXW);
        mejor = { cols, w, h: w / 1.5 };
      }
      setG({ cols: mejor.cols, w: Math.floor(mejor.w), h: Math.floor(mejor.h) });
    };
    calc();
    const ro = new ResizeObserver(calc);   // cambios del contenedor (p.ej. sidebar)
    ro.observe(el);
    window.addEventListener("resize", calc);  // rotar el celu / redimensionar ventana
    return () => { ro.disconnect(); window.removeEventListener("resize", calc); };
  }, [n, activo]);
  return [ref, g];
}

// passThrough=true: los hijos son solo informativos (sin sus propios controles) y
// un click ahí también debe abrir la categoría. Default false: los hijos tienen
// controles propios (Seg, paleta) que no deben disparar además la navegación.
function Card({ glyph, label, hint, onOpen, passThrough = false, children }) {
  // La celda la fija el grid (3:2 exacto, filas parejas); el contenido se
  // compacta solo, por container queries sobre el ancho real de la celda.
  return html`
    <div role="button" tabindex="0" onClick=${onOpen} class="mem-set-card"
         style="border-radius:var(--radius-md);background:var(--color-surface);cursor:pointer">
      <div class="mem-set-in">
        <div class="mem-set-icon" style="background:var(--color-accent);color:var(--color-bg)">${glyph}</div>
        <div><div class="mem-set-title">${label}</div>
          <div class="mem-set-hint">${hint}</div></div>
        <!-- sin margin-top:auto: el reparto del hueco lo hace .mem-set-in con
             space-between, así el aire cae ENTRE los bloques en vez de acumularse
             en el medio con todo pegado a los bordes (pedido 2026-08-08) -->
        <div style="min-width:0" onClick=${passThrough ? null : (e) => e.stopPropagation()}>${children}</div>
      </div>
    </div>`;
}
function Row({ label, value, children }) {
  // wrap: en el celu el control cae debajo del rótulo en vez de empujar la
  // pantalla a lo ancho (nada de scroll horizontal)
  return html`
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:13px 0;border-top:1px solid var(--color-divider);font-size:14px">
      <span style="flex:1">${label}</span>
      ${value != null ? html`<span style="font-family:var(--font-mono);font-size:11.5px;opacity:.62">${value}</span>` : children}
    </div>`;
}
function Seg({ opciones, valor, onPick }) {
  return html`
    <div style="display:inline-flex;flex-wrap:wrap;border:1px solid var(--color-divider);border-radius:var(--radius-md);overflow:hidden">
      ${opciones.map(([id, label]) => html`
        <div role="button" tabindex="0" onClick=${() => onPick(id)} class=${id === valor ? "mem-btn-accent" : ""}
             style="padding:7px 12px;font-size:12.5px;cursor:pointer;border:0">${label}</div>`)}
    </div>`;
}

// --- Agentes de IA ----------------------------------------------------------
const GLIFOS = ["◈", "◉", "▮", "⌗", "◐", "⌾", "⊢", "▤", "✦", "⚡", "❋", "◭", "⌬"];
const BASE_IDS = ["smart", "cheap"];  // agentes.py: agentes base, nunca borrables
const PROVS = [["anthropic", "Claude API"], ["claude_code", "Claude Code"], ["openai", "Local / OpenAI"]];
// Al cambiar de proveedor, los campos se rellenan con estos valores.
const PREFILL = {
  anthropic: { modelo: "claude-sonnet-5", base_url: "", api_key_env: "ANTHROPIC_API_KEY" },
  claude_code: { modelo: "", base_url: "", api_key_env: "" },
  openai: { modelo: "", base_url: "http://localhost:1234/v1", api_key_env: "" },
};
const TAREAS_L = {
  es: { chat: "Chat", procesar: "Procesar inbox", resumir: "Resumen de sesión" },
  en: { chat: "Chat", procesar: "Inbox processing", resumir: "Session summary" },
};
const INP = "border:1px solid var(--color-divider);border-radius:var(--radius-md);padding:6px 9px;background:var(--color-surface);color:var(--color-text);font-family:var(--font-mono);font-size:11.5px";
const ETIQ = "font-family:var(--font-mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;opacity:.55";

// Rótulo + control. Es también la unidad que .mem-set-cols reparte en columnas
// (por eso el bloque nunca se parte: el break-inside vive en esa clase).
const Grupo = ({ titulo, children }) => html`
  <div><div style="${ETIQ};margin-bottom:7px">${titulo}</div>${children}</div>`;

function Campo({ label, valor, hint, onSave }) {
  const [v, setV] = useState(valor);
  useEffect(() => setV(valor), [valor]);
  return html`
    <${Row} label=${label}>
      <input value=${v} placeholder=${hint || ""} onInput=${(e) => setV(e.target.value)} onBlur=${() => onSave(v)}
             style="width:190px;text-align:right;${INP}" />
    <//>`;
}

// Menú propio en vez de <select>: el nombre del modelo va a la izquierda y sus
// modalidades a la derecha, alineadas en columna. Un <option> nativo solo admite
// una cadena, y ahí el id largo tapaba los glifos (o el select se encogía hasta
// dejar solo la flecha). Reusa las clases del selector de proyecto.
function ModeloPicker({ opciones, valor, en, cargando, onPick }) {
  const [abierto, setAbierto] = useState(false);
  const [lado, setLado] = useState("");
  const actual = opciones.find((m) => m.id === valor);
  useEscape(abierto, () => setAbierto(false));
  // medido contra el viewport como los otros tres menús: con `right:0` fijo, el
  // picker de una tarjeta pegada al borde derecho se salía de la pantalla.
  const alternar = (e) => {
    if (!abierto) setLado(menuFijo(e, 340));
    setAbierto(!abierto);
  };
  return html`
    <span style="position:relative;display:inline-flex;min-width:0;max-width:240px">
      <span role="button" tabindex="0" onClick=${alternar} aria-haspopup="menu" aria-expanded=${abierto}
            style="display:flex;align-items:center;gap:8px;min-width:0;width:100%;cursor:pointer;${INP}">
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${valor || "—"}</span>
        ${actual && html`<${Modalidades} m=${actual} en=${en} />`}
        <span style="flex-shrink:0;opacity:.7;font-size:11px;line-height:1">▾</span>
      </span>
      ${abierto && html`
        <div onClick=${() => setAbierto(false)} class="mem-velo"></div>
        <div class="mem-proy-menu" role="menu" style=${lado}>
          ${!opciones.length && html`
            <div class="mem-proy-item" style="opacity:.5">${cargando ? (en ? "loading…" : "cargando…") : (en ? "no models" : "sin modelos")}</div>`}
          ${opciones.map((m) => html`
            <div role="button" tabindex="0" class="mem-proy-item" onClick=${() => { setAbierto(false); onPick(m.id); }}
                 style="gap:12px;font-family:var(--font-mono);font-size:11.5px;${m.id === valor ? "color:var(--color-accent-700);font-weight:700" : ""}">
              <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${m.id}</span>
              <${Modalidades} m=${m} en=${en} />
            </div>`)}
        </div>`}
    </span>`;
}

// Dropdown con los modelos que el proveedor realmente ofrece (local/claude code);
// si la lista no carga (endpoint local caído, etc.) cae al campo de texto libre.
function ModeloField({ ag, lang, onCambio }) {
  const en = lang === "en";
  const dinamico = ag.proveedor === "openai" || ag.proveedor === "claude_code";
  const [opciones, setOpciones] = useState(null);   // null = todavía cargando
  useEffect(() => {
    if (!dinamico) { setOpciones(null); return; }
    setOpciones(null);
    get(`/provider/models?agente=${encodeURIComponent(ag.id)}`)
      // un servidor viejo devuelve strings en vez de {id,in,out}: sin esto la
      // lista salía con los nombres en blanco y todo "▤→▤".
      .then((r) => setOpciones((r.modelos || []).map((m) => (typeof m === "string" ? { id: m } : m))))
      .catch(() => setOpciones([]));
  }, [ag.id, ag.proveedor, ag.base_url]);

  // El picker desde el primer render: antes salía el campo de texto mientras
  // cargaba la lista y mutaba a dropdown al llegar la respuesta, que se leía
  // como un control que cambia solo al pasarle el cursor.
  if (dinamico && (opciones === null || opciones.length)) {
    return html`
      <${Row} label=${en ? "Model" : "Modelo"}>
        <${ModeloPicker} opciones=${opciones || []} valor=${ag.modelo} en=${en} cargando=${opciones === null}
                         onPick=${(id) => onCambio({ modelo: id })} />
      <//>`;
  }
  return html`
    <${Campo} label=${en ? "Model" : "Modelo"} valor=${ag.modelo}
              hint=${ag.proveedor === "claude_code" ? (en ? "empty = CLI default" : "vacío = default del CLI") : ""}
              onSave=${(v) => onCambio({ modelo: v })} />`;
}

function EditorAgente({ ag, lang, onCambio, onBorrar }) {
  const en = lang === "en";
  const protegido = BASE_IDS.includes(ag.id);
  const [prompt, setPrompt] = useState(ag.system_prompt || "");
  const [test, setTest] = useState(null);
  useEffect(() => setPrompt(ag.system_prompt || ""), [ag.id]);
  async function probar() {
    setTest({ probando: true });
    try { setTest(await get(`/provider/test?agente=${encodeURIComponent(ag.id)}`)); }
    catch (e) { setTest({ ok: false, detalle: String(e) }); }
  }
  return html`
    <div style="padding:2px 0 14px">
      <${Campo} label=${en ? "Name" : "Nombre"} valor=${ag.nombre} onSave=${(v) => onCambio({ nombre: v })} />
      <div style="padding:10px 0;border-top:1px solid var(--color-divider);display:flex;align-items:center;gap:10px">
        <span style="${ETIQ};flex-shrink:0">${en ? "Icon" : "Icono"}</span>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          ${GLIFOS.map((g) => html`
            <div role="button" tabindex="0" onClick=${() => onCambio({ icono: g })}
                 style="width:30px;height:30px;flex-shrink:0;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:14px;line-height:1;border:1px solid ${ag.icono === g ? "var(--color-accent)" : "var(--color-divider)"};background:${ag.icono === g ? "var(--color-accent)" : "transparent"};color:${ag.icono === g ? "var(--color-bg)" : "inherit"};opacity:${ag.icono === g ? 1 : 0.6}">${g}</div>`)}
        </div>
      </div>
      <div style="padding:10px 0;border-top:1px solid var(--color-divider)">
        <${Seg} opciones=${PROVS} valor=${ag.proveedor} onPick=${(p) => onCambio({ proveedor: p, ...PREFILL[p] })} />
      </div>
      ${ag.proveedor === "openai" && html`
        <${Campo} label="Base URL" valor=${ag.base_url} hint="http://localhost:1234/v1" onSave=${(v) => onCambio({ base_url: v })} />`}
      <${ModeloField} ag=${ag} lang=${lang} onCambio=${onCambio} />
      ${ag.proveedor !== "claude_code" && html`
        <${Campo} label=${en ? "API key" : "API key"} valor=${ag.api_key_env}
                  hint=${ag.proveedor === "openai" ? (en ? "env var, optional" : "variable env, opcional") : "ANTHROPIC_API_KEY"}
                  onSave=${(v) => onCambio({ api_key_env: v })} />`}
      <div style="padding:10px 0;border-top:1px solid var(--color-divider)">
        <textarea rows="2" value=${prompt} onInput=${(e) => setPrompt(e.target.value)}
                  onBlur=${() => onCambio({ system_prompt: prompt })}
                  placeholder=${en ? "System prompt (optional)" : "System prompt (opcional)"}
                  style="width:100%;resize:vertical;font-size:12.5px;font-family:inherit;line-height:1.4;${INP}"></textarea>
      </div>
      <div style="display:flex;gap:8px;margin-top:2px">
        <div role="button" tabindex="0" onClick=${test?.probando ? null : probar}
             style="flex:1;height:38px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;justify-content:center;font-size:13px;cursor:pointer;opacity:${test?.probando ? 0.6 : 1}">
          ${test?.probando ? "…" : (en ? "Test" : "Probar")}
        </div>
        ${!protegido && html`
          <div role="button" tabindex="0" onClick=${onBorrar}
               style="height:38px;padding:0 14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;justify-content:center;font-size:13px;cursor:pointer;color:var(--color-accent-700)">
            ${en ? "Delete" : "Eliminar"}
          </div>`}
      </div>
      ${test && !test.probando && html`
        <div style="margin-top:6px;font-size:12px;font-family:var(--font-mono);color:${test.ok ? "var(--color-ok)" : "var(--color-accent-700)"}">
          ${test.ok ? `✓ ${test.respuesta}` : `⚠ ${test.detalle}`}
        </div>`}
      ${protegido && html`
        <div style="font-size:11px;opacity:.45;padding:6px 0 0">${en ? "Base agent — can't be deleted." : "Agente base — no se puede borrar."}</div>`}
    </div>`;
}

function CatAI({ lang, initialSel }) {
  const en = lang === "en";
  const [data, setData] = useState(null);   // {agentes, asignaciones, tareas}
  const [sel, setSel] = useState(initialSel || null); // agente resaltado; null = el de chat
  const [vista, setVista] = useState("agentes"); // "agentes" | "tareas" — separadas a propósito
  const [err, setErr] = useState(null);
  useEffect(() => { get("/agents").then(setData).catch((e) => setErr(String(e))); }, []);
  useEffect(() => { if (initialSel) setSel(initialSel); }, [initialSel]);  // deep link con la pantalla ya abierta
  if (err) return html`
    <div style="padding:16px 0;font-size:12.5px;line-height:1.6;color:var(--color-accent-700)">
      ⚠ ${err}<br /><span style="opacity:.7">${en ? "Restart the server (mem serve) to load the new /agents endpoint." : "Reinicia el servidor (mem serve) para cargar el endpoint /agents nuevo."}</span></div>`;
  if (!data) return html`<div style="padding:16px 0;font-size:12.5px;opacity:.5">…</div>`;

  async function persistir(next) {
    setData(next);
    try { await post("/agents", { agentes: next.agentes, asignaciones: next.asignaciones }); invalidarAgentes(); } catch {}
  }
  const cambiar = (id, cambios) =>
    persistir({ ...data, agentes: data.agentes.map((a) => (a.id === id ? { ...a, ...cambios } : a)) });
  function nuevo() {
    const id = `ag-${Date.now().toString(36)}`;
    persistir({ ...data, agentes: [...data.agentes, {
      id, nombre: en ? "New agent" : "Nuevo agente", icono: GLIFOS[data.agentes.length % GLIFOS.length],
      proveedor: "anthropic", system_prompt: "", ...PREFILL.anthropic }] });
    setSel(id);
  }
  function borrar(id) {
    persistir({ ...data, agentes: data.agentes.filter((a) => a.id !== id),
      asignaciones: Object.fromEntries(Object.entries(data.asignaciones).filter(([, v]) => v !== id)) });
    setSel(null);
  }
  const porId = (id) => data.agentes.find((a) => a.id === id) || data.agentes[0];
  // siempre hay uno resaltado: el elegido, si no el que atiende el chat
  const activo = porId(sel || data.asignaciones?.chat);

  return html`
    <div style="padding-top:6px;margin-bottom:4px">
      <${Seg} opciones=${[["agentes", en ? "Agents" : "Agentes"], ["tareas", en ? "Per task" : "Por tarea"]]}
              valor=${vista} onPick=${setVista} />
    </div>

    ${vista === "agentes" && html`
      <div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;padding:6px 0 12px">
          <div role="button" tabindex="0" class="mem-ag-btn mem-ag-nuevo" onClick=${nuevo}
               title=${en ? "New agent" : "Nuevo agente"}>＋</div>
          ${data.agentes.map((a) => html`
            <div role="button" tabindex="0" class="mem-ag-btn ${a.id === activo.id ? "on" : ""}" onClick=${() => setSel(a.id)}>
              <span class="mem-ag-glyph">${a.icono}</span>
              <span style="min-width:0">
                <span class="mem-ag-nombre">${a.nombre}</span>
                <span class="mem-ag-modelo">${a.modelo || (PROVS.find(([p]) => p === a.proveedor) || [])[1] || a.proveedor}</span>
              </span>
            </div>`)}
        </div>
        <${EditorAgente} ag=${activo} lang=${lang} onCambio=${(c) => cambiar(activo.id, c)}
                         onBorrar=${() => borrar(activo.id)} />
      </div>`}

    ${vista === "tareas" && html`
      <div>
        <div style="font-size:12px;opacity:.5;line-height:1.5;padding:4px 0 10px">
          ${en ? "Which agent handles each part of the app that calls an LLM." : "Qué agente atiende cada parte de la app que usa un LLM."}</div>
        ${data.tareas.map((t) => html`
          <${Row} label=${TAREAS_L[en ? "en" : "es"][t] || t}>
            <select value=${porId(data.asignaciones[t]).id} class="mem-select"
                    onChange=${(e) => persistir({ ...data, asignaciones: { ...data.asignaciones, [t]: e.target.value } })}
                    style="max-width:190px">
              ${data.agentes.map((a) => html`<option value=${a.id}>${a.icono} ${a.nombre}</option>`)}
            </select>
          <//>`)}
      </div>`}`;
}

const RANGOS_LOG = [["1", "1h"], ["24", "24h"], ["168", "7d"]]; // valor = horas

function CatProcessing({ lang, cfg, onCfg }) {
  const en = lang === "en";
  const pend = useInboxPend();
  const { procesando } = useProcesando();
  const [resultado, setResultado] = useState(null);
  const [rango, setRango] = useState("1");
  const [lineas, setLineas] = useState(null);
  const [health, setHealth] = useState(null);
  const inerte = !procesando && !pend;
  const bloqueado = procesando || inerte;
  const cargarHealth = () => fetch("/health").then((r) => r.json()).then(setHealth).catch(() => setHealth({ ok: false }));
  useEffect(() => { cargarHealth(); }, []);

  useEffect(() => {
    const cargar = () => get(`/log?horas=${rango}`).then((r) => setLineas(r.lineas)).catch(() => setLineas([]));
    cargar();
    return intervaloVisible(cargar, 2000); // "tiempo real": refresco corto, sin infra de streaming
  }, [rango]);

  async function procesarAhora() {
    if (bloqueado) return;
    setResultado(null);
    const r = await procesarInbox();
    if (r) setResultado(r);
  }

  return html`
    <div style="padding:13px 0 0;flex:1;display:flex;flex-direction:column;min-height:0">
      <div role="button" tabindex="0" onClick=${bloqueado ? null : procesarAhora}
           class=${procesando ? "mem-btn-procesando" : "mem-btn-accent"}
           style="flex-shrink:0;height:44px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:13.5px;font-weight:600;cursor:${bloqueado ? "default" : "pointer"};opacity:${procesando ? 0.65 : inerte ? 0.45 : 1};margin-bottom:8px">
        ${procesando ? (en ? "processing…" : "procesando…") : (en ? "Process inbox now" : "Procesar inbox ahora")}
      </div>
      ${resultado && html`<div style="flex-shrink:0;font-size:12.5px;font-family:var(--font-mono);opacity:.7;margin-bottom:10px">${resultado.procesadas} ${en ? "processed" : "procesadas"} · ${resultado.errores} ${en ? "errors" : "con error"}</div>`}
      <div style="flex-shrink:0;display:flex;align-items:center;gap:10px;margin:6px 0 8px">
        <span style="${ETIQ}">${en ? "System log" : "Log del sistema"}</span>
        <span style="margin-left:auto"><${Seg} opciones=${RANGOS_LOG} valor=${rango} onPick=${setRango} /></span>
      </div>
      <pre style="flex:1;min-height:140px;margin:0;padding:10px;border-radius:var(--radius-md);background:color-mix(in srgb,var(--color-text) 5%,transparent);font-size:11px;font-family:var(--font-mono);overflow:auto;white-space:pre-wrap">${lineas === null ? "…" : lineas.length ? lineas.join("\n") : (en ? "(empty)" : "(vacío)")}</pre>

      <!-- Almacenamiento vive acá (pedido 2026-08-04): carpeta, estado y reinicio.
           En columnas donde hay ancho: son cinco bloques angostos que en fila
           única sumaban media pantalla de scroll para nada. -->
      <div class="mem-set-cols" style="flex-shrink:0;border-top:1px solid var(--color-divider);margin-top:14px;padding-top:6px">
        <${CarpetaAlmacen} cfg=${cfg} onCfg=${onCfg} lang=${lang} />
        <${Backup} lang=${lang} />
        <${IndiceBusqueda} cfg=${cfg} onCfg=${onCfg} lang=${lang} />
        <${GeoCaptura} lang=${lang} />
        <div>
          <${Row} label=${en ? "Server" : "Servidor"} value=${health === null ? "…" : health.ok ? (en ? "connected" : "conectado") : (en ? "unreachable" : "sin conexión")} />
          <${BotonReiniciarServidor} lang=${lang} onListo=${cargarHealth} />
        </div>
      </div>
    </div>`;
}

// --- Media: generación de imagen / video / audio ---------------------------
// Todo lo que decide QUIÉN genera: servicios locales (ComfyUI, LM Studio) con
// arranque y log, el CLI de Higgsfield, backend preferido por tipo y modelos por
// defecto. Las preferencias van al prompt del modo media (crear.preferencias).
const TIPOS_MEDIA = ["imagen", "video", "audio"];
const TIPO_L = { imagen: ["Imagen", "Image"], video: ["Video", "Video"], audio: ["Audio", "Audio"] };
const TIPO_API = { imagen: "image", video: "video", audio: "audio" };   // como los nombra Higgsfield
const BACKEND_L = { comfyui: ["Local", "Local"], higgsfield: ["Nube", "Cloud"], preguntar: ["Preguntar", "Ask"] };
// minutos sin uso tras los que se suelta la VRAM (config.idle_minutos; 0 = nunca)
const OCIOSOS = [["0", ["Nunca", "Never"]], ["5", ["5 min", "5 min"]], ["15", ["15 min", "15 min"]],
                 ["30", ["30 min", "30 min"]], ["60", ["1 h", "1 h"]]];

function Barra({ etiq, pct, texto }) {
  const p = Math.max(0, Math.min(100, pct || 0));   // sin datos todavía: barra en cero, no NaN
  return html`
    <div style="padding:5px 0">
      <div style="display:flex;gap:8px;font-size:11px;margin-bottom:3px;align-items:baseline">
        <span style="font-weight:600">${etiq}</span>
        <span style="margin-left:auto;font-family:var(--font-mono);opacity:.6">${texto}</span>
      </div>
      <div style="height:6px;border-radius:var(--radius-md);overflow:hidden;background:color-mix(in srgb,var(--color-text) 10%,transparent)">
        <div style="height:100%;border-radius:var(--radius-md);width:${p}%;transition:width .4s ease;background:${p >= 90 ? "var(--color-accent-700)" : "var(--color-accent)"}"></div>
      </div>
    </div>`;
}

// Nace de un problema real: generar video local necesita casi toda la VRAM y LM
// Studio deja su modelo residente ~12 GB aunque esté ocioso. Sin esto el único
// síntoma es un OOM quince minutos después de disparar. El hook vive en ui.js
// desde 2026-08-11, que es cuando el bloque de la máquina llegó también a Home.
// Acá va "en vivo" (3s, el default): esta es la pantalla donde se está mirando.
//
// Mientras no llegó la primera lectura se dibuja TODO en cero (pedido 2026-08-07):
// antes el panel no existía hasta el primer /system y la sección aparecía de golpe
// unos segundos después de abrir Ajustes. El primer % de CPU también viene en null
// (es un delta entre dos llamadas), así que la barra tampoco puede depender de él.
const SIN_DATOS = { cpu: 0, ram: { total: 0, libre: 0 }, modelos: [],
                    gpus: [{ nombre: "", vram_usada: 0, vram_total: 0, uso: 0 }] };
const GB = (b, d = 1) => ((b || 0) / 1024 ** 3).toFixed(d);

/** Las mismas barras en una línea de ~40 caracteres, para la tarjeta de la grilla.
 *  Con `s` en null sale en cero, igual que el panel. */
function resumenMaquina(s, en) {
  const g = s?.gpus?.[0];
  const usada = (s?.ram?.total || 0) - (s?.ram?.libre || 0);
  const generando = s?.modelos?.some((m) => m.servicio === "ComfyUI");
  return [`CPU ${Math.round(s?.cpu || 0)}%`,
          `RAM ${GB(usada, 0)}/${GB(s?.ram?.total, 0)}`,
          `VRAM ${GB(g?.vram_usada)}/${GB(g?.vram_total)}`,
          generando ? (en ? "generating" : "generando") : ""].filter(Boolean).join(" · ");
}

function Recursos({ en, svc }) {
  const datos = useSistema();
  const s = datos || SIN_DATOS;
  const [haciendo, setHaciendo] = useState("");   // acción en curso: bloquea su botón
  const [aviso, setAviso] = useState("");
  async function accion(id, ruta) {
    setHaciendo(id); setAviso("");
    try { setAviso((await post(ruta)).mensaje || "ok"); }
    catch (e) { setAviso(String(e.message || e)); }
    setHaciendo("");
  }

  const usadaRam = s.ram?.total ? s.ram.total - s.ram.libre : 0;
  const generando = s.modelos?.some((m) => m.servicio === "ComfyUI");
  const enLM = s.modelos?.some((m) => m.servicio === "LM Studio");
  const btn = (id, ruta, texto, fuerte) => html`
    <div role="button" tabindex="0" onClick=${haciendo ? null : () => accion(id, ruta)}
         class=${fuerte ? "mem-btn-accent" : ""}
         style="padding:6px 12px;border-radius:var(--radius-md);cursor:pointer;flex-shrink:0;font-size:11px;opacity:${haciendo === id ? 0.5 : 1}${fuerte ? "" : ";border:1px solid var(--color-divider)"}">
      ${haciendo === id ? "…" : texto}</div>`;
  return html`
    <div style="${ETIQ};margin:6px 0 2px">${en ? "The machine right now" : "La máquina ahora"}</div>
    <${Barra} etiq="CPU" pct=${s.cpu} texto=${`${Math.round(s.cpu || 0)}%`} />
    <${Barra} etiq="RAM" pct=${100 * usadaRam / (s.ram?.total || 1)}
              texto=${`${GB(usadaRam)} / ${GB(s.ram?.total)} GB`} />
    ${(s.gpus || []).map((g) => html`
      <${Barra} etiq=${g.nombre ? `VRAM · ${g.nombre.replace(/^NVIDIA /, "")}` : "VRAM"}
                pct=${100 * g.vram_usada / (g.vram_total || 1)}
                texto=${`${GB(g.vram_usada)} / ${GB(g.vram_total)} GB · ${en ? "GPU" : "uso"} ${Math.round(g.uso || 0)}%${g.temp ? ` · ${g.temp.toFixed(0)}°` : ""}`} />`)}
    <div style="font-size:11px;line-height:1.65;padding:7px 0 2px">
      ${!datos
        ? html`<div style="opacity:.5">…</div>`
        : s.modelos?.length
        ? s.modelos.map((m) => html`
            <div><span class="mem-status-dot" style="width:6px;height:6px;box-shadow:none"></span>
              <b>${m.servicio}</b> — ${m.nombre}${m.detalle ? ` · ${m.detalle}` : ""}</div>`)
        : html`<div style="opacity:.6">${en ? "No model loaded in LM Studio." : "Ningún modelo cargado en LM Studio."}</div>`}
      <div style="opacity:.45;font-size:10px;padding-top:3px">
        ${en ? "ComfyUI doesn't report which model it holds; its share shows in the VRAM bar."
             : "ComfyUI no informa qué modelo tiene cargado; su parte se ve en la barra de VRAM."}</div>
    </div>
    <!-- Soltar la VRAM sin apagar el servicio: los dos recargan solos cuando les
         toca, así que lo único que cuesta es releer el modelo. Detener existe
         porque un clip de video son 12-40 min y antes la única salida era matar
         ComfyUI y perder también el modelo cargado. -->
    ${(generando || svc?.comfyui || enLM) && html`
      <div style="display:flex;gap:8px;flex-wrap:wrap;padding:4px 0 2px">
        ${generando && btn("stop", "/services/comfyui/interrupt", en ? "Stop generation" : "Detener generación", true)}
        ${svc?.comfyui && btn("vram", "/services/comfyui/unload", en ? "Free VRAM · ComfyUI" : "Liberar VRAM · ComfyUI")}
        ${enLM && btn("lms", "/services/lmstudio/unload", en ? "Unload · LM Studio" : "Descargar · LM Studio")}
      </div>`}
    ${!!aviso && html`
      <div style="font-size:11px;font-family:var(--font-mono);opacity:.6;padding:2px 0 4px">${aviso}</div>`}`;
}

// Poner y sacar de la máquina lo que MeM usa pero no trae: LM Studio, Ollama,
// ComfyUI. El motor es winget (mem/componentes.py) y el catálogo es
// componentes.json, el mismo que lee install.ps1 — agregar un componente es
// agregarlo ahí, sin tocar esta pantalla.
//
// Tres estados y no dos: "instalado a mano" (ComfyUI clonado a pulso, LM Studio
// puesto por su .exe) no ofrece desinstalar, porque winget no puede sacar lo que
// no puso y el error que devuelve no se entiende.
function Componentes({ lang }) {
  const en = lang === "en";
  const [datos, setDatos] = useState(null);   // {winget, componentes:{...}}
  const [err, setErr] = useState("");
  const [confirmar, setConfirmar] = useState("");  // id esperando el segundo toque

  const cargar = () => get("/components").then(setDatos).catch(() => setDatos(null));
  const lista = Object.values(datos?.componentes || {});
  const hayTrabajo = lista.some((c) => c.trabajo?.activo);
  useEffect(() => {
    cargar();
    // winget tarda minutos y no avisa cuando termina: mientras hay algo corriendo
    // se mira seguido, y quieto lo justo para notar un cambio hecho por fuera.
    return intervaloVisible(cargar, hayTrabajo ? 2500 : 15000);
  }, [hayTrabajo]);

  async function accion(id, que) {
    setErr(""); setConfirmar("");
    try { await post(`/components/${id}/${que}`); } catch (e) { setErr(String(e.message || e)); }
    cargar();
  }

  const btn = (txt, onClick, fuerte) => html`
    <div role="button" tabindex="0" onClick=${onClick} class=${fuerte ? "mem-btn-accent" : ""}
         style="height:30px;padding:0 13px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:12px;cursor:pointer;flex-shrink:0${fuerte ? "" : ";border:1px solid var(--color-divider)"}">${txt}</div>`;

  return html`
    <${Grupo} titulo=${en ? "Components" : "Componentes"}>
      ${datos && !datos.winget && html`
        <div style="font-size:12px;line-height:1.5;color:var(--color-accent-700);padding:0 0 8px">
          ${en ? "winget not found — install 'App Installer' from the Microsoft Store to manage components from here."
               : "no encuentro winget — se instala 'Instalador de aplicaciones' desde la Microsoft Store para manejar componentes desde acá."}</div>`}
      ${datos === null && html`<div style="font-size:12px;opacity:.5;padding:4px 0">…</div>`}
      ${lista.map((c) => {
        const trabajando = c.trabajo?.activo;
        return html`
          <div key=${c.id} style="padding:11px 0;border-top:1px solid var(--color-divider)">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="flex:1;min-width:0">
                <span style="font-size:14px;font-weight:600">${c.nombre}</span>
                <span style="display:block;font-family:var(--font-mono);font-size:9.5px;opacity:.55;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                  ${trabajando ? (en ? "working…" : "trabajando…")
                    : c.instalacion === "winget" ? `${en ? "installed" : "instalado"} · ${c.winget}`
                    : c.instalacion === "manual" ? (en ? "installed by hand — remove it the way you put it"
                                                       : "instalado a mano — se saca por donde se puso")
                    : c.winget}</span>
              </span>
              ${!trabajando && datos?.winget && c.instalacion === "no"
                && btn(en ? "Install" : "Instalar", () => accion(c.id, "install"), true)}
              ${!trabajando && c.instalacion === "winget" && (confirmar === c.id
                ? btn(en ? "Tap again to remove" : "Tocá de nuevo para sacarlo", () => accion(c.id, "uninstall"))
                : btn(en ? "Uninstall" : "Desinstalar", () => {
                    setConfirmar(c.id);
                    setTimeout(() => setConfirmar((v) => (v === c.id ? "" : v)), 4000);
                  }))}
            </div>
            ${c.instalacion === "no" && !trabajando && html`
              <div style="font-size:11.5px;opacity:.5;line-height:1.45;padding-top:4px">${c.que_es?.[en ? "en" : "es"] || ""}</div>`}
            <!-- el progreso de winget: sin esto, una instalación que falla es un
                 botón que no cambia nunca y nada que mirar -->
            ${c.trabajo && html`
              <pre style="margin:6px 0 0;padding:8px;border-radius:var(--radius-md);max-height:110px;overflow:auto;background:color-mix(in srgb,var(--color-text) 5%,transparent);font-size:10px;font-family:var(--font-mono);white-space:pre-wrap">${c.trabajo.log.join("\n") || "…"}</pre>`}
          </div>`;
      })}
      ${err && html`<div style="margin:8px 0 0;font-size:12px;font-family:var(--font-mono);color:var(--color-accent-700)">⚠ ${err}</div>`}
      <div style="margin-top:9px;font-size:11.5px;opacity:.5;line-height:1.5">
        ${en ? "Installs and removals go through winget; Windows may ask for permission on the PC. The same list is what install.ps1 offers on a clean machine."
             : "Instalar y sacar van por winget; Windows puede pedir permiso en la PC. Es la misma lista que ofrece install.ps1 en una máquina limpia."}</div>
    <//>`;
}

function CatMedia({ lang, cfg, onCfg }) {
  const en = lang === "en";
  const [svc, setSvc] = useState(null);             // {comfyui, lmstudio, higgsfield}
  const [arrancando, setArrancando] = useState(""); // servicio arrancando (bloquea botones)
  const [logLineas, setLogLineas] = useState(null);
  const [workflows, setWorkflows] = useState([]);
  const [nubeModelos, setNubeModelos] = useState([]);   // catálogo de Higgsfield
  const [err, setErr] = useState("");
  const backends = Object.entries(BACKEND_L).map(([id, l]) => [id, l[en ? 1 : 0]]);

  const cargarSvc = () => get("/services").then(setSvc).catch(() => setSvc(null));
  useEffect(() => {
    cargarSvc();
    get("/media/workflows").then((r) => setWorkflows(r.detalle || [])).catch(() => {});
    // sin CLI o sin sesión vuelve vacío: el desplegable queda con lo ya guardado
    get("/media/cloud-models").then((r) => setNubeModelos(r.modelos || [])).catch(() => {});
    return intervaloVisible(cargarSvc, 8000);
  }, []);
  useEffect(() => {
    // la consola que ya no está: lo que ComfyUI escribe cuando lo arranca MeM
    const cargar = () => get("/services/comfyui/log?n=120").then((r) => setLogLineas(r.lineas)).catch(() => setLogLineas([]));
    cargar();
    return intervaloVisible(cargar, 3000);
  }, []);

  async function arrancar(nombre) {
    setArrancando(nombre); setErr("");
    try { await post(`/services/${nombre}/start`); } catch (e) { setErr(String(e.message || e)); }
    setArrancando("");
    cargarSvc();
  }
  async function guardarCfg(cambios) {
    try { onCfg(await patch("/config", cambios)); } catch (e) { setErr(String(e.message || e)); }
  }

  const dot = (on) => html`<span class="mem-status-dot ${on ? "" : "off"}"></span>`;
  const fila = (icono, label, hint, extra) => html`
    <div style="display:flex;align-items:center;gap:10px;padding:11px 0;border-top:1px solid var(--color-divider)">
      ${icono}
      <span style="flex:1;min-width:0"><span style="font-size:14px;font-weight:600">${label}</span>
        <span style="display:block;font-family:var(--font-mono);font-size:9.5px;opacity:.55;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${hint}</span></span>
      ${extra}
    </div>`;
  const btnArrancar = (nombre) => svc && !svc[nombre] && html`
    <div role="button" tabindex="0" onClick=${arrancando ? null : () => arrancar(nombre)} class="mem-btn-accent"
         style="height:30px;padding:0 13px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:12px;cursor:pointer;flex-shrink:0;opacity:${arrancando === nombre ? 0.6 : 1}">
      ${arrancando === nombre ? (en ? "starting…" : "arrancando…") : (en ? "Start" : "Arrancar")}</div>`;

  return html`
    <div class="mem-set-cols" style="padding-top:4px">
      <div><${Recursos} en=${en} svc=${svc} /></div>

      <!-- La versión automática de los botones de arriba: acordarse de soltar la
           VRAM a mano no escala, y el OOM aparece recién al generar (2026-08-07). -->
      <${Grupo} titulo=${en ? "Release VRAM when idle" : "Soltar la VRAM al quedar ocioso"}>
        <${Row} label=${en ? "Idle for" : "Sin usar un modelo local durante"}>
          <${Seg} opciones=${OCIOSOS.map(([v, l]) => [v, l[en ? 1 : 0]])} valor=${String(cfg?.idle_minutos ?? 15)}
                  onPick=${(v) => guardarCfg({ idle_minutos: Number(v) })} />
        <//>
        <div style="font-size:12px;opacity:.5;line-height:1.5;padding:6px 0 0">
          ${en ? "Unloads LM Studio and asks ComfyUI to free its models; both reload on their next request. Generating counts as use."
               : "Descarga LM Studio y le pide a ComfyUI que suelte los suyos; los dos recargan en su próximo pedido. Generar cuenta como uso."}</div>
      <//>

      <${Grupo} titulo=${en ? "Local services" : "Servicios locales"}>
        ${fila(dot(!!svc?.comfyui), "ComfyUI", svc?.comfyui ? (en ? "running · " : "corriendo · ") + (cfg?.comfy_url || "") : (cfg?.comfy_url || ""), btnArrancar("comfyui"))}
        ${fila(dot(!!svc?.lmstudio), "LM Studio", svc?.lmstudio ? (en ? "running · " : "corriendo · ") + (cfg?.base_url || "") : (cfg?.base_url || ""), btnArrancar("lmstudio"))}
        ${fila(dot(!!svc?.higgsfield), "Higgsfield",
               svc?.higgsfield ? (en ? "CLI installed · Diego's account, uses plan credits" : "CLI instalado · cuenta de Diego, gasta créditos del plan")
                               : "npm i -g @higgsfield/cli · higgsfield auth login")}
        ${err && html`<div style="margin:6px 0;font-size:12px;font-family:var(--font-mono);color:var(--color-accent-700)">⚠ ${err}</div>`}
      <//>

      <${Componentes} lang=${lang} />

      <${Grupo} titulo=${en ? "Preferred backend per type" : "Backend preferido por tipo"}>
        ${TIPOS_MEDIA.map((t) => html`
          <${Row} label=${TIPO_L[t][en ? 1 : 0]}>
            <${Seg} opciones=${backends} valor=${cfg?.[`media_${t}`] || "preguntar"}
                    onPick=${(id) => guardarCfg({ [`media_${t}`]: id })} />
          <//>`)}
        <div style="font-size:12px;opacity:.5;line-height:1.5;padding:6px 0 0">
          ${en ? "Guides the media-mode agent; an explicit ask in the conversation wins."
               : "Guía al agente del modo media; un pedido explícito en la conversación manda."}</div>
      <//>

      <${Grupo} titulo=${en ? "Default models" : "Modelos por defecto"}>
      <${Row} label=${en ? "ComfyUI workflow (local)" : "Workflow ComfyUI (local)"}>
        <!-- Solo los de IMAGEN: esto fija el workflow por defecto de crear_imagen, y
             ofrecer acá uno de video daba un fallo ilegible ("Invalid image file:
             %imagen%") porque los de video parten de un primer fotograma. El tipo
             lo decide el backend por el nodo que guarda la salida, no por el nombre. -->
        <select value=${cfg?.comfy_workflow || "imagen"} class="mem-select"
                onChange=${(e) => guardarCfg({ comfy_workflow: e.target.value })} style="max-width:190px">
          ${[...new Set([cfg?.comfy_workflow || "imagen",
                         ...workflows.filter((w) => w.tipo === "imagen" && !w.necesita_imagen).map((w) => w.nombre)])]
            .map((w) => html`<option value=${w}>${w}</option>`)}
        </select>
      <//>
      <!-- Desplegable y no campo libre: con la nube elegida y este job_type vacío,
           cada generación le costaba al modelo tres pasos (listar el catálogo,
           elegir, generar) antes de arrancar — y ahí se perdía (2026-08-07).
           El catálogo lo trae el propio CLI de Higgsfield. -->
      ${TIPOS_MEDIA.map((t) => html`
        <${Row} label=${`Higgsfield · ${TIPO_L[t][en ? 1 : 0].toLowerCase()}`}>
          <select value=${cfg?.[`higgs_${t}`] || ""} class="mem-select"
                  onChange=${(e) => guardarCfg({ [`higgs_${t}`]: e.target.value })} style="max-width:190px">
            <option value="">${en ? "— pick in chat —" : "— elegir en el chat —"}</option>
            ${(cfg?.[`higgs_${t}`] && !nubeModelos.some((m) => m.job_type === cfg[`higgs_${t}`])
               ? [{ job_type: cfg[`higgs_${t}`], nombre: cfg[`higgs_${t}`], tipo: TIPO_API[t] }] : [])
              .concat(nubeModelos.filter((m) => m.tipo === TIPO_API[t]))
              .map((m) => html`<option value=${m.job_type}>${m.nombre}</option>`)}
          </select>
        <//>`)}
      <//>

      <${Grupo} titulo="ComfyUI">
        <${Campo} label="URL" valor=${cfg?.comfy_url || ""} hint="http://127.0.0.1:8188"
                  onSave=${(v) => guardarCfg({ comfy_url: v })} />
        <${Campo} label=${en ? "Launch command" : "Comando de arranque"} valor=${cfg?.comfy_cmd || ""}
                  hint="W:\\ComfyUI\\arrancar.bat" onSave=${(v) => guardarCfg({ comfy_cmd: v })} />
      <//>

      <${Grupo} titulo=${en ? "ComfyUI log" : "Log de ComfyUI"}>
        <div style="font-family:var(--font-mono);font-size:9.5px;opacity:.5;margin:-3px 0 6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">%LOCALAPPDATA%\\MeM\\comfyui.log</div>
        <pre style="min-height:120px;max-height:300px;margin:0;padding:10px;border-radius:var(--radius-md);background:color-mix(in srgb,var(--color-text) 5%,transparent);font-size:11px;font-family:var(--font-mono);overflow:auto;white-space:pre-wrap">${logLineas === null ? "…" : logLineas.length ? logLineas.join("\n") : (en ? "(empty — fills up when MeM launches ComfyUI)" : "(vacío — se llena cuando MeM arranca ComfyUI)")}</pre>
      <//>
    </div>`;
}

const RADIO_IDS = ["recto", "suave", "redondo"];
const BORDE_IDS = ["sutil", "normal", "marcado"];
const BURBUJA_IDS = ["llena", "contorno", "tinte", "minima"];
const FUENTE_IDS = ["archivo", "inter", "serif", "sistema"];
const LETRAS_ESTILO = ["A", "B", "C"];
// id -> nombre traducido, para el subtítulo de un estilo guardado ("paleta ·
// burbuja · fuente") sin repetir la lista es/en de cada knob.
const nombreDe = (ids, names, id) => names[ids.indexOf(id)] || id;

function CatLook({ s, lang, cfg, onCfg }) {
  const L = dict(lang);
  const en = lang === "en";
  const segPref = (ids, names, campo) => html`
    <${Seg} opciones=${ids.map((id, i) => [id, names[i]])} valor=${s[campo]} onPick=${(id) => setState({ [campo]: id })} />`;

  const guardados = cfg?.estilos_guardados || [];
  const idsPal = PALETTES.map((p) => p[0]);
  /** "Guardar acá" captura el estado VIVO de este device en el slot idx —
   *  reemplaza la lista entera (PATCH /config no mergea arrays). */
  async function guardarEnSlot(idx) {
    const actual = { nombre: `${en ? "Style" : "Estilo"} ${LETRAS_ESTILO[idx]}`, palette: s.palette,
                      themePref: s.themePref, burbuja: s.burbuja, radio: s.radio, borde: s.borde, fuente: s.fuente };
    const lista = [...guardados];
    while (lista.length <= idx) lista.push({ nombre: "" });   // slots salteados: "vacío" hasta que se guarden
    lista[idx] = actual;
    try { onCfg(await patch("/config", { estilos_guardados: lista })); } catch {}
  }
  function aplicarSlot(g) {
    if (!g?.nombre) return;
    setState({ palette: g.palette, themePref: g.themePref, theme: resolverAuto(g.themePref),
               burbuja: g.burbuja, radio: g.radio, borde: g.borde, fuente: g.fuente });
  }

  return html`
    <div class="mem-set-cols" style="padding-top:6px">
      <${Grupo} titulo=${`${L.themeLabels[2]} / ${L.themeLabels[0]} / ${L.themeLabels[1]}`}>
        <${Seg} opciones=${[["light", L.themeLabels[0]], ["dark", L.themeLabels[1]], ["auto", L.themeLabels[2]]]} valor=${s.themePref}
                onPick=${(id) => setState({ themePref: id, theme: resolverAuto(id) })} />
      <//>

      <${Grupo} titulo=${en ? "Palette" : "Paleta"}>
        <!-- auto-fill y no 4 fijas: en una columna angosta entran 3 y en el celu
             siguen siendo 4, sin que la ficha se estruje -->
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(76px,1fr));gap:7px">
          ${PALETTES.map(([id, c1, c2], i) => {
            const on = s.palette === id;
            return html`
              <div key=${id} role="button" tabindex="0" onClick=${() => setState({ palette: id })}
                   style="border-radius:var(--radius-md);padding:7px;cursor:pointer;border:1px solid ${on ? "var(--color-accent)" : "var(--color-divider)"};background:${on ? "color-mix(in srgb,var(--color-accent) 10%,transparent)" : "transparent"}">
                <div style="height:18px;border-radius:var(--radius-sm);background:${c1};margin-bottom:4px"></div>
                <div style="height:7px;border-radius:var(--radius-sm);background:${c2};margin-bottom:3px"></div>
                <div style="font-size:9.5px;font-family:var(--font-mono);text-transform:uppercase;text-align:center">${L.paletteNames[i]}</div>
              </div>`;
          })}
        </div>
      <//>

      <${Grupo} titulo=${L.tBurbuja}>${segPref(BURBUJA_IDS, L.burbujaNames, "burbuja")}<//>
      <${Grupo} titulo=${L.tRadio}>${segPref(RADIO_IDS, L.radioNames, "radio")}<//>
      <${Grupo} titulo=${L.tBorde}>${segPref(BORDE_IDS, L.bordeNames, "borde")}<//>
      <${Grupo} titulo=${L.tFuente}>${segPref(FUENTE_IDS, L.fuenteNames, "fuente")}<//>

      <!-- vista previa viva: mismas clases que el chat/markdown de verdad, así que
           cualquier knob de arriba se ve acá al toque, sin aplicar-y-mirar
           (pedido 2026-08-09). El chip usa .md-entry pero inerte (href vacío,
           preventDefault): no hay memoria "preview" real que abrir. -->
      <${Grupo} titulo=${L.tPreview}>
        <div style="padding:12px;border:1px solid var(--color-divider);border-radius:var(--radius-md);background:var(--color-bg);display:flex;flex-direction:column;gap:8px">
          <div class="mem-burb-mem" style="align-self:flex-start;font-size:14px">
            ${en ? "That reminds me of " : "Eso me recuerda a "}<span class="md" style="display:inline-block"><a class="md-entry" href="#" onClick=${(e) => e.preventDefault()}>▤ ${en ? "a linked memory" : "una memoria enlazada"}</a></span>
          </div>
          <div class="mem-yo mem-burb-yo" style="align-self:flex-end;font-size:14px">${en ? "Looks good like this." : "Se ve bien así."}</div>
          <div role="button" tabindex="0" class="mem-btn-accent"
               style="align-self:flex-start;height:32px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:12.5px;cursor:default">${en ? "A button" : "Un botón"}</div>
        </div>
      <//>

      <${Grupo} titulo=${L.tStyles}>
        <div style="display:flex;flex-direction:column;gap:6px">
          ${LETRAS_ESTILO.map((letra, idx) => {
            const g = guardados[idx];
            const lleno = !!g?.nombre;
            return html`
              <div key=${letra} style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--color-divider);border-radius:var(--radius-md)">
                <span style="width:22px;height:22px;flex-shrink:0;border-radius:var(--radius-sm);background:color-mix(in srgb,var(--color-accent) 16%,transparent);display:flex;align-items:center;justify-content:center;font-family:var(--font-mono);font-size:11px;font-weight:700">${letra}</span>
                <span style="flex:1;min-width:0">
                  <span style="display:block;font-size:12.5px;font-weight:600">${en ? "Style" : "Estilo"} ${letra}</span>
                  <span style="display:block;font-family:var(--font-mono);font-size:9px;opacity:.55;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                    ${lleno ? `${nombreDe(idsPal, L.paletteNames, g.palette)} · ${nombreDe(BURBUJA_IDS, L.burbujaNames, g.burbuja)} · ${nombreDe(FUENTE_IDS, L.fuenteNames, g.fuente)}` : L.tStyleEmpty}</span>
                </span>
                ${lleno && html`
                  <div role="button" tabindex="0" onClick=${() => aplicarSlot(g)} class="mem-btn-accent"
                       style="height:26px;padding:0 10px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:11px;cursor:pointer;flex-shrink:0">${L.tStyleApply}</div>`}
                <div role="button" tabindex="0" onClick=${() => guardarEnSlot(idx)}
                     style="height:26px;padding:0 10px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;font-size:11px;cursor:pointer;flex-shrink:0">${L.tStyleSaveHere}</div>
              </div>`;
          })}
        </div>
      <//>

      <${Grupo} titulo=${L.tUiLang}>
        <${Seg} opciones=${[["es", "Español"], ["en", "English"]]} valor=${s.lang} onPick=${(id) => setState({ lang: id })} />
      <//>
      <${Grupo} titulo=${L.tVoiceLang}>
        <${Seg} opciones=${[["es-ES", "Español"], ["en-US", "English"]]} valor=${s.voiceLang} onPick=${(id) => setState({ voiceLang: id })} />
        <div style="margin-top:8px;font-size:12px;opacity:.5;line-height:1.5">
          ${lang === "en" ? "Dictation uses your browser's speech recognition; original audio is not kept."
                          : "El dictado usa el reconocimiento de voz del navegador; el audio original no se conserva."}</div>
      <//>
    </div>`;
}

// idx = posición en L.setCats. Cuatro categorías (pedido 2026-08-04): idioma y
// voz viven dentro de UX/UI, almacenamiento dentro de Procesamiento.
const CATS = [
  { key: "ai", glyph: "◈", idx: 0 }, { key: "media", glyph: "◇", idx: 1 },
  { key: "processing", glyph: "⊢", idx: 2 }, { key: "look", glyph: "◐", idx: 3 },
];

function BotonProcesar({ lang }) {
  const pend = useInboxPend();
  const { procesando, ultimoLog } = useProcesando();
  const [resultado, setResultado] = useState(null);
  const inerte = !procesando && !pend;
  const bloqueado = procesando || inerte;

  async function correr() {
    if (bloqueado) return;
    setResultado(null);
    const r = await procesarInbox();
    if (r) setResultado(r);
  }
  const label = procesando ? "…"
    : resultado ? `${resultado.procesadas} ✓${resultado.errores ? ` · ${resultado.errores} ⚠` : ""}`
    : (lang === "en" ? "Process now" : "Procesar ahora");
  return html`
    <div style="display:flex;flex-direction:column;gap:5px;min-width:0">
      <div role="button" tabindex="0" onClick=${bloqueado ? null : correr}
           class=${procesando ? "mem-btn-procesando" : "mem-btn-accent"}
           style="display:inline-flex;align-items:center;justify-content:center;height:28px;padding:0 12px;border-radius:var(--radius-md);font-size:11.5px;font-weight:600;cursor:${bloqueado ? "default" : "pointer"};opacity:${procesando ? 0.65 : inerte ? 0.45 : 1}">${label}</div>
      ${ultimoLog && html`
        <div class="mem-set-chico" style="font-family:var(--font-mono);font-size:9px;opacity:.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
          ${ultimoLog.replace(/^##\s*\[[^\]]*\]\s*/, "")}</div>`}
    </div>`;
}

function CarpetaAlmacen({ cfg, onCfg, lang }) {
  const en = lang === "en";
  const [v, setV] = useState(cfg?.hamuq || "");
  const [msg, setMsg] = useState(null);
  useEffect(() => setV(cfg?.hamuq || ""), [cfg?.hamuq]);
  async function guardar() {
    if (!v || v === cfg?.hamuq) return;
    try { onCfg(await patch("/config", { hamuq: v })); setMsg({ ok: true }); }
    catch (e) { setMsg({ ok: false, detalle: String(e.message || e) }); }
  }
  return html`
    <div style="padding:14px 0 0">
      <div style="${ETIQ};margin-bottom:8px">${en ? "Storage folder (hamuQ base)" : "Carpeta de almacenamiento (base hamuQ)"}</div>
      <input value=${v} onInput=${(e) => setV(e.target.value)} onBlur=${guardar}
             onKeyDown=${(e) => e.key === "Enter" && e.target.blur()}
             style="width:100%;${INP}" />
      ${msg && html`
        <div style="margin-top:8px;font-size:12px;font-family:var(--font-mono);color:${msg.ok ? "var(--color-ok)" : "var(--color-accent-700)"}">
          ${msg.ok ? (en ? "✓ folder changed" : "✓ carpeta cambiada") : `⚠ ${msg.detalle}`}</div>`}
      <div style="margin-top:10px;font-size:12px;opacity:.5;line-height:1.5">
        ${en ? "Point it to another existing folder to switch bases." : "Apunta a otra carpeta existente para cambiar de base."}</div>
    </div>`;
}

// Geolocalización de las capturas: dónde estaba Diego al anotar algo. NO hay
// ajuste propio a propósito — el permiso del navegador ES el interruptor (y es
// el único lugar donde revocarlo funciona de verdad). Acá solo se ve el estado y
// se puede conceder de antemano, para que el permiso no salte a mitad de una captura.
function GeoCaptura({ lang }) {
  const en = lang === "en";
  const [estado, setEstado] = useState("…");   // granted | denied | prompt | no
  const mirar = () => {
    if (!navigator.geolocation) return setEstado("no");
    if (!navigator.permissions?.query) return setEstado("prompt");
    navigator.permissions.query({ name: "geolocation" })
      .then((p) => { setEstado(p.state); p.onchange = () => setEstado(p.state); })
      .catch(() => setEstado("prompt"));
  };
  useEffect(mirar, []);
  const TXT = {
    granted: en ? "✓ captures record where you were" : "✓ las capturas guardan dónde estabas",
    denied: en ? "✕ denied — grant it in the browser's site settings"
               : "✕ denegado — se concede en los permisos del sitio, en el navegador",
    prompt: en ? "· not granted yet" : "· todavía sin conceder",
    no: en ? "· unavailable (needs https or localhost)" : "· no disponible (requiere https o localhost)",
    "…": "…",
  };
  return html`
    <div style="padding:14px 0 0;border-top:1px solid var(--color-divider);margin-top:14px">
      <div style="${ETIQ};margin-bottom:8px">${en ? "Capture location" : "Lugar de captura"}</div>
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        ${estado === "prompt" && html`
          <div role="button" tabindex="0" onClick=${() => posicion().then(mirar)}
               style="height:36px;padding:0 16px;display:inline-flex;align-items:center;font-size:13px;cursor:pointer;border:1px solid var(--color-divider);background:var(--color-surface)">
            ${en ? "◈ Allow location" : "◈ Permitir ubicación"}
          </div>`}
        <span style="font-size:12px;color:${estado === "granted" ? "var(--color-accent-2-700)" : "var(--text-3)"}">${TXT[estado] || estado}</span>
      </div>
      <div style="margin-top:8px;font-size:11.5px;opacity:.5">
        ${en ? "Stored per memory as coordinates plus a place name; shown on the map as ◇. Revoke it in the browser to stop."
             : "Se guarda en cada memoria como coordenadas más el nombre del lugar; en el mapa son los ◇. Para cortarlo, revocar el permiso en el navegador."}
      </div>
    </div>`;
}

// Backup fuera del vault (pedido 2026-08-12): sacar lo capturado a una carpeta
// de la máquina y poder volver a meterlo. La carpeta es del disco de la PC —
// el server escribe y lee ahí — aunque esto se toque desde el celular; por eso
// exportar pide huella antes: es toda la memoria de un tirón, y el celular es
// el aparato que se presta. La misma carpeta sirve para importar: el server
// busca adentro el backup más nuevo.
const CARPETA_BACKUP = "mem.backup.carpeta";

function Backup({ lang }) {
  const en = lang === "en";
  const [v, setV] = useState(() => localStorage.getItem(CARPETA_BACKUP) || "");
  const [fase, setFase] = useState("idle");   // idle | exportando | importando | listo | fallo
  const [msg, setMsg] = useState("");
  const ocupado = fase === "exportando" || fase === "importando";

  async function correr(accion) {
    const carpeta = v.trim();
    if (ocupado || !carpeta) return;
    localStorage.setItem(CARPETA_BACKUP, carpeta);
    if (accion === "exportando" && !(await verificarSiMovil())) {
      setFase("fallo"); setMsg(en ? "verification cancelled" : "verificación cancelada"); return;
    }
    setFase(accion); setMsg("");
    try {
      const r = await post(accion === "exportando" ? "/export" : "/import", { carpeta });
      setFase("listo");
      if (accion === "exportando") {
        setMsg(`${r.memorias} ${en ? "memories" : "memorias"} · ${r.sesiones} ${en ? "sessions" : "sesiones"} · ${r.adjuntos} ${en ? "files" : "adjuntos"} → ${r.carpeta}`);
      } else {
        setMsg([`${r.nuevas} ${en ? "new" : "nuevas"}`, `${r.fusionadas} ${en ? "merged" : "fusionadas"}`,
                `${r.iguales} ${en ? "already there" : "ya estaban"}`, `${r.sesiones} ${en ? "sessions" : "sesiones"}`,
                r.adjuntos_faltantes && `${r.adjuntos_faltantes} ${en ? "files missing" : "adjuntos faltantes"}`,
                r.errores && `${r.errores} ${en ? "failed" : "con error"}`].filter(Boolean).join(" · "));
        cargarInboxPend();   // lo importado espera en el inbox: que el botón de arriba se entere
      }
    } catch (e) { setFase("fallo"); setMsg(String(e.message || e)); }
  }

  const BTN = (accion, txt) => html`
    <div role="button" tabindex="0" onClick=${() => correr(accion)}
         style="height:40px;padding:0 16px;border-radius:var(--radius-md);display:inline-flex;align-items:center;gap:8px;font-size:13.5px;cursor:${ocupado || !v.trim() ? "default" : "pointer"};border:1px solid var(--color-divider);background:var(--color-surface);opacity:${ocupado || !v.trim() ? 0.5 : 1}">
      ${fase === accion ? (en ? "working…" : "trabajando…") : txt}
    </div>`;

  return html`
    <div style="padding:14px 0 0;border-top:1px solid var(--color-divider);margin-top:14px">
      <div style="${ETIQ};margin-bottom:8px">${en ? "Backup" : "Backup"}</div>
      <input value=${v} onInput=${(e) => setV(e.target.value)}
             placeholder=${en ? "folder on this PC" : "carpeta de esta PC"}
             onKeyDown=${(e) => e.key === "Enter" && e.target.blur()} style="width:100%;${INP}" />
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
        ${BTN("exportando", en ? "⇪ Export memory" : "⇪ Exportar memoria")}
        ${BTN("importando", en ? "⇩ Import" : "⇩ Importar")}
      </div>
      ${msg && html`<div style="margin-top:9px;font-size:12px;font-family:var(--font-mono);line-height:1.5;word-break:break-all;color:${fase === "fallo" ? "var(--color-accent-700)" : "var(--color-ok)"}">${fase === "fallo" ? "⚠ " : "✓ "}${msg}</div>`}
      <div style="margin-top:8px;font-size:11.5px;opacity:.5">
        ${en ? "Copies what was captured — no titles, summaries or history: those come back when it is processed again. Importing sends the new ones to the inbox and merges anything the vault already knows."
             : "Copia lo que se capturó — sin títulos, síntesis ni registro: eso vuelve a salir al procesarlo de nuevo. Importar manda lo nuevo al inbox y fusiona lo que el vault ya conoce."}
      </div>
    </div>`;
}

// Índice de búsqueda híbrida (mem.db): reconstrucción manual + modelo de
// embeddings. La primera vez descarga el modelo (~120 MB), por eso el botón
// avisa que puede tardar; después reindexar 50-500 entradas es cosa de segundos.
function IndiceBusqueda({ cfg, onCfg, lang }) {
  const en = lang === "en";
  const [v, setV] = useState(cfg?.embed_modelo || "");
  const [fase, setFase] = useState("idle"); // idle | corriendo | listo | fallo
  const [stats, setStats] = useState(null);
  useEffect(() => setV(cfg?.embed_modelo || ""), [cfg?.embed_modelo]);
  async function guardarModelo() {
    if (!v || v === cfg?.embed_modelo) return;
    try { onCfg(await patch("/config", { embed_modelo: v })); } catch { /* queda el anterior */ }
  }
  async function reindexar() {
    if (fase === "corriendo") return;
    setFase("corriendo"); setStats(null);
    try {
      const r = await post("/memory/reindex");
      if (r.error) { setFase("fallo"); setStats(r.error); return; }
      setStats(`${r.entradas} ${en ? "entries" : "entradas"} · ${r.con_embedding} ${en ? "embedded" : "con embedding"} · ${r.geocodificados} ${en ? "places" : "lugares"} · ${(r.ms / 1000).toFixed(1)} s`);
      setFase("listo");
      try { onCfg(await get("/config")); } catch { /* la línea de estado se refresca sola al reabrir */ }
    } catch (e) { setFase("fallo"); setStats(String(e.message || e)); }
  }
  return html`
    <div style="padding:14px 0 0;border-top:1px solid var(--color-divider);margin-top:14px">
      <div style="${ETIQ};margin-bottom:8px">${en ? "Search index (hybrid)" : "Índice de búsqueda (híbrida)"}</div>
      <input value=${v} onInput=${(e) => setV(e.target.value)} onBlur=${guardarModelo}
             onKeyDown=${(e) => e.key === "Enter" && e.target.blur()} style="width:100%;${INP}" />
      <div style="margin-top:10px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <div role="button" tabindex="0" onClick=${fase === "corriendo" ? null : reindexar}
             style="height:40px;padding:0 18px;border-radius:var(--radius-md);display:inline-flex;align-items:center;gap:8px;font-size:13.5px;cursor:${fase === "corriendo" ? "default" : "pointer"};border:1px solid var(--color-divider);background:var(--color-surface);opacity:${fase === "corriendo" ? 0.7 : 1}">
          ${fase === "corriendo" ? (en ? "indexing…" : "indexando…") : (en ? "⌕ Rebuild search index" : "⌕ Reindexar búsqueda")}
        </div>
        ${stats && html`<span style="font-size:12px;font-family:var(--font-mono);color:${fase === "fallo" ? "var(--color-accent-700)" : "var(--color-ok)"}">${fase === "fallo" ? "⚠ " : "✓ "}${stats}</span>`}
      </div>
      ${cfg?.busqueda && html`<div style="margin-top:8px;font-size:12px;color:${cfg.busqueda.hibrida ? "var(--color-ok)" : "var(--color-accent-700)"}">
        ${cfg.busqueda.hibrida ? (en ? "✓ hybrid search active" : "✓ búsqueda híbrida activa")
                               : `⚠ ${en ? "lexical-only" : "solo léxica"}: ${cfg.busqueda.motivo}`}
      </div>`}
      <div style="margin-top:8px;font-size:11.5px;opacity:.5">
        ${en ? "First run downloads the embedding model (~120 MB); search works lexical-only until then."
             : "La primera vez descarga el modelo de embeddings (~120 MB); hasta entonces la búsqueda es solo léxica."}
      </div>
    </div>`;
}

// Reinicio remoto del servidor (POST /server/restart) — sirve desde el móvil.
// Dos toques (el segundo confirma) y luego poll a /health hasta que el proceso
// nuevo conteste: el viejo muere <1 s después de responder.
function BotonReiniciarServidor({ lang, onListo }) {
  const en = lang === "en";
  const { fase, iniciar } = useReiniciarServidor(onListo);
  const txt = {
    idle: en ? "⟳ Restart server" : "⟳ Reiniciar servidor",
    confirmar: en ? "Tap again to confirm" : "Toca de nuevo para confirmar",
    reiniciando: en ? "restarting…" : "reiniciando…",
    listo: en ? "✓ server is back" : "✓ servidor de vuelta",
    fallo: en ? "did not come back — check the tray on the PC" : "no volvió — mirar el tray en el PC",
  }[fase];
  const clic = fase === "idle" || fase === "confirmar" ? iniciar : undefined;
  return html`
    <div style="margin-top:18px">
      <div role="button" tabindex="0" onClick=${clic}
           style="height:40px;padding:0 18px;border-radius:var(--radius-md);display:inline-flex;align-items:center;gap:8px;font-size:13.5px;cursor:${clic ? "pointer" : "default"};border:1px solid ${fase === "confirmar" ? "var(--color-accent)" : "var(--color-divider)"};background:${fase === "confirmar" ? "color-mix(in srgb,var(--color-accent) 14%,transparent)" : "var(--color-surface)"};opacity:${fase === "reiniciando" ? 0.7 : 1}">
        ${txt}
      </div>
      <div style="margin-top:8px;font-size:11.5px;opacity:.5">
        ${en ? "Applies new code without touching the PC. The tray icon adopts the new process."
             : "Aplica código nuevo sin tocar el PC. El icono de bandeja adopta el proceso nuevo."}
      </div>
    </div>`;
}

export function Settings() {
  const s = useStore();
  const L = dict(s.lang);
  const en = s.lang === "en";
  // La categoría abierta viaja en la ruta (#settings/<cat>) para que el back del
  // sistema haga lo mismo que el botón. Deep link desde el chip de un agente
  // (home/sidebar): "agent:<id>" = categoría ai con ese agente resaltado.
  const param = s.param || "";
  const agentParam = param.startsWith("agent:") ? param.slice(6) : null;
  const cat = agentParam ? "ai" : (CATS.some((c) => c.key === param) ? param : null);
  const [cfg, setCfg] = useState(null);
  const [agentsData, setAgentsData] = useState(null); // {agentes, asignaciones}
  const [provOk, setProvOk] = useState(null);  // null=probando · true/false
  const [gref, grid] = useGrid32(CATS.length, !cat);
  const [svc, setSvc] = useState(null);        // /services para la tarjeta Media
  const [vServer, setVServer] = useState(0);   // versión del código que corre el SERVER
  const sis = useSistema(!cat);                // solo mientras se ve la grilla
  const pendInbox = useInboxPend();            // para la tarjeta de Procesamiento

  useEffect(() => {
    // los .js salen del disco (siempre los últimos) y el Python es el del arranque:
    // si el server no se reinició, la app se porta como una versión vieja y no se
    // nota (2026-08-07: un video pedido "en higgsfield" salió por ComfyUI)
    get("/health").then((h) => setVServer(h.version || 0)).catch(() => {});
    get("/config").then(setCfg).catch(() => setCfg(null));  // sin server no revienta la pantalla
    get("/services").then(setSvc).catch(() => {});
    get("/agents").then((d) => {
      setAgentsData(d);
      const ag = d.agentes.find((a) => a.id === d.asignaciones?.chat) || d.agentes[0];
      return get(`/provider/test?agente=${encodeURIComponent(ag.id)}`);
    }).then((r) => setProvOk(!!(r && r.ok))).catch(() => setProvOk(false));
  }, []);
  // guardar un ajuste desde la tarjeta (sin entrar a la categoría)
  const cambiarCfg = async (cambios) => { try { setCfg(await patch("/config", cambios)); } catch { /* queda como estaba */ } };
  const agPrin = agentsData ? (agentsData.agentes.find((a) => a.id === agentsData.asignaciones?.chat) || agentsData.agentes[0]) : null;
  const agSmart = agentsData?.agentes.find((a) => a.id === "smart");
  const agCheap = agentsData?.agentes.find((a) => a.id === "cheap");

  if (cat) {
    const c = CATS.find((x) => x.key === cat);
    const [label] = L.setCats[c.idx];
    return html`
      <div class="mem-screen ancha" style="flex:1;display:flex;flex-direction:column;animation:scIn .38s cubic-bezier(.22,1,.36,1);min-height:0">
        <!-- back directo a Home; para moverse entre categorías está el strip de
             abajo (reemplazar: cambiar de pestaña no apila historial) -->
        <${ScreenHead} titulo=${label} onBack=${() => go("home")} />
        <!-- saltar a otra categoría: tira donde hay ancho, dropdown en el celu -->
        <div style="display:flex;gap:6px;flex-shrink:0;padding:4px 22px 2px;flex-wrap:wrap">
          <span class="mem-solo-ancho" style="display:flex;gap:6px;flex-wrap:wrap">
            ${CATS.map((x) => html`
              <span role="button" tabindex="0" onClick=${() => reemplazar("settings", x.key)}
                    class="mem-proy-chip ${x.key === cat ? "on" : ""}" style="height:28px;flex-shrink:0;max-width:none">
                ${x.glyph} ${L.setCats[x.idx][0]}</span>`)}
          </span>
          <span class="mem-solo-angosto">
            <${ChipMenu} etiqueta=${`${c.glyph} ${label}`} on=${true} estilo="height:28px" ancho=${230}
                         items=${CATS.map((x) => ({ id: x.key, glyph: x.glyph, label: L.setCats[x.idx][0],
                                                    sub: L.setCats[x.idx][1], on: x.key === cat }))}
                         onPick=${(k) => reemplazar("settings", k)} />
          </span>
        </div>
        <div style="flex:1;overflow:auto;padding:8px 22px 40px">
          ${cat === "ai" && html`<${CatAI} lang=${s.lang} initialSel=${agentParam} />`}
          ${cat === "media" && html`<${CatMedia} lang=${s.lang} cfg=${cfg} onCfg=${setCfg} />`}
          ${cat === "processing" && html`<${CatProcessing} lang=${s.lang} cfg=${cfg} onCfg=${setCfg} />`}
          ${cat === "look" && html`<${CatLook} s=${s} lang=${s.lang} cfg=${cfg} onCfg=${setCfg} />`}
        </div>
      </div>`;
  }

  return html`
    <div class="mem-screen ancha" style="flex:1;display:flex;flex-direction:column;animation:scIn .38s cubic-bezier(.22,1,.36,1);min-height:0">
      <${ScreenHead} titulo=${L.tSettings} onBack=${() => go("home")} />
      <div class="mem-set-wrap">
        <div ref=${gref} style="flex:1;min-height:0;overflow:auto">
        <div style="display:grid;grid-template-columns:repeat(${grid.cols},${grid.w}px);grid-auto-rows:${grid.h}px;gap:9px;justify-content:center;align-content:center;min-height:100%;visibility:${grid.w ? "visible" : "hidden"}">
          ${CATS.map((c) => {
            const [label, hint] = L.setCats[c.idx];
            return html`
              <${Card} glyph=${c.glyph} label=${label} hint=${hint} onOpen=${() => go("settings", c.key)}>
                ${c.key === "ai" && html`
                  <div style="display:flex;flex-direction:column;gap:5px;min-width:0">
                    <div style="display:flex;align-items:center;gap:6px;min-width:0">
                      <span style="font-size:11px;padding:3px 8px;border-radius:var(--radius-md);background:color-mix(in srgb,var(--color-accent) 16%,transparent);color:var(--color-accent-700);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${agPrin ? `${agPrin.icono} ${agPrin.nombre}` : "…"}</span>
                      <span title=${provOk == null ? "probando…" : provOk ? "conexión ok" : "sin conexión"}
                            style="flex-shrink:0;width:8px;height:8px;border-radius:var(--radius-md);background:${provOk == null ? "color-mix(in srgb,var(--color-text) 25%,transparent)" : provOk ? "var(--color-ok)" : "var(--color-accent)"}"></span>
                      <!-- con ancho de sobra, el agente del chat se cambia acá mismo -->
                      <span class="mem-set-extra"><${BotonAgente} lang=${s.lang} /></span>
                    </div>
                    <!-- el resumen de una línea solo mientras la lista de abajo no
                         entre: con las dos, la tarjeta repite lo mismo dos veces -->
                    <div class="mem-set-chico mem-set-nomas" style="font-family:var(--font-mono);font-size:9px;opacity:.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                      ${agSmart ? `✦ ${agSmart.modelo || "—"}` : ""}${agCheap ? ` · ⚡ ${agCheap.modelo || "—"}` : ""}</div>
                    <!-- con celda grande, los agentes con su modelo y un clic que
                         entra directo a ESE agente: hasta acá había que abrir la
                         categoría para ver siquiera cuáles hay (pedido 2026-08-08) -->
                    <div class="mem-set-mas">
                      ${(agentsData?.agentes || []).slice(0, 4).map((a) => html`
                        <div key=${a.id} role="button" tabindex="0" onClick=${() => go("settings", `agent:${a.id}`)}
                             style="display:flex;align-items:center;gap:7px;min-width:0;padding:6px 0;border-top:1px solid var(--color-divider);cursor:pointer">
                          <span style="flex-shrink:0;font-size:11px;color:var(--color-accent)">${a.icono}</span>
                          <span style="flex-shrink:0;font-size:11px;font-weight:600">${a.nombre}</span>
                          <span style="flex:1;min-width:0;text-align:right;font-family:var(--font-mono);font-size:9.5px;color:var(--text-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${a.modelo || "—"}</span>
                        </div>`)}
                    </div>
                  </div>`}
                ${c.key === "media" && html`
                  <div style="display:flex;flex-direction:column;gap:5px;min-width:0">
                    <div style="display:flex;flex-wrap:wrap;gap:5px;min-width:0">
                      ${[["comfyui", "comfy"], ["lmstudio", "lms"], ["higgsfield", "higgs"]].map(([k, label]) => html`
                        <span style="display:inline-flex;align-items:center;gap:5px;padding:3px 8px;border-radius:var(--radius-md);border:1px solid var(--color-divider);font-family:var(--font-mono);font-size:9px;letter-spacing:.07em;text-transform:uppercase;color:var(--text-2)">
                          <span class="mem-status-dot ${svc?.[k] ? "" : "off"}" style="width:6px;height:6px;box-shadow:none"></span>${label}</span>`)}
                    </div>
                    <!-- la máquina, resumida: es el dato que decide si conviene
                         generar en local o mandarlo a la nube (pedido 2026-08-07) -->
                    <div class="mem-set-chico" style="font-family:var(--font-mono);font-size:9px;opacity:.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                      ${resumenMaquina(sis, en)}</div>
                    <!-- quién genera cada tipo, sin entrar a la categoría -->
                    <div class="mem-set-extra">
                      ${TIPOS_MEDIA.map((t) => html`
                        <${ChipMenu} etiqueta=${`${TIPO_L[t][en ? 1 : 0].slice(0, 3)} ${BACKEND_L[cfg?.[`media_${t}`] || "preguntar"][en ? 1 : 0]}`}
                                     ancho=${190} estilo="height:22px;font-size:9px"
                                     items=${Object.entries(BACKEND_L).map(([id, l]) => ({ id, label: l[en ? 1 : 0],
                                       on: (cfg?.[`media_${t}`] || "preguntar") === id }))}
                                     onPick=${(id) => cambiarCfg({ [`media_${t}`]: id })} />`)}
                    </div>
                    <!-- qué hay cargado en la máquina AHORA: es lo que decide si
                         conviene generar local, y ya viene en el mismo /system -->
                    <div class="mem-set-mas">
                      ${(sis?.modelos || []).slice(0, 3).map((m) => html`
                        <div key=${`${m.servicio}-${m.nombre}`} style="display:flex;align-items:center;gap:7px;min-width:0;padding:6px 0;border-top:1px solid var(--color-divider);font-family:var(--font-mono);font-size:9.5px">
                          <span class="mem-status-dot" style="width:6px;height:6px"></span>
                          <span style="flex-shrink:0;color:var(--text-2)">${m.servicio}</span>
                          <span style="flex:1;min-width:0;text-align:right;color:var(--text-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${m.nombre}</span>
                        </div>`)}
                      ${!sis?.modelos?.length && html`
                        <div style="padding:6px 0;border-top:1px solid var(--color-divider);font-family:var(--font-mono);font-size:9.5px;color:var(--text-3)">
                          ${en ? "no model loaded" : "ningún modelo cargado"}</div>`}
                    </div>
                  </div>`}
                ${c.key === "processing" && html`
                  <div style="display:flex;flex-direction:column;gap:6px;min-width:0">
                    <${BotonProcesar} lang=${s.lang} />
                    <!-- el estado del triaje, sin entrar: cuántas esperan y desde
                         cuándo corre el server (pedido 2026-08-08) -->
                    <div class="mem-set-mas">
                      <div style="display:flex;align-items:center;gap:7px;padding:6px 0;border-top:1px solid var(--color-divider);font-family:var(--font-mono);font-size:9.5px">
                        <span style="color:var(--text-2)">${en ? "in queue" : "en cola"}</span>
                        <span style="flex:1;text-align:right;color:${pendInbox ? "var(--color-accent)" : "var(--text-3)"}">${pendInbox || 0}</span>
                      </div>
                      <div style="display:flex;align-items:center;gap:7px;padding:6px 0;border-top:1px solid var(--color-divider);font-family:var(--font-mono);font-size:9.5px">
                        <span style="color:var(--text-2)">${en ? "server" : "servidor"}</span>
                        <span style="flex:1;text-align:right;color:${vServer && vServer !== VERSION.n ? "var(--color-accent)" : "var(--text-3)"}">${vServer ? `v${vServer}` : "—"}</span>
                      </div>
                    </div>
                  </div>`}
                ${c.key === "look" && html`
                  <div style="display:flex;flex-direction:column;gap:6px;min-width:0">
                    <div class="mem-set-nomas" style="display:flex;flex-wrap:wrap;gap:4px">${PALETTES.map(([id, c1]) => html`<span style="width:14px;height:14px;border-radius:var(--radius-md);background:${c1};border:${s.palette === id ? "2px solid var(--color-accent-700)" : "1px solid transparent"};cursor:pointer" onClick=${() => setState({ palette: id })}></span>`)}</div>
                    <div class="mem-set-extra">
                      <${Seg} opciones=${[["light", "☀"], ["dark", "☾"], ["auto", L.themeLabels[2]]]} valor=${s.themePref}
                              onPick=${(id) => setState({ themePref: id, theme: resolverAuto(id) })} />
                      <${ChipMenu} etiqueta=${s.lang === "en" ? "EN" : "ES"} ancho=${150} estilo="height:22px"
                                   items=${[["es", "Español"], ["en", "English"]].map(([id, l]) => ({ id, label: l, on: s.lang === id }))}
                                   onPick=${(id) => setState({ lang: id })} />
                    </div>
                    <!-- con sitio, la paleta se elige por NOMBRE y con sus tres
                         tonos a la vista, no adivinando cuál es cada cuadradito.
                         En GRILLA y no en filas: doce filas rayadas llenaban la
                         tarjeta de arriba abajo mientras las otras tres respiran
                         (pedido 2026-08-11); así son tres líneas. -->
                    <div class="mem-set-mas mem-set-pal">
                      ${PALETTES.map(([id, c1, c2, c3]) => html`
                        <div key=${id} role="button" tabindex="0" title=${id} onClick=${() => setState({ palette: id })}
                             style="display:flex;align-items:center;gap:5px;min-width:0;padding:3px 5px;border-radius:var(--radius-md);cursor:pointer;border:1px solid ${s.palette === id ? "var(--color-accent)" : "transparent"};background:${s.palette === id ? "color-mix(in srgb,var(--color-accent) 10%,transparent)" : "transparent"};font-family:var(--font-mono);font-size:9px;letter-spacing:.05em;text-transform:uppercase;color:${s.palette === id ? "var(--color-text)" : "var(--text-3)"}">
                          <span style="flex-shrink:0;display:flex;border-radius:var(--radius-sm);overflow:hidden">
                            ${[c1, c2, c3].map((col, j) => html`<span key=${j} style="width:5px;height:14px;background:${col}"></span>`)}
                          </span>
                          <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${id}</span>
                        </div>`)}
                    </div>
                  </div>`}
              <//>`;
          })}
        </div>
        </div>
        <!-- qué build tiene cargado este dispositivo: n = CACHE del sw -->
        <div style="flex-shrink:0;text-align:center;padding:7px 0 2px;font-family:var(--font-mono);font-size:9.5px;letter-spacing:.08em;color:var(--text-3)">
          v${VERSION.n} · ${VERSION.fecha}</div>
        ${!!vServer && vServer !== VERSION.n && html`
          <div style="flex-shrink:0;text-align:center;padding:0 16px 6px;font-size:11.5px;line-height:1.45;color:var(--color-accent)">
            ${en ? `The server is still on v${vServer} — restart it (System › Restart server) or it keeps running the old code.`
                 : `El servidor sigue en la v${vServer}: reinicialo en Sistema › Reiniciar servidor o sigue corriendo el código viejo.`}
          </div>`}
      </div>
    </div>`;
}
