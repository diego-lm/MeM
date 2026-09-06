// Componentes compartidos — portados del prototipo (tabs, chips, brackets de
// esquina, mic) + Sidebar de escritorio (extrapolación propia del DS, sin
// documento de referencia: sigue tokens/clases existentes, nada inventado fuera de ellos).
import { html, useRef, useState, useEffect } from "../vendor/preact-htm.js";
import { useStore, go, back, setState, GENERAL } from "./state.js";
import { dict, MODE_FALLBACK } from "./i18n.js";
import { bajarAdjunto, get, post, postAttach } from "./api.js";
import { privadosDe, esSesionPrivada, esLocal, usePrivado, BotonCandado } from "./privado.js";
import { AUDIO_SVG, Lupa } from "./md.js";
import { VERSION } from "./version.js";

// -- dictado por voz (Web Speech API) — reemplaza el ticker de palabras falso
// del prototipo por transcripción real; si el navegador no la soporta, el
// mic se oculta (decisión de Diego). --
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
export const dictadoSoportado = !!SR;

export function useDictado(lang, onFinal) {
  const [activo, setActivo] = useState(false);
  const recRef = useRef(null);
  useEffect(() => () => recRef.current?.stop(), []);
  function toggle() {
    if (!SR) return;
    if (activo) { recRef.current?.stop(); setActivo(false); return; }
    const rec = new SR();
    rec.lang = lang; rec.continuous = true; rec.interimResults = false;
    rec.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++)
        if (e.results[i].isFinal) onFinal(e.results[i][0].transcript.trim());
    };
    rec.onend = () => setActivo(false);
    rec.onerror = () => setActivo(false);
    recRef.current = rec;
    rec.start();
    setActivo(true);
  }
  return { activo, toggle };
}

export const tint = (hex, pct) => `color-mix(in srgb, ${hex} ${pct}%, transparent)`;

// Think ya no es tab: las sesiones viven dentro de Input (Home). La ruta
// #sessions sigue existiendo por deep links/back, pero sin entrada en la nav.
const NAV = [
  { id: "home", glyph: "▮", key: 0 },
  { id: "memory", glyph: "▤", key: 2 },
  { id: "settings", glyph: "⚙", key: 3 },
];
const SHOW_NAV = new Set(["home", "sessions", "memory", "settings"]);

// sep = cuánto se despegan del borde de la caja: pegados al borde se leían como
// un doble marco sucio; separados se ven como lo que son, marcas de encuadre.
export function CornerBrackets({ corners = "all", color = "var(--color-accent)", size = 18, sep = 7 }) {
  const s = (top, left) => ({
    position: "absolute", [top]: `-${sep}px`, [left]: `-${sep}px`, width: `${size}px`, height: `${size}px`,
    [`border-${top}`]: `2px solid ${color}`, [`border-${left}`]: `2px solid ${color}`,
    [`border-${top}-${left}-radius`]: "0",
  });
  const toCss = (o) => Object.entries(o).map(([k, v]) => `${k.replace(/[A-Z]/g, m => "-" + m.toLowerCase())}:${v}`).join(";");
  const all = [["top", "left"], ["top", "right"], ["bottom", "left"], ["bottom", "right"]];
  const use = corners === "all" ? all : [["top", "left"], ["bottom", "right"]];
  return html`${use.map(([a, b]) => html`<span style=${toCss(s(a, b))}></span>`)}`;
}

// -- pendientes del inbox (badge del tab Memory): cache de módulo compartido —
// se refresca al cambiar de pantalla y al terminar un procesado (sin importar
// quién lo disparó), no solo con el fetch de este componente --
let inboxPend = 0;
const inboxSubs = new Set();
export function cargarInboxPend() {
  return get("/inbox").then((its) => {
    inboxPend = its.length;
    inboxSubs.forEach((fn) => fn(inboxPend));
    return inboxPend;
  }).catch(() => inboxPend);
}
/** setInterval que se calla con la app en segundo plano, y devuelve su limpieza
 *  lista para el useEffect. Sin esto MeM seguía pidiendo /system (nvidia-smi) cada
 *  3s con la pantalla apagada: batería y disco para nadie. */
export function intervaloVisible(fn, ms) {
  const id = setInterval(() => { if (document.visibilityState === "visible") fn(); }, ms);
  return () => clearInterval(id);
}

export function useInboxPend() {
  const s = useStore();
  const [n, setN] = useState(inboxPend);
  useEffect(() => {
    inboxSubs.add(setN);
    cargarInboxPend();
    return () => inboxSubs.delete(setN);
  }, [s.screen]);
  return n;
}

// -- procesar inbox: acción única compartida por los botones que la disparan
// (tarjeta de Ajustes, detalle de Ajustes, Inbox). Antes cada uno tenía su
// propio estado local y no se enteraban entre sí de que ya se estaba
// procesando, ni mostraban la misma última línea del log. --
// Dos banderas y no una: el server procesa solo en cuanto entra una captura, así
// que "procesando" puede venir de este cliente (el botón) o del server (el hilo
// de fondo, que puede haber arrancado desde otro dispositivo o desde el CLI).
let procManual = false;
let procFondo = false;
let ultimoLog = null;
const procesarSubs = new Set();
const emitirProcesar = () =>
  procesarSubs.forEach((fn) => fn({ procesando: procManual || procFondo, ultimoLog }));

function cargarUltimoLog() {
  return get("/log?horas=24&n=1").then((r) => {
    ultimoLog = r.lineas[r.lineas.length - 1] || null;
    emitirProcesar();
  }).catch(() => {});
}

export function useProcesando() {
  const [st, setSt] = useState({ procesando: procManual || procFondo, ultimoLog });
  useEffect(() => {
    procesarSubs.add(setSt);
    if (ultimoLog === null && !procManual && !procFondo) cargarUltimoLog();
    return () => procesarSubs.delete(setSt);
  }, []);
  return st;
}

// -- vigía del procesado de fondo -------------------------------------------
// El server arranca a procesar solo al recibir una captura (POST /capture) y
// lleva un contador `hechas` que solo sube. Acá se sondea mientras haya algo
// corriendo: cada vez que ese número cambia, una memoria terminó, así que se
// refresca el badge del inbox y se avisa a quien esté mirando la lista. Se
// apaga solo cuando el server dice que no queda nada — también si los items
// quedaron en error, que si no el sondeo no terminaría nunca.
let hechas = null;                 // null = todavía no se miró
let vigiaId = null;
const memoriasSubs = new Set();

/** "Algo cambió en las memorias": Memory recarga su lista sin recargar la app. */
export function onMemoriasCambian(fn) {
  memoriasSubs.add(fn);
  return () => memoriasSubs.delete(fn);
}

function mirarProcesado() {
  return get("/process/status").then((st) => {
    const termino = hechas !== null && st.hechas !== hechas;
    hechas = st.hechas;
    if (procFondo !== st.corriendo) { procFondo = st.corriendo; emitirProcesar(); }
    if (termino) {
      cargarInboxPend();
      cargarUltimoLog();
      memoriasSubs.forEach((fn) => fn());
    }
    if (!st.corriendo) pararVigia();
    return st;
  }).catch(() => { pararVigia(); });
}

function pararVigia() { clearInterval(vigiaId); vigiaId = null; }

export function vigilarProcesado() {
  if (vigiaId) return;
  vigiaId = setInterval(mirarProcesado, 1800);
  mirarProcesado();
}

// una captura entra por api.js (que no puede importar este módulo sin ciclo):
// avisa por un evento de ventana y acá se actúa — badge al toque y a vigilar el
// procesado que el server ya arrancó por su cuenta.
window.addEventListener("mem:capturado", () => { cargarInboxPend(); vigilarProcesado(); });
// al abrir la app: si quedó algo corriendo (otro dispositivo, el CLI), engancharse
mirarProcesado();

export async function procesarInbox() {
  if (procManual || procFondo) return null;
  procManual = true; emitirProcesar();
  const tic = setInterval(cargarUltimoLog, 1500);
  try {
    return await post("/process", {});
  } finally {
    clearInterval(tic);
    procManual = false; emitirProcesar();
    cargarUltimoLog();
    cargarInboxPend();
    memoriasSubs.forEach((fn) => fn());
  }
}

function BadgeInbox({ n }) {
  if (!n) return html``;
  return html`
    <span style="min-width:17px;height:17px;padding:0 4px;border-radius:var(--radius-md);background:var(--color-accent);color:var(--color-bg);font-family:var(--font-mono);font-size:9.5px;font-weight:700;display:flex;align-items:center;justify-content:center">${n > 99 ? "99+" : n}</span>`;
}

/** Los tres controles que no son de ninguna pantalla en particular: candado,
 *  tema y Ajustes. Arriba a la derecha y en TODAS las pantallas (pedido
 *  2026-09-06) — el tema y el ⚙ vivían dentro de la banda de Home, así que
 *  fuera de Home no existían, y el candado tiene que estar donde sea que se
 *  esté mirando algo tapado. Las cabeceras dejan sitio con `padding-right`
 *  (.mem-scr-head-row, .mem-chat-head, .mem-home-banda): flotan encima, y sin
 *  ese hueco tapaban el ⋯ del chat o el contador del inbox. */
export function ControlesGlobales() {
  const s = useStore();
  const L = dict(s.lang);
  const dark = s.theme === "dark";
  return html`
    <div class="mem-controles">
      <${BotonCandado} lang=${s.lang} />
      <div role="button" tabindex="0" class="mem-ctrl" title=${L.themeLabels[dark ? 0 : 1]}
           onClick=${() => setState({ theme: dark ? "light" : "dark", themePref: dark ? "light" : "dark" })}>
        ${dark ? "☾" : "☀"}
      </div>
      <div role="button" tabindex="0" class="mem-ctrl" title=${L.tabs[3]} onClick=${() => go("settings")}>⚙</div>
    </div>`;
}

export function TabBar({ EditorProyectos }) {
  const s = useStore();
  const pend = useInboxPend();
  const [proyAbierto, setProyAbierto] = useState(false);
  if (!SHOW_NAV.has(s.screen)) return html``;
  const L = dict(s.lang);
  const proyecto = String(s.proyecto || "");
  return html`
    <div class="mem-tabbar-scrim"></div>
    <div class="mem-tabbar">
      ${NAV.map((n) => {
        const on = s.screen === n.id;
        const fg = on ? "var(--color-text)" : `color-mix(in srgb, var(--color-text) 45%, transparent)`;
        return html`
          <div role="button" tabindex="0" class="mem-tab" style="position:relative;color:${fg}" onClick=${() => go(n.id)}>
            <span style="font-size:18px;line-height:1">${n.glyph}</span>
            <span style="font-family:var(--font-heading);font-size:11px;font-weight:${on ? 800 : 600}">${L.tabs[n.key]}</span>
            <span style="width:4px;height:4px;flex-shrink:0;background:${on ? "var(--color-accent)" : "transparent"}"></span>
            ${n.id === "memory" && !!pend && html`
              <span style="position:absolute;top:-4px;right:calc(50% - 26px)"><${BadgeInbox} n=${pend} /></span>`}
          </div>`;
      })}
      <!-- el proyecto activo, adaptado al estándar de la tabbar: un tab más, no
           un chip aparte (pedido 2026-08-31) — un toque abre el mismo editor que
           el sidebar de escritorio (elegir · crear · renombrar · privado · unir ·
           borrar), único punto de acceso en el celular ahora que el chip por
           pantalla se fue. -->
      <div role="button" tabindex="0" class="mem-tab" style="color:color-mix(in srgb, var(--color-text) 45%, transparent)" onClick=${() => setProyAbierto(true)}>
        <span style="font-size:18px;line-height:1">◈</span>
        <span style="font-family:var(--font-heading);font-size:11px;font-weight:600;max-width:64px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${proyecto || GENERAL}</span>
        <span style="width:4px;height:4px;flex-shrink:0;background:transparent"></span>
      </div>
      ${proyAbierto && html`
        <${EditorProyectos} valor=${proyecto} lang=${s.lang}
                             onPick=${(n) => setState({ proyecto: n })}
                             onClose=${() => setProyAbierto(false)} />`}
    </div>`;
}

// -- agentes en uso: lista cacheada de /agents/activos + estado vivo del store --
let agentesCache = null;
const agentesSubs = new Set();

export function cargarAgentes() {
  if (agentesCache) return Promise.resolve(agentesCache);
  return fetch("/agents/activos", { headers: { "Content-Type": "application/json" } })
    .then((r) => (r.ok ? r.json() : []))
    .then((a) => { agentesCache = a; agentesSubs.forEach((fn) => fn(a)); return a; })
    .catch(() => []);
}

/** Settings cambió agentes/asignaciones: invalida el cache para que Home/Sidebar
 *  reflejen el modelo/nombre nuevo sin esperar a un reload completo. */
export function invalidarAgentes() {
  agentesCache = null;
  return cargarAgentes();
}

export function useAgentes() {
  const [lista, setLista] = useState(agentesCache || []);
  useEffect(() => {
    agentesSubs.add(setLista);
    cargarAgentes();
    return () => agentesSubs.delete(setLista);
  }, []);
  return lista;
}

/** Agente asignado a una tarea, para etiquetar quién va a responder. */
export function agenteDe(lista, tarea) {
  return lista.find((a) => (a.tareas || []).includes(tarea)) || lista[0] || null;
}

// Modalidades del modelo (in→out). El backend las manda como letras t/i/a/v;
// acá van como íconos, no como glifos de texto: "▤" y "▦" no se distinguían.
// Siluetas bien distintas a 13px — renglones / foto enmarcada / altavoz / cámara.
const SVG_MOD = (d) => `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
export const ICONO_MOD = {
  t: SVG_MOD(`<path d="M4 6h16"/><path d="M4 12h13"/><path d="M4 18h9"/>`),
  i: SVG_MOD(`<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><circle cx="8.5" cy="10" r="1.7" fill="currentColor" stroke="none"/><path d="M20.5 16.5l-5.5-5.5-7 7"/>`),
  a: SVG_MOD(`<path d="M4 9.5h3.5L12 5.5v13L7.5 14.5H4z"/><path d="M16.5 9.5a4 4 0 0 1 0 5"/>`),
  v: SVG_MOD(`<rect x="2.5" y="5.5" width="13.5" height="13" rx="2.5"/><path d="M16 10.5l5.5-3v9l-5.5-3z"/>`),
};
const NOMBRE_MOD = { t: ["texto", "text"], i: ["imagen", "image"], a: ["audio", "audio"], v: ["video", "video"] };
export const modalidadTitulo = (m, en) => {
  const lista = (s) => [...(s ?? "t")].map((x) => (NOMBRE_MOD[x] || [x, x])[en ? 1 : 0]).join(", ")
    || (en ? "not usable as chat output" : "no sirve como salida de chat");
  return en ? `in: ${lista(m.in)} · out: ${lista(m.out)}` : `entrada: ${lista(m.in)} · salida: ${lista(m.out)}`;
};
export function Modalidades({ m, en }) {
  const lado = (s) => {
    const ks = [...(s ?? "t")].filter((k) => ICONO_MOD[k]);
    return ks.length
      ? ks.map((k) => html`<${Icon} svg=${ICONO_MOD[k]} />`)
      : html`<span style="font-size:11px">✕</span>`;
  };
  return html`
    <span style="display:inline-flex;align-items:center;gap:3px;flex-shrink:0" title=${modalidadTitulo(m, en)}>
      ${lado(m.in)}<span style="opacity:.5;margin:0 1px">→</span>${lado(m.out)}
    </span>`;
}

/** Quién va a responder, con qué modelo y qué material sabe leer y devolver.
 *  Abierto lista a los demás con lo mismo; elegir uno reasigna la tarea `chat`
 *  (que es también la del triaje) — no hay agente por sesión.
 *  Desde v86 es también el ÚNICO sitio donde se ve el estado de los agentes y de
 *  la máquina (pedido 2026-08-11): la tira que los listaba al pie de Home se
 *  borró, así que el punto de este chip y los del menú son los que dicen si hay
 *  server, y el bloque de la máquina cuelga del agente local dentro del menú. */
export function BotonAgente({ pensando = false, lang = "es" }) {
  const s = useStore();
  const [abierto, setAbierto] = useState(false);
  const [lado, setLado] = useState("");     // ver alternar()
  const [data, setData] = useState(null);   // /agents completo: solo al abrir
  const lista = useAgentes();
  const ag = agenteDe(lista, "chat");
  const en = lang === "en";
  const L = dict(lang);
  useEffect(() => {
    if (abierto && !data) get("/agents").then(setData).catch(() => setData({ agentes: [], modalidades: {} }));
  }, [abierto]);
  if (!ag) return html``;

  async function elegir(id) {
    setAbierto(false);
    if (!data || id === ag.id) return;
    try {
      await post("/agents", { agentes: data.agentes, asignaciones: { ...(data.asignaciones || {}), chat: id } });
      invalidarAgentes();
    } catch { /* la asignación no cambió: el chip sigue diciendo la verdad */ }
  }

  // El chip cae en cualquier punto de la fila (el ancho del rótulo manda): el
  // menú se mide contra el viewport al abrir (menuFijo) — un tope fijo a ~420px
  // se salía por el borde, y absolute se recortaba en contenedores con overflow.
  function alternar(e) {
    if (!abierto) setLado(menuFijo(e, 330, true));
    setAbierto(!abierto);
  }

  const mods = data?.modalidades || {};
  // el punto se apaga sin server y late en el que está trabajando. `pensando` es
  // el triaje de Home, que corre sobre el agente de `chat` sin pasar por el store.
  const estado = (a) => (!s.online ? "off"
    : s.agenteTrabajando === a.id || (pensando && a.id === ag.id) ? "busy" : "");
  const etiqueta = (a) => (!s.online ? L.tServerDown : estado(a) === "busy" ? L.tAgentBusy : L.tAgentIdle);
  // la máquina cuelga del agente local, y de UNO solo: con dos apuntando a
  // localhost se vería el mismo nvidia-smi dos veces.
  const local = (data?.agentes || []).find(esLocal);
  useEscape(abierto, () => setAbierto(false));
  return html`
    <span style="position:relative;display:inline-flex">
      <span role="button" tabindex="0" onClick=${alternar} aria-haspopup="menu" aria-expanded=${abierto}
            class="mem-proy-chip ${abierto ? "on" : ""}" style="max-width:none;gap:6px"
            title="${ag.nombre} · ${etiqueta(ag)} · ${modalidadTitulo(ag, en)}">
        <span class="mem-status-dot ${estado(ag)}"></span>
        <span style="font-size:12px">${ag.icono}</span>
        <!-- nombre, modelo y modalidades solo con espacio de sobra: en el celu
             queda un botón-icono y el detalle vive en el menú o en el title -->
        <span class="mem-chip-txt">${ag.nombre}</span>
        <span class="mem-ag-chip-extra" style="opacity:.7;text-transform:none;letter-spacing:.04em;overflow:hidden;text-overflow:ellipsis">${ag.modelo}</span>
        <span class="mem-ag-chip-extra"><${Modalidades} m=${ag} en=${en} /></span>
        <span style="opacity:.6">▾</span>
      </span>
      ${abierto && html`
        <div onClick=${() => setAbierto(false)} class="mem-velo"></div>
        <div class="mem-proy-menu" role="menu" style=${lado}>
          ${!data && html`<div class="mem-proy-item" style="opacity:.5">…</div>`}
          ${(data?.agentes || []).map((a) => html`
            <div key=${a.id}>
              <div role="button" tabindex="0" class="mem-proy-item" onClick=${() => elegir(a.id)}
                   style="gap:9px;${a.id === ag.id ? "color:var(--color-accent-700);font-weight:700" : ""}">
                <span class="mem-status-dot ${estado(a)}" title=${etiqueta(a)}></span>
                <span style="flex-shrink:0">${a.icono || "✦"}</span>
                <span style="flex:1;min-width:0">
                  <span style="display:block;font-size:12.5px">${a.nombre}</span>
                  <span style="display:block;font-family:var(--font-mono);font-size:9.5px;opacity:.6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${a.modelo}</span>
                </span>
                <${Modalidades} m=${mods[a.id] || {}} en=${en} />
              </div>
              ${a === local && html`<div class="mem-agente-loc"><${Maquina} en=${en} local=${true} /></div>`}
            </div>`)}
        </div>`}
    </span>`;
}

// -- qué está usando la máquina AHORA (/system), UNA lectura para todos --------
// Hay hasta tres consumidores simultáneos: el panel de Ajustes › Media, la
// tarjeta de la grilla y el bloque que cuelga del agente local — que en
// escritorio se monta dos veces, porque la tira del celu sigue en el DOM con
// display:none. Cada lectura es un nvidia-smi más dos sondas HTTP, así que se
// hace una sola y se reparte, al ritmo del que la pidió más corta. Ese ritmo es
// también la ventana del % de CPU, que el backend calcula como delta entre dos
// llamadas seguidas.
let sisCache = null;
const sisSubs = new Map();   // setState -> ms que pidió
let pararSis = null;

function sondearSistema() {
  const arrancaba = !pararSis;
  pararSis?.();
  const cargar = () => get("/system")
    .then((d) => { sisCache = d; sisSubs.forEach((_, fn) => fn(d)); })
    .catch(() => {});
  if (arrancaba) cargar();   // el que se suma después ya arranca con sisCache
  pararSis = intervaloVisible(cargar, Math.min(...sisSubs.values()));
}

export function useSistema(activo = true, ms = 3000) {
  const [s, setS] = useState(sisCache);
  useEffect(() => {
    if (!activo) return;
    sisSubs.set(setS, ms);
    sondearSistema();
    return () => {
      sisSubs.delete(setS);
      if (sisSubs.size) sondearSistema();
      else { pararSis?.(); pararSis = null; }
    };
  }, [activo, ms]);
  return s;
}

const GB = (b) => Math.round((b || 0) / 1024 ** 3);

/** Usado/total con barra, en el ancho de un chip. Rojo desde el 90%: es donde el
 *  próximo modelo ya no entra (mismo umbral que el panel grande). --color-priv es
 *  el rojo del sistema (ds.css) y ninguna paleta lo redefine, así que el aviso se
 *  lee igual en las doce. */
function Medidor({ etiq, usado, total, nombre }) {
  const pct = total ? Math.min(100, Math.round((100 * usado) / total)) : 0;
  const alto = pct >= 90;
  return html`
    <span class="mem-maq-med" title=${`${nombre}: ${GB(usado)} / ${GB(total)} GB · ${pct}%`}>
      ${etiq}
      <span class="mem-maq-bar"><i style="width:${pct}%${alto ? ";background:var(--color-priv)" : ""}"></i></span>
      <span class="mem-maq-num" style=${alto ? "color:var(--color-priv)" : ""}>${GB(usado)}/${GB(total)}</span>
    </span>`;
}

/** Lo local, colgado del agente que lo usa (pedido 2026-08-11): los dos servicios
 *  de la máquina y cuánta RAM/VRAM queda libre. Antes eran dos chips sueltos al
 *  lado de los agentes y no se leía que fueran de ninguno.
 *  La VRAM está acá y no solo en Ajustes porque es la que decide si el próximo
 *  modelo entra: verla después del OOM no sirve de nada. Clic → Ajustes › Media,
 *  que es donde se arrancan y vive el log.
 *  Cuelga del agente local en los dos sitios donde se listan los agentes: la
 *  lista del sidebar y el menú de BotonAgente (que en el celu es el único). */
export function Maquina({ en = false, local = false }) {
  const [svc, setSvc] = useState(null);
  useEffect(() => {
    const cargar = () => get("/services").then(setSvc).catch(() => setSvc(null));
    cargar();
    return intervaloVisible(cargar, 30000);
  }, []);
  // 10s y no los 3 del panel: acá es un vistazo de fondo y cada lectura es un
  // nvidia-smi más dos sondas. Sin nada local no se sondea nada.
  const medir = local || !!(svc?.comfyui || svc?.lmstudio);
  const sis = useSistema(medir, 10000);
  const ram = sis?.ram || {};
  const g = sis?.gpus?.[0];
  if (!svc) return html``;
  return html`
    <span class="mem-maq" role="button" tabindex="0" onClick=${() => go("settings", "media")}
          title=${en ? "Local services · Settings › Media" : "Servicios locales · Ajustes › Media"}>
      ${[["lmstudio", "lms"], ["comfyui", "comfy"]].map(([k, label]) => html`
        <span key=${k} class="mem-maq-svc">
          <span class="mem-status-dot ${svc[k] ? "" : "off"}" style="width:6px;height:6px;box-shadow:none"></span>${label}
        </span>`)}
      ${medir && !!ram.total && html`
        <${Medidor} etiq="RAM" usado=${ram.total - ram.libre} total=${ram.total} nombre="RAM" />`}
      ${medir && !!g && html`
        <${Medidor} etiq="VRAM" usado=${g.vram_usada} total=${g.vram_total} nombre=${g.nombre || "VRAM"} />`}
    </span>`;
}

/** La lista de agentes del sidebar. En el celu NO hay tira equivalente (pedido
 *  2026-08-11): ahí los agentes y la máquina viven en el menú de BotonAgente, y
 *  el renglón que ocupaban al pie de Home se lo quedó el chatbox. */
export function AgentesEstado() {
  const s = useStore();
  const L = dict(s.lang);
  const lista = useAgentes();
  const trabajando = s.agenteTrabajando;
  if (!lista.length) return html``;
  const en = s.lang === "en";
  const estado = (a) => (!s.online ? "off" : trabajando === a.id ? "busy" : "");
  const etiqueta = (a) => (!s.online ? L.tServerDown : trabajando === a.id ? L.tAgentBusy : L.tAgentIdle);
  // el bloque de la máquina cuelga del agente local; si NINGUNO lo es, va suelto
  // al final (ComfyUI sigue siendo local aunque el LLM no lo sea).
  const local = lista.find(esLocal);

  const abrir = (a) => go("settings", `agent:${a.id}`);
  return html`
    <div class="mem-agentes-list">
      <div class="mem-agentes-title">${L.tAgents}</div>
      ${lista.map((a) => html`
        <div key=${a.id}>
          <div class="mem-agente-row" role="button" tabindex="0" style="cursor:pointer" onClick=${() => abrir(a)} title=${a.modelo}>
            <span class="mem-agente-icon">${a.icono}</span>
            <span style="flex:1;min-width:0">
              <span class="mem-agente-name">${a.nombre}</span>
              <span class="mem-agente-model">${a.modelo}</span>
            </span>
            <span class="mem-status-dot ${estado(a)}" title=${etiqueta(a)}></span>
          </div>
          ${a === local && html`<div class="mem-agente-loc"><${Maquina} en=${en} local=${true} /></div>`}
        </div>`)}
      ${!local && html`<div class="mem-agente-loc"><${Maquina} en=${en} /></div>`}
    </div>`;
}

/** Yachay, la mascota (vuelve el 2026-08-08, más chica que la de v73).
 *  El vaivén va HORNEADO en el archivo: yachay-pp.webm es el clip original
 *  seguido de sí mismo al revés, así que un `loop` pelado ya hace el ping pong
 *  sin una línea de JS. La alternativa era un rAF empujando currentTime hacia
 *  atrás —Chrome no acepta playbackRate negativo en <video>— y encima el máster
 *  no trae duración en el contenedor, así que ni `ended` ni un seek eran fiables.
 *  El máster YA viene con canal alfa (TAG:alpha_mode=1) y hay que decodificarlo
 *  con `-c:v libvpx-vp9` a la ENTRADA: el decodificador vp9 nativo de ffmpeg se
 *  come la alfa sin avisar y saca el clip sobre negro. Un primer intento recortó
 *  ese negro con `colorkey` y le agujereó las pupilas — no hay umbral que
 *  distinga el fondo del ojo, la alfa de verdad sí. Con eso el asset quedó en
 *  0,5 MB (el máster son 18) y apoya limpio sobre cualquier fondo y en oscuro. */
export function Alpaca({ alto = 96, clase = "", estilo = "" }) {
  return html`
    <video class=${clase} src="/assets/yachay-pp.webm" autoplay muted loop playsinline
           aria-hidden="true" tabindex="-1"
           style="height:${alto}px;width:auto;display:block;pointer-events:none;${estilo}"></video>`;
}

/** Sesiones del sidebar: propias (fetch en el módulo, no en Home — el sidebar
 *  vive montado en TODAS las pantallas) filtradas al proyecto elegido en el
 *  dropdown de acá abajo. Se recarga al cambiar de pantalla (mismo patrón que
 *  useInboxPend): no hay push del server, así que un turno nuevo/movido se ve
 *  recién al navegar — vale para una app de un solo usuario. */
function useSesionesSidebar(screen, proyecto) {
  const [sesiones, setSesiones] = useState(null);
  // `proyecto` en las dependencias y no solo la pantalla: /sessions responde
  // según desde dónde se pregunta (las de un proyecto privado no salen de él),
  // así que al mudarse de proyecto la lista de antes ya no vale — se veía
  // "Sin sesiones acá" recién entrado a un proyecto privado.
  useEffect(() => {
    let vivo = true;
    get("/sessions").then((r) => { if (vivo) setSesiones(r); }).catch(() => {});
    return () => { vivo = false; };
  }, [screen, proyecto]);
  return sesiones;
}

export function Sidebar({ EditorProyectos }) {
  const s = useStore();
  const L = dict(s.lang);
  const pend = useInboxPend();
  const proyectosTodos = useProyectos();
  const privs = privadosDe(proyectosTodos);
  const proyecto = String(s.proyecto || "");
  const sesiones = useSesionesSidebar(s.screen, proyecto);
  const [gestionando, setGestionando] = useState(false);
  // el candado también acá: parado en un proyecto privado, esta lista era la
  // única que seguía mostrando sus títulos con el candado puesto — en compu no
  // se notaba porque hasta v110 en compu no había candado (reportado 2026-09-06).
  const { oculto } = usePrivado();
  const propias = (sesiones || [])
    .filter((x) => String(x.proyecto || "") === proyecto && !(oculto && esSesionPrivada(x, privs)))
    .sort((a, b) => String(b.actualizada || "").localeCompare(String(a.actualizada || "")));
  // con el candado puesto un proyecto privado no se nombra: tampoco en el selector
  const proyectosVis = oculto ? proyectosTodos.filter((p) => !p.privado) : proyectosTodos;
  return html`
    <aside class="mem-sidebar">
      <div class="mem-sidebar-brand">Me<span style="color:var(--color-accent)">M</span></div>
      <nav class="mem-sidebar-nav">
        <!-- sin Ajustes (pedido 2026-09-05): en escritorio se entra por el ⚙ de
             Home, al lado del tema. En el celular no hay sidebar y el tab bar
             SÍ lo conserva — es su única puerta. -->
        ${NAV.filter((n) => n.id !== "settings").map((n) => {
          const on = s.screen === n.id;
          return html`
            <div role="button" tabindex="0" class="mem-sidebar-item ${on ? "on" : ""}" onClick=${() => go(n.id)}>
              <span class="mem-sidebar-glyph">${n.glyph}</span>${L.tabs[n.key]}
              <span style="flex:1"></span>
              ${on && html`<span style="width:6px;height:6px;flex-shrink:0;background:var(--color-accent)"></span>`}
              ${n.id === "memory" && !!pend && html`<span style="margin-left:6px"><${BadgeInbox} n=${pend} /></span>`}
            </div>`;
        })}
      </nav>
      <!-- proyecto + sus sesiones (pedido 2026-08-31): el dropdown SOLO filtra
           esta lista y el proyecto activo (mismo itemsDeProyectos que el filtro
           de Home) — no navega ni toca ninguna sesión. Abrir una sí. El ✎ de al
           lado es el único lugar de la app que ahora abre EditorProyectos
           (crear/renombrar/privado/unir/borrar) — el chip por pantalla que hacía
           esto se fue con el sidebar (pedido 2026-08-31). -->
      <div class="mem-sidebar-ses-wrap">
        <!-- El selector se lleva TODO el ancho de la columna: el nombre del
             proyecto es lo que hay que poder leer entero, y compartir la fila
             con un botón lo cortaba a la mitad. Crear y editar bajan a su
             propia fila, separados: ＋ es "uno nuevo" y ✎ es "este de acá"
             (pedido 2026-09-05). -->
        <${ChipMenu} etiqueta=${`◈ ${proyecto}`} on=${!!proyecto}
                     estilo="display:flex;width:100%;max-width:none;justify-content:space-between"
                     items=${itemsDeProyectos(proyectosVis, proyecto, [], s.lang)}
                     onPick=${(n) => setState({ proyecto: n })} />
        <div style="display:flex;gap:6px">
          <!-- parado en General no hay ✎: es fijo y no tiene nada que editar
               (pedido 2026-09-06), así que ＋ se queda con la fila entera — y desde
               ahí se llega igual a la lista para gestionar los otros. -->
          ${[[L.tNewProject, "＋", L.tNew, () => setGestionando("nuevo")],
             ...(norm(proyecto) === norm(GENERAL) ? []
                 : [[L.tEdit, "✎", L.tEdit, () => setGestionando("editar")]])].map(([titulo, glifo, corto, abrir]) => html`
            <span key=${glifo} role="button" tabindex="0" title=${titulo} class="mem-hit" onClick=${abrir}
                  style="flex:1;height:30px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;justify-content:center;gap:6px;cursor:pointer;font-size:13px;color:var(--text-2);white-space:nowrap">
              ${glifo}<span style="font-size:11px">${corto}</span>
            </span>`)}
        </div>
        ${gestionando && html`
          <${EditorProyectos} valor=${proyecto} lang=${s.lang}
                               abrirEnNuevo=${gestionando === "nuevo"}
                               editar=${gestionando === "editar" ? proyecto : ""}
                               onPick=${(n) => setState({ proyecto: n })}
                               onClose=${() => setGestionando(false)} />`}
        <div class="mem-sidebar-ses-list">
          ${sesiones === null && html`<div style="opacity:.5;font-size:12px;padding:6px 10px">…</div>`}
          ${sesiones !== null && !propias.length && html`<div style="opacity:.4;font-size:12px;padding:6px 10px">${L.tNoSessions}</div>`}
          ${propias.map((ses) => html`
            <div key=${ses.id} role="button" tabindex="0"
                 class="mem-sidebar-ses ${s.screen === "chat" && s.param === ses.id ? "on" : ""} ${esSesionPrivada(ses, privs) ? "mem-privada" : ""}"
                 title=${ses.titulo || ses.id} onClick=${() => go("chat", ses.id)}>${ses.titulo || ses.id}</div>`)}
        </div>
      </div>
      <div class="mem-sidebar-foot">
        <!-- en escritorio la mascota vive acá, en la columna de la izquierda
             (pedido 2026-08-08): es la única banda de la app con aire de sobra
             y así acompaña a TODA pantalla, no solo a Home. Grande (pedido
             2026-08-09): la columna daba de sobra y a 118px se perdía. -->
        <div class="mem-sidebar-alpaca"><${Alpaca} alto=${172} /></div>
        <!-- ...y el bloque de agentes le pasa POR ENCIMA: línea + sombra hacia
             arriba + fondo opaco que le tapa las patas. Sin la sombra la línea
             se lee como un separador más, no como un plano adelante del otro.
             Sin línea de "Server online" (pedido 2026-08-11): el punto de cada
             agente YA se apaga cuando el server no responde, con su title. -->
        <div class="mem-sidebar-capa">
          <${AgentesEstado} />
          <!-- versión de la app, y el botón de actualizar cuando el server que
               corre quedó atrás del código en disco (pedido 2026-09-05) -->
          <div style="padding:8px 10px 0"><${IndicadorVersion} lang=${s.lang} /></div>
        </div>
      </div>
    </aside>`;
}

// Pila de sheets abiertos (ids), para que back (⌫/botón/swipe→) cierre
// SOLO el de más arriba cuando hay varios encimados (p.ej. Vistazo abierto
// desde el popup de Memory) — un solo listener de popstate por sheet, pero
// solo reacciona el que está en la punta de la pila.
let sheetSeq = 0;
const pilaSheets = [];
// El history.back() que dispara un sheet al cerrarse por botón consume SU entrada,
// pero el popstate le llega igual al sheet de abajo, que se cerraba también: con
// uno solo abierto no se notaba, con dos (el popup de "permitir públicos" dentro
// del editor) cerraba los dos. La bandera lo tapa, y se limpia en un listener
// `once` registrado recién ahí: como los de los sheets se registraron al montar,
// corren antes y todos ven la bandera todavía puesta.
let popPropio = false;
export function Sheet({ onClose, children, maxHeight = "92%", ancho = 720 }) {
  const idRef = useRef(0);
  const viaPop = useRef(false);
  useEffect(() => {
    idRef.current = ++sheetSeq;
    pilaSheets.push(idRef.current);
    history.pushState(null, "");
    const onPop = () => {
      if (popPropio) return;                                          // ese back lo pedimos nosotros
      if (pilaSheets[pilaSheets.length - 1] !== idRef.current) return; // no soy el de arriba
      pilaSheets.pop();
      viaPop.current = true;
      onClose();
    };
    addEventListener("popstate", onPop);
    return () => {
      removeEventListener("popstate", onPop);
      if (!viaPop.current) {
        const i = pilaSheets.indexOf(idRef.current);
        if (i !== -1) pilaSheets.splice(i, 1);
        popPropio = true;
        addEventListener("popstate", () => { popPropio = false; }, { once: true });
        history.back();   // se cerró por X/backdrop: consume la entrada que pusheamos
      }
    };
  }, []);
  return html`
    <div class="mem-sheet-root">
      <div onClick=${onClose} style="position:absolute;inset:0;background:rgba(20,18,16,.45);backdrop-filter:blur(3px);animation:veil .3s both"></div>
      <div class="mem-sheet-panel" style="max-height:${maxHeight};--sheet-w:${ancho}px">
        <div style="padding:12px 0 4px;display:flex;justify-content:center;flex-shrink:0"><span style="width:44px;height:5px;border-radius:var(--radius-md);background:var(--color-divider)"></span></div>
        ${children}
      </div>
    </div>`;
}

// Cabecera única de pantalla. Antes cada screens/*.js se hacía la suya y no
// coincidía ninguna: padding 48/56/58px, botón de volver de 36 o 44px (redondo
// o cuadrado), título en mono o en font-heading. Todo eso vive ahora en las
// clases .mem-scr-* y se ve igual en Memory, Ajustes, Inbox, Lint y Entrada.
// onBack por defecto = historial; las pantallas de primer nivel (Memory,
// Ajustes) pasan () => go("home") porque no siempre se llega a ellas navegando.
export function ScreenHead({ titulo, sub, onBack = back, children }) {
  return html`
    <div class="mem-scr-head">
      <div class="mem-scr-head-row">
        <div role="button" tabindex="0" onClick=${onBack} class="mem-scr-back">‹</div>
        <div class="mem-scr-title">MeM <span style="opacity:.5">//</span> ${titulo}</div>
        ${children}
      </div>
      ${sub && html`<div class="mem-scr-sub">${sub}</div>`}
    </div>`;
}

// -- proyectos: lista compartida. La crean tanto el selector del chatbox como el
// agente desde el chat (tool fijar_proyecto), así que el cache vive en el módulo
// y se re-emite a todos los que la muestran. --
let proyectosCache = null;
const proySubs = new Set();

export function cargarProyectos(forzar = false) {
  if (proyectosCache && !forzar) return Promise.resolve(proyectosCache);
  return get("/projects")
    .then((ps) => { proyectosCache = ps; proySubs.forEach((fn) => fn(ps)); return ps; })
    .catch(() => {
      // sin la lista el candado oculta TODO (no puede decidir qué es privado):
      // reintentar hasta la primera respuesta, no quedarse a ciegas para siempre
      if (proyectosCache === null) setTimeout(() => cargarProyectos(), 3000);
      return proyectosCache || [];
    });
}

export function useProyectos() {
  const [lista, setLista] = useState(proyectosCache || []);
  useEffect(() => {
    proySubs.add(setLista);
    cargarProyectos();
    return () => proySubs.delete(setLista);
  }, []);
  return lista;
}

/** false = /projects aún no llegó: con el candado puesto se oculta todo hasta
 *  saber qué es privado (mejor un parpadeo vacío que un parpadeo que muestra). */
export const proyectosListos = () => proyectosCache !== null;

export async function crearProyecto(nombre, privado) {
  const p = await post("/projects", { nombre, privado: !!privado });
  await cargarProyectos(true);
  return p;
}

/** Items de ChipMenu para los selectores de proyecto — Home, Memory, Media, el
 *  sidebar y el mover de sesión/memoria arman exactamente los mismos. `extras`
 *  van primero (típicamente Todo). Acá porque ChipMenu ya vive acá; proyectos.js
 *  lo reexporta (lo sigue usando EditorProyectos). Un proyecto privado se marca
 *  con ⚿ — el mismo glifo/rojo que sus fichas y filas (.mem-privada). */
export function itemsDeProyectos(lista, valor, extras = [], lang = "es") {
  const L = dict(lang);
  return [...extras.map((e) => ({ glyph: "◈", ...e, on: e.id === valor })),
          ...lista.map((p) => ({
            id: p.nombre, label: p.nombre, sub: p.privado ? L.tPrivado : "",
            glyph: p.privado ? html`<span class="mem-privada">⚿</span>` : "◈",
            on: p.nombre === valor }))];
}

// El editor de proyecto (renombrar/ámbito/unir/borrar) vivía acá y se borró en
// v87: elegir es una de las cinco cosas que se hacen con un proyecto, y las
// otras cuatro estaban escondidas en el panel de búsqueda de Home, una con
// window.prompt. Ese editor es ahora js/proyectos.js.

/** Escape cierra el menú/desplegable abierto. Un hook y no un onKeyDown por
 *  copia: las cuatro variantes de dropdown compartían el mismo agujero (solo
 *  cerraba Escape si había un input con foco dentro). */
export function useEscape(activo, cerrar) {
  useEffect(() => {
    if (!activo) return;
    const fn = (e) => { if (e.key === "Escape") cerrar(); };
    addEventListener("keydown", fn);
    return () => removeEventListener("keydown", fn);
  }, [activo]);
}

/** Menú de un chip medido contra el viewport, compartido por ChipMenu,
 *  ChipMenu, BotonAgente y ModeloPicker (settings.js). position:fixed y
 *  no absolute: absolute se recorta dentro de contenedores con overflow (la fila
 *  2 de la cabecera del chat scrollea en x y clipea en y — el menú salía
 *  cortado) y también dentro del panel del buscador de Home. El ancho se capa al
 *  viewport para que en un celu angosto el menú nunca se salga por el borde;
 *  max-height capado a lo que queda de pantalla — scrollea por dentro en vez de
 *  perderse por abajo.
 *  haciaDerecha: un chip al INICIO de su fila despliega hacia la derecha si
 *  entra — hacia la izquierda tapaba el sidebar. */
export function menuFijo(e, ancho, haciaDerecha = false) {
  const r = e.currentTarget.getBoundingClientRect();
  const w = Math.min(ancho, window.innerWidth - 16);
  const izq = haciaDerecha ? r.left + w < window.innerWidth - 8 : r.right - w < 8;
  const x = Math.round(Math.max(8, izq ? r.left : r.right - w));
  const alto = Math.max(120, Math.min(300, window.innerHeight - r.bottom - 14));
  return `position:fixed;top:${Math.round(r.bottom + 6)}px;left:${x}px;right:auto;max-height:${Math.round(alto)}px;width:${w}px`;
}

/** Chip + menú anclado, genérico. Reemplaza
 *  a las tiras horizontales que en el celu no entraban y dejaban scroll lateral:
 *  modo de sesión, filtros de memoria, categorías de Ajustes.
 *  items = [{id, label, glyph, sub, on}]; multi=true no cierra al elegir. */
export function ChipMenu({ etiqueta, items, onPick, multi = false, on = false, ancho = 230, titulo = "",
                          estilo = "", buscador = "", clase = "", haciaDerecha = false }) {
  const [abierto, setAbierto] = useState(false);
  const [lado, setLado] = useState("right:0");
  const [q, setQ] = useState("");
  const nm = (x) => String(x || "").toLowerCase().normalize("NFD").replace(/\p{Diacritic}/gu, "");
  const cerrar = () => { setAbierto(false); setQ(""); };
  function abrir(e) {
    setLado(menuFijo(e, ancho, haciaDerecha));
    setAbierto(true);
  }
  // Abre al APRETAR, no al soltar (pedido 2026-09-05): un menú que espera el
  // click completo se siente lento aunque no lo sea. El velo también cierra en
  // pointerdown, y por eso el gesto que abrió no se cierra solo: el velo no
  // existía todavía cuando ese pointerdown salió.
  const alPuntero = (e) => (abierto ? cerrar() : abrir(e));
  const visibles = q.trim() ? items.filter((it) => nm(`${it.label} ${it.sub || ""}`).includes(nm(q))) : items;
  useEscape(abierto, cerrar);
  return html`
    <span class="mem-chip-wrap">
      <span role="button" tabindex="0" title=${titulo} class="mem-proy-chip ${on || abierto ? "on" : ""} ${clase}"
            aria-haspopup="menu" aria-expanded=${abierto}
            style="max-width:calc(100vw - 32px);${estilo}" onPointerDown=${alPuntero}
            onKeyDown=${(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); alPuntero(e); } }}>${etiqueta} ▾</span>
      ${abierto && html`
        <div onPointerDown=${cerrar} class="mem-velo"></div>
        <div class="mem-proy-menu" role="menu" style=${lado}>
          ${buscador && html`
            <input value=${q} autofocus placeholder=${buscador} class="mem-proy-input" style="margin:2px 0 6px"
                   onInput=${(e) => setQ(e.target.value)}
                   onKeyDown=${(e) => { if (e.key === "Escape") cerrar(); }} />`}
          ${!visibles.length && html`<div class="mem-proy-item" style="opacity:.5">—</div>`}
          ${visibles.map((it) => html`
            <div role="button" tabindex="0" class="mem-proy-item" key=${it.id}
                 onClick=${() => { if (!multi) cerrar(); onPick(it.id); }}
                 style=${it.on ? "color:var(--color-accent-700);font-weight:700" : ""}>
              ${it.glyph && html`<span style="flex-shrink:0;width:16px;text-align:center;font-family:var(--font-mono)">${it.glyph}</span>`}
              <span style="flex:1;min-width:0">
                <span style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${it.label}</span>
                ${it.sub && html`
                  <span style="display:block;font-family:var(--font-mono);font-size:9.5px;opacity:.6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${it.sub}</span>`}
              </span>
              ${multi && html`<span style="flex-shrink:0;opacity:${it.on ? 1 : 0.2}">✓</span>`}
            </div>`)}
        </div>`}
    </span>`;
}

/** Modo de la sesión (chat · mindmap · timeline) como dropdown: antes era una
 *  tira de pastillas que en el celu se salía de la pantalla. Va en la misma
 *  fila que el proyecto (pedido 2026-08-04).
 *  "media" no es un modo elegible acá (pedido 2026-08-10): el taller creativo
 *  lo sigue siendo del lado del server (chat.py cambia el modo solo cuando
 *  responde el agente de crear), pero la galería para VERLO se mudó a Memory
 *  — de este menú solo desaparece la entrada, nada del backend se toca. */
export function SelectorModo({ valor, modos, lang, onPick }) {
  const L = dict(lang);
  const lista = (modos && modos.length ? modos : Object.entries(MODE_FALLBACK)
    .map(([nombre, v]) => ({ nombre, ...v })))
    // "crear" es alias histórico de "media": ninguno de los dos es un modo del menú
    .filter((m) => m.nombre !== "crear" && m.nombre !== "media");
  const actual = lista.find((m) => m.nombre === valor) || lista[0] || { glyph: "▮", nombre: valor };
  return html`
    <${ChipMenu} etiqueta=${`${actual.glyph || "▮"} ${valor || actual.nombre}`} on=${true} ancho=${250}
                 items=${lista.map((m) => ({ id: m.nombre, glyph: m.glyph, label: m.nombre,
                   sub: (L.modeDescs || {})[m.nombre] || m.descripcion || "", on: m.nombre === valor }))}
                 onPick=${onPick} />`;
}

/** Borrar una sesión: sola, o con las memorias que produjo. Sale del ✕/papelera
 *  de Home y de la pantalla completa, así que vive acá y no en una de las dos. */
export function ConfirmarBorradoSesion({ ses, memorias = [], adjuntos = [], lang, onClose, onBorrar }) {
  const L = dict(lang);
  const [borrando, setBorrando] = useState(false);
  const hayContenido = !!(memorias.length || adjuntos.length);
  const lanzar = (conContenido) => { setBorrando(true); onBorrar(conContenido); };
  const boton = (label, sub, conContenido) => html`
    <div role="button" tabindex="0" class="mem-btn-danger" onClick=${borrando ? null : () => lanzar(conContenido)}
         style="min-height:52px;padding:8px 16px;border-radius:var(--radius-md);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;font-family:var(--font-heading);font-size:16px;cursor:pointer;margin-bottom:10px;opacity:${borrando ? 0.6 : 1}">
      ${label}
      ${sub && html`<span style="font-family:var(--font-mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;opacity:.75">${sub}</span>`}
    </div>`;
  const subContenido = [memorias.length && `${memorias.length} · ${memorias.map((m) => m.titulo).join(" · ")}`,
                         adjuntos.length && `${adjuntos.length} adjunto${adjuntos.length === 1 ? "" : "s"}`]
                        .filter(Boolean).join(" · ").slice(0, 70);
  return html`
    <${Sheet} onClose=${onClose} ancho=${520}>
      <div style="padding:8px 22px 26px">
        <h3 style="margin:0 0 8px;font-family:var(--font-heading);font-size:24px">${L.tDeleteQ}</h3>
        <div style="font-size:15px;font-weight:600;margin-bottom:8px">${ses.titulo || ses.id}</div>
        <p style="margin:0 0 20px;font-size:14px;line-height:1.6;color:var(--text-2)">${L.tDeleteBody}</p>
        ${hayContenido
          ? html`${boton(L.tDeleteOnly, "", false)}${boton(L.tDeleteWithMem, subContenido, true)}`
          : boton(L.tDeleteSession, "", false)}
        <div role="button" tabindex="0" onClick=${onClose}
             style="height:48px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:14.5px;cursor:pointer;opacity:.7">${L.tCancel}</div>
      </div>
    <//>`;
}

// -- cámara: foto o video como adjunto. getUserMedia da preview real (desktop y
// móvil); si el contexto no es seguro —el PWA servido por http:// en la LAN— no
// existe, y el llamador cae al input file con capture, que en el celular abre la
// cámara nativa igual de bien. --
export const camaraSoportada = () => !!(window.isSecureContext && navigator.mediaDevices?.getUserMedia);

const sello = () => new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");

export function Camara({ onListo, onClose, lang }) {
  const L = dict(lang);
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const recRef = useRef(null);
  const [err, setErr] = useState("");
  const [grabando, setGrabando] = useState(false);
  const [segs, setSegs] = useState(0);

  useEffect(() => {
    let vivo = true;
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: true })
      .then((st) => {
        if (!vivo) { st.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = st;
        if (videoRef.current) videoRef.current.srcObject = st;
      })
      .catch((e) => setErr(String(e?.message || e)));
    return () => {   // cerrar la hoja tiene que APAGAR la cámara, no dejarla viva
      vivo = false;
      if (recRef.current?.state === "recording") recRef.current.stop();
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  useEffect(() => {
    if (!grabando) return;
    const t = setInterval(() => setSegs((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [grabando]);

  function foto() {
    const v = videoRef.current;
    if (!v?.videoWidth) return;
    const c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    c.toBlob((b) => b && onListo(new File([b], `foto-${sello()}.jpg`, { type: "image/jpeg" })), "image/jpeg", 0.9);
  }

  function video() {
    if (grabando) { recRef.current?.stop(); return; }
    if (!streamRef.current) return;
    const trozos = [];
    const rec = new MediaRecorder(streamRef.current);
    rec.ondataavailable = (e) => e.data.size && trozos.push(e.data);
    rec.onstop = () => {
      setGrabando(false);
      const b = new Blob(trozos, { type: rec.mimeType || "video/webm" });
      onListo(new File([b], `video-${sello()}.webm`, { type: b.type }));
    };
    recRef.current = rec;
    setSegs(0); setGrabando(true);
    rec.start();
  }

  const mmss = `${String(Math.floor(segs / 60)).padStart(2, "0")}:${String(segs % 60).padStart(2, "0")}`;
  return html`
    <${Sheet} onClose=${onClose} ancho=${520}>
      <div style="padding:6px 18px 24px">
        ${err ? html`
          <div style="padding:26px 4px;font-size:13.5px;line-height:1.5">
            <div style="font-weight:600;margin-bottom:6px">${L.tCamErr}</div>
            <div style="opacity:.6;font-size:12.5px">${err}</div>
          </div>`
        : html`
          <div style="position:relative;border-radius:var(--radius-md);overflow:hidden;background:#14110c;aspect-ratio:4/3">
            <video ref=${videoRef} autoplay muted playsInline style="width:100%;height:100%;object-fit:cover"></video>
            ${grabando && html`
              <div style="position:absolute;top:10px;left:10px;display:flex;align-items:center;gap:7px;padding:4px 10px;border-radius:var(--radius-md);background:rgba(20,17,12,.6);color:#f6ecdb;font-family:var(--font-mono);font-size:11px">
                <span style="width:8px;height:8px;border-radius:var(--radius-md);background:#e2603c;animation:sunpulse 1.1s ease-in-out infinite"></span>${mmss}
              </div>`}
          </div>
          <div style="display:flex;gap:9px;margin-top:12px">
            <div role="button" tabindex="0" onClick=${grabando ? null : foto} class="mem-btn-accent"
                 style="flex:1;height:46px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;gap:8px;font-size:14.5px;cursor:pointer;opacity:${grabando ? 0.4 : 1}">◉ ${L.tPhoto}</div>
            <div role="button" tabindex="0" onClick=${video} class=${grabando ? "mem-btn-danger" : "mem-btn-icon"}
                 style="flex:1;height:46px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;gap:8px;font-size:14.5px;cursor:pointer">
              ${grabando ? `■ ${L.tStop}` : `▶ ${L.tRecord}`}</div>
          </div>`}
      </div>
    <//>`;
}

// -- COMPARTIR HACIA AFUERA (pedido 2026-08-06): la hoja de compartir nativa del
// móvil, la misma que sale desde cualquier app, con los contactos de WhatsApp,
// Telegram, mail, etc. Requiere contexto seguro — en el celu MeM entra por
// https://…ts.net, así que está. --

export const compartirSoportado = () => typeof navigator.share === "function";

/** Copiar al portapapeles con las dos vías: la moderna pide permiso y hay
 *  navegadores que lo niegan de plano (medido en el pane embebido), y ahí el
 *  execCommand viejo —deprecado pero universal y sin permisos— sigue andando. */
async function copiar(texto) {
  try { await navigator.clipboard.writeText(texto); return true; } catch { /* sigue abajo */ }
  const ta = document.createElement("textarea");
  ta.value = texto;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;top:-9999px;opacity:0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  return ok;
}

/** Comparte por el share nativo. Si hay TEXTO SELECCIONADO en pantalla se
 *  comparte la selección: seleccionar y compartir queda como un gesto solo.
 *  Con un adjunto (imagen, video, audio, pdf) se manda el archivo de verdad, no
 *  su URL — que fuera de la tailnet no abriría. En escritorio, sin Web Share
 *  API, copia al portapapeles: es el equivalente honesto, no un botón muerto.
 *  Devuelve "ok" | "copiado" | "cancelado" | "no". */
export async function compartir({ titulo = "", texto = "", url = "", adjunto = "" }) {
  const sel = String(window.getSelection?.() || "").trim();
  const datos = { title: titulo || "MeM", text: sel || texto };
  if (url) datos.url = url;
  if (adjunto && !sel && navigator.canShare) {
    try {
      const f = await bajarAdjunto(adjunto);
      const conArchivo = { title: datos.title, text: datos.text, files: [f] };
      if (navigator.canShare(conArchivo)) { await navigator.share(conArchivo); return "ok"; }
    } catch (e) {
      if (e?.name === "AbortError") return "cancelado";   // lo cerró Diego, no falló
      /* el archivo no se pudo mandar: se comparte el texto igual */
    }
  }
  if (!compartirSoportado()) return (await copiar([datos.text, url].filter(Boolean).join("\n"))) ? "copiado" : "no";
  try { await navigator.share(datos); return "ok"; }
  catch (e) { return e?.name === "AbortError" ? "cancelado" : "no"; }
}

const COMPARTIR_SVG = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="2.8"/><circle cx="6" cy="12" r="2.8"/><circle cx="18" cy="19" r="2.8"/><path d="M8.5 10.7 15.5 6.4"/><path d="M8.5 13.3 15.5 17.6"/></svg>`;

export function BotonCompartir({ titulo, texto, url, adjunto, lang, estilo = "", etiqueta = false }) {
  const L = dict(lang);
  const [estado, setEstado] = useState("");
  async function tocar() {
    const r = await compartir({ titulo, texto, url, adjunto });
    if (r === "cancelado") return;
    setEstado(r);
    setTimeout(() => setEstado(""), 1800);
  }
  const label = estado === "copiado" ? L.tCopied : estado === "no" ? L.tShareFail : L.tShare;
  return html`
    <div role="button" tabindex="0" onClick=${tocar} title=${L.tShare} class="mem-hit"
         style="height:36px;padding:0 ${etiqueta ? "13px" : "10px"};border-radius:var(--radius-md);display:inline-flex;align-items:center;gap:7px;cursor:pointer;border:1px solid var(--color-divider);background:var(--color-surface);font-size:13px;flex-shrink:0;${estilo}">
      <${Icon} svg=${COMPARTIR_SVG} />${(etiqueta || estado) && html`<span>${label}</span>`}
    </div>`;
}

export function Toast({ children }) {
  return html`
    <div style="position:absolute;left:20px;right:20px;bottom:112px;background:var(--color-accent-2-700);color:var(--color-bg);border-radius:var(--radius-md);padding:14px 18px;display:flex;align-items:center;gap:10px;box-shadow:var(--shadow-lg);animation:pop .4s cubic-bezier(.22,1,.36,1);font-size:14px;z-index:40">
      <span style="width:22px;height:22px;border-radius:var(--radius-md);background:var(--color-bg);color:var(--color-accent-2-700);display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700">✓</span>
      ${children}
    </div>`;
}

const MIC_SVG = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2.5" width="6" height="11" rx="3"></rect><path d="M5.5 11a6.5 6.5 0 0 0 13 0"></path><path d="M12 17.5V21"></path><path d="M8.5 21h7"></path></svg>`;

export function Icon({ svg, style = "" }) {
  return html`<span style="display:inline-flex;flex-shrink:0;${style}" dangerouslySetInnerHTML=${{ __html: svg }}></span>`;
}

export function MicButton({ active, onClick, disabled, size = 52 }) {
  return html`
    <div role="button" tabindex="0" onClick=${disabled ? null : onClick} class=${active ? "mem-btn-accent" : "mem-btn-icon"}
         style="width:${size}px;height:${size}px;flex-shrink:0;display:flex;align-items:center;justify-content:center;gap:2px;line-height:1;cursor:${disabled ? "default" : "pointer"};opacity:${disabled ? 0.4 : 1};transition:background .25s cubic-bezier(.22,1,.36,1)">
      ${active
        ? html`<span style="width:3px;height:11px;background:currentColor;animation:breathe .9s ease-in-out infinite"></span>
               <span style="width:3px;height:19px;background:currentColor;animation:breathe .9s ease-in-out infinite .15s"></span>
               <span style="width:3px;height:14px;background:currentColor;animation:breathe .9s ease-in-out infinite .3s"></span>`
        : html`<span style="display:flex" dangerouslySetInnerHTML=${{ __html: MIC_SVG }}></span>`}
    </div>`;
}

// pegar · adjuntar · cámara: los tres botones que acompañan al micrófono en el
// compositor. Estaban copiados con sus svg en Home y en Chat (mismo trazo, un
// px de diferencia); el resto de cada compositor SÍ es distinto y se queda en
// su pantalla — hacer UN componente de todo pedía diez props y dos layouts.
const SVG_PEGAR = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" style="display:block"><rect x="8" y="3" width="8" height="4" rx="1.4"></rect><path d="M8 5H6.5A1.5 1.5 0 0 0 5 6.5v13A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5v-13A1.5 1.5 0 0 0 17.5 5H16"></path><path d="M9 12h6"></path><path d="M9 16h4"></path></svg>`;
const SVG_CLIP = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" style="display:block"><path d="M16.5 7.5 8.6 15.4a2.6 2.6 0 0 0 3.7 3.7l8.2-8.2a5 5 0 0 0-7.1-7.1l-8.2 8.2a7.4 7.4 0 0 0 10.5 10.5l4-4"></path></svg>`;
const SVG_CAMARA = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="display:block"><path d="M3 8.5A2 2 0 0 1 5 6.5h2.2l1.3-2h7l1.3 2H19a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"></path><circle cx="12" cy="13" r="3.6"></circle></svg>`;

export function AccionesAdjunto({ onPegar, onClip, onCamara, estilo = "", tam = 18, lang = "es" }) {
  const L = dict(lang);
  const boton = (onClick, svg, title) => html`
    <div role="button" tabindex="0" onClick=${onClick} class="mem-btn-icon" title=${title} style=${estilo}>
      <span style="display:flex;width:${tam}px;height:${tam}px" dangerouslySetInnerHTML=${{ __html: svg }}></span>
    </div>`;
  return html`
    ${boton(onPegar, SVG_PEGAR, L.tPaste)}
    ${boton(onClip, SVG_CLIP, L.tAttach)}
    ${boton(onCamara, SVG_CAMARA, L.tCamera)}`;
}

// ------------------------------------------------- adjuntos del compositor
// Pedido 2026-08-07: pegar una imagen del portapapeles tiene que adjuntarla igual
// que arrastrarla (antes "pegar" solo leía TEXTO y una captura de pantalla se
// perdía), se puede adjuntar más de una, y la ficha es solo miniatura + ✕ — el
// nombre del archivo no le dice nada a nadie y ocupaba la fila entera.
// Vive en ui.js y no en cada pantalla porque el gesto es el mismo en Home y en la
// sesión: si diverge, uno de los dos compositores queda viejo.

/** Los archivos que ESTE mensaje lleva encima: File recién elegido (por clip,
 *  cámara, arrastre o pegado) y/o ruta ya subida (borrador restaurado). Los
 *  iniciales pueden ser de las dos clases: un string es una ruta de la base. */
export function useAdjuntos(iniciales = []) {
  const [items, setItems] = useState(() => iniciales.filter(Boolean).map((x) =>
    (typeof x === "string" ? { ruta: x, url: `/attach/${x}`, nombre: x.split("/").pop() }
                           : { file: x, url: URL.createObjectURL(x), nombre: x.name })));
  const revocar = (it) => { if (it?.file) URL.revokeObjectURL(it.url); };
  // los object URL se sueltan al irse de la pantalla; los File que sobreviven al
  // desmontaje (borrador de Home) vuelven a crear el suyo al montar de nuevo
  const vivos = useRef(items);
  vivos.current = items;
  useEffect(() => () => vivos.current.forEach(revocar), []);
  const agregar = (archivos) => {
    const nuevos = [...(archivos || [])].filter(Boolean)
      .map((f) => ({ file: f, url: URL.createObjectURL(f), nombre: f.name }));
    if (nuevos.length) setItems((p) => [...p, ...nuevos]);
    return nuevos.length;
  };
  const quitar = (i) => setItems((p) => { revocar(p[i]); return p.filter((_, k) => k !== i); });
  const limpiar = () => setItems((p) => { p.forEach(revocar); return []; });
  /** Sube lo que falte y devuelve las rutas de la base, en orden. Cada archivo se
   *  sube UNA vez: la ruta queda en el item y un reintento no vuelve a subirlo. */
  const subir = async () => {
    const listos = await Promise.all(items.map(async (it) =>
      it.ruta || (await postAttach(it.file)).path));
    setItems((p) => p.map((it, i) => ({ ...it, ruta: listos[i] })));
    return listos;
  };
  return { items, agregar, quitar, limpiar, subir, rutas: items.map((i) => i.ruta).filter(Boolean) };
}

/** Lo que el portapapeles trae como ARCHIVO (una captura de pantalla, un audio,
 *  un archivo copiado del explorador). Vacío = era texto y lo pega el navegador. */
export function archivosDelPortapapeles(e) {
  const dt = e.clipboardData;
  if (!dt) return [];
  if (dt.files?.length) return [...dt.files];
  // Chrome en Windows: una captura viene como item de tipo image/png sin entrar
  // en .files. getAsFile() la devuelve sin nombre útil, así que se le pone uno.
  return [...(dt.items || [])].filter((it) => it.kind === "file")
    .map((it) => it.getAsFile()).filter(Boolean)
    .map((f) => (f.name && f.name !== "image.png" ? f
      : new File([f], `pegado-${Date.now()}.${(f.type.split("/")[1] || "png").replace("jpeg", "jpg")}`, { type: f.type })));
}

/** Tira de adjuntos: miniatura cuadrada + ✕, sin nombre ni peso (pedido
 *  2026-08-07). Imagen y video se ven; audio y lo demás llevan su glifo. */
export function TiraAdjuntos({ items, onQuitar, subiendo = false, tam = 54 }) {
  if (!items.length) return null;
  return html`
    <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap;padding-bottom:8px">
      ${items.map((it, i) => html`
        <span key=${it.url} style="position:relative;width:${tam}px;height:${tam}px;border-radius:var(--radius-md);overflow:hidden;flex-shrink:0;background:var(--color-surface);border:1px solid var(--color-divider);display:flex;align-items:center;justify-content:center;animation:pop .28s cubic-bezier(.22,1,.36,1)">
          ${IMG_EXT.test(it.nombre) || it.file?.type?.startsWith("image/")
            ? html`<img src=${it.url} alt="" style="width:100%;height:100%;object-fit:cover;display:block" />`
            : VID_EXT.test(it.nombre) || it.file?.type?.startsWith("video/")
            ? html`<video src=${it.url} muted preload="metadata" style="width:100%;height:100%;object-fit:cover;display:block"></video>`
            : html`<span style="font-size:18px;opacity:.55">${AUD_EXT.test(it.nombre) || it.file?.type?.startsWith("audio/") ? "♪" : "▦"}</span>`}
          <span role="button" tabindex="0" title=${it.nombre} class="mem-hit" onClick=${() => onQuitar(i)}
                style="position:absolute;top:2px;right:2px;width:18px;height:18px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:10px;line-height:1;cursor:pointer;background:rgba(0,0,0,.55);color:#fff">✕</span>
        </span>`)}
      ${subiendo && html`<span style="font-family:var(--font-mono);font-size:10.5px;opacity:.55">…</span>`}
    </div>`;
}

export function AddChip({ onAdd, placeholder = "tag", dashed = true }) {
  const [editando, setEditando] = useState(false);
  const [v, setV] = useState("");
  const ref = useRef(null);
  useEffect(() => { if (editando) ref.current?.focus(); }, [editando]);
  function commit() {
    const val = v.trim();
    setEditando(false); setV("");
    if (val) onAdd(val);
  }
  if (editando) return html`
    <input ref=${ref} value=${v} onInput=${(e) => setV(e.target.value)}
           onKeyDown=${(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") { setEditando(false); setV(""); } }}
           onBlur=${commit} placeholder=${placeholder}
           style="height:28px;width:100px;padding:0 10px;border-radius:var(--radius-md);font-size:11.5px;border:1px solid var(--color-accent);background:var(--color-bg);color:var(--color-text);outline:none" />`;
  return html`
    <span role="button" tabindex="0" onClick=${() => setEditando(true)}
          style="height:28px;padding:0 10px;display:flex;align-items:center;font-family:var(--font-mono);font-size:10.5px;cursor:pointer;border:1px dashed var(--color-divider);opacity:.7">＋ ${placeholder}</span>`;
}

/** Texto comparable: sin mayúsculas ni tildes. Lo usan los tres buscadores
 *  (Home, Inbox, Memory) — estaba copiado tal cual en cada uno. */
export const norm = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/\p{Diacritic}/gu, "");

/** Rótulo de sección (mismo mono/uppercase que el resto del brief). */
export const TITULO_SEC = "font-family:var(--font-mono);font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--text-3)";

/** Botón cuadrado de icono del compositor (adjuntar, micrófono, enviar). Estaba
 *  duplicado byte a byte en home.js y chat.js — son el MISMO compositor. */
export const BTN_ICONO = "width:44px;height:44px;border-radius:var(--radius-md);flex-shrink:0;display:flex;align-items:center;justify-content:center;line-height:1;cursor:pointer;box-shadow:var(--shadow-sm)";

/** Reinicio remoto del server (POST /server/restart) — dos toques (el segundo
 *  confirma), luego poll a /health hasta que el proceso nuevo conteste. Lo usan
 *  el botón de Ajustes y el indicador de versión de Home: mismo flujo, dos sitios. */
export function useReiniciarServidor(onListo) {
  const [fase, setFase] = useState("idle"); // idle | confirmar | reiniciando | listo | fallo
  async function iniciar() {
    if (fase === "idle") {
      setFase("confirmar");
      setTimeout(() => setFase((f) => (f === "confirmar" ? "idle" : f)), 4000);
      return;
    }
    if (fase !== "confirmar") return;
    setFase("reiniciando");
    try { await post("/server/restart"); } catch { /* el server puede cortar justo al responder */ }
    for (let i = 0; i < 30; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      try {
        const h = await fetch("/health").then((r) => r.json());
        if (h.ok) { setFase("listo"); onListo?.(); setTimeout(() => setFase("idle"), 3000); return; }
      } catch { /* aún levantando */ }
    }
    setFase("fallo");
  }
  return { fase, iniciar };
}

/** vN chico en Home; si el server (proceso vivo) quedó atrás del código en
 *  disco (pedido 2026-09-05, mismo bug que ya pasaba en Ajustes: el .js se lee
 *  siempre del disco pero Python es el del arranque), se vuelve un botón de
 *  reinicio en vez de solo texto. */
export function IndicadorVersion({ lang, clase = "" }) {
  const en = lang === "en";
  const [vServer, setVServer] = useState(null);
  useEffect(() => { get("/health").then((h) => setVServer(h.version || 0)).catch(() => {}); }, []);
  const { fase, iniciar } = useReiniciarServidor(() => setVServer(VERSION.n));
  // al día = un dato, no una acción: lleva su propia clase para que una pantalla
  // apretada (Home en el celular) lo esconda sin esconder también el aviso de
  // actualización, que es lo único que hay que ver sí o sí.
  if (vServer === null || vServer === VERSION.n)
    return html`<span class=${`mem-ver-alDia ${clase}`} style="font-family:var(--font-mono);font-size:10px;opacity:.4">v${VERSION.n}</span>`;
  const txt = {
    // sin el número: en la banda de Home en el celular, "⟳ actualizar a v100"
    // era lo bastante ancha para partir la fila en dos. El número está en el title.
    idle: en ? "⟳ update" : "⟳ actualizar",
    confirmar: en ? "tap again" : "tocá de nuevo",
    reiniciando: en ? "restarting…" : "reiniciando…",
    listo: en ? "✓ done" : "✓ listo",
    fallo: en ? "failed" : "falló",
  }[fase];
  return html`
    <span role="button" tabindex="0" onClick=${iniciar} class=${`mem-btn-accent ${clase}`}
          title=${en ? `server on v${vServer}, code on v${VERSION.n}` : `el server corre v${vServer} y el código es v${VERSION.n}`}
          style="height:26px;padding:0 9px;border-radius:var(--radius-md);display:inline-flex;align-items:center;font-family:var(--font-mono);font-size:10px;white-space:nowrap;cursor:pointer">${txt}</span>`;
}

/** El celu no tiene dónde apretar "actualizar" —ese botón vivía en la banda de
 *  Home y se fue (pedido 2026-09-06)— así que un shell viejo se quedaba viejo
 *  para siempre. Si el server corre una versión MÁS NUEVA que la que este
 *  navegador tiene cargada, lo viejo es el cache: se tira y se recarga.
 *
 *  El flag de sessionStorage guarda para QUÉ versión ya se recargó, así que si
 *  después de recargar sigue habiendo diferencia (un sw.js que no cede, un
 *  proxy) no entra en bucle: recarga una vez y se queda. Y sin red no se toca
 *  nada: borrar el cache offline deja la app en blanco. */
export function useAutoActualizar() {
  useEffect(() => {
    if (!navigator.onLine) return;
    get("/health").then(async (h) => {
      const v = h.version || 0;
      if (v <= VERSION.n || sessionStorage.getItem("mem.recargado") === String(v)) return;
      try { sessionStorage.setItem("mem.recargado", String(v)); } catch { /* modo privado */ }
      try { await Promise.all((await caches.keys()).map((k) => caches.delete(k))); } catch { /* sin CacheStorage */ }
      try {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map((r) => r.unregister()));
      } catch { /* sin service worker */ }
      location.reload();
    }).catch(() => {});
  }, []);
}

export const IMG_EXT = /\.(png|jpe?g|gif|webp)$/i;
export const VID_EXT = /\.(mp4|webm|mov|m4v)$/i;
export const AUD_EXT = /\.(mp3|wav|ogg|m4a|opus|flac|weba)$/i;
// el altavoz vive en md.js (lo necesita como string para el markdown) y se
// importa acá en vez de estar copiado — era el mismo svg dos veces

/** Reproductor de audio con su icono de altavoz: lo usan el Adjunto de una
 *  entrada/burbuja y la vista previa del chatbox de Home (src puede ser un
 *  object URL, que no tiene extensión — por eso recibe src y no ruta). */
export function ReproductorAudio({ src, estilo = "" }) {
  return html`
    <span style="display:flex;align-items:center;gap:9px;padding:7px 9px;border-radius:var(--radius-md);background:color-mix(in srgb,var(--color-text) 6%,transparent);border:1px solid var(--color-divider);${estilo}">
      <span style="flex-shrink:0;width:34px;height:34px;border-radius:var(--radius-md);background:color-mix(in srgb,var(--color-accent-2) 26%,transparent);display:flex;align-items:center;justify-content:center"><${Icon} svg=${AUDIO_SVG} /></span>
      <audio src=${src} controls preload="metadata" style="flex:1;min-width:0;height:34px"></audio>
    </span>`;
}

/** Copia un medio al portapapeles. La imagen va como IMAGEN de verdad (se pega
 *  en cualquier app), y para eso hay que pasarla a PNG: es el único tipo que
 *  aceptan los navegadores en el portapapeles — un jpg/webp crudo tira
 *  NotAllowedError. Video y audio no entran en ningún portapapeles: de esos se
 *  copia la URL absoluta, que es lo útil que se puede dar. */
export async function copiarMedio(ruta) {
  const url = `/attach/${ruta}`;
  const absoluta = location.origin + url;
  if (!IMG_EXT.test(ruta) || !window.ClipboardItem) return copiar(absoluta);
  try {
    const blob = await (await fetch(url)).blob();
    let png = blob;
    if (blob.type !== "image/png") {
      const bmp = await createImageBitmap(blob);
      const c = Object.assign(document.createElement("canvas"), { width: bmp.width, height: bmp.height });
      c.getContext("2d").drawImage(bmp, 0, 0);
      png = await new Promise((r) => c.toBlob(r, "image/png"));
    }
    await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
    return true;
  } catch {
    return copiar(absoluta);   // sin permiso de portapapeles queda la URL
  }
}

const ICONO_COPIAR = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>`;

function BotonCopiarMedio({ ruta, estilo = "" }) {
  // "" | "✓" | "✕": el navegador puede negar el portapapeles y un botón que no
  // hace nada visible se lee como roto.
  const [marca, setMarca] = useState("");
  async function alTocar(e) {
    e.preventDefault(); e.stopPropagation();   // la imagen vive dentro de un <a>
    setMarca(await copiarMedio(ruta) ? "✓" : "✕");
    setTimeout(() => setMarca(""), 1400);
  }
  return html`
    <span role="button" tabindex="0" title="Copiar" onClick=${alTocar}
          style="width:30px;height:30px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;background:color-mix(in srgb,var(--color-bg) 82%,transparent);border:1px solid var(--color-divider);backdrop-filter:blur(6px);${estilo}">
      ${marca || html`<${Icon} svg=${ICONO_COPIAR} />`}
    </span>`;
}

// Adjunto de una captura/entrada: imagen -> miniatura (clic la agranda en la
// lupa); video/audio -> reproductor; cualquier otro archivo -> chip con nombre +
// botón. Todos llevan copiar (pedido 2026-08-06): en la imagen flota arriba a la
// derecha para no comerle alto a la burbuja.
export function Adjunto({ ruta }) {
  const [zoom, setZoom] = useState(false);
  const url = `/attach/${ruta}`;
  const nombre = ruta.split("/").pop();
  // md-thumb, la misma clase que las imágenes de una respuesta: antes esta abría
  // el original en una pestaña nueva y las del chat en el diálogo — dos gestos
  // distintos para lo mismo (pedido 2026-08-07). Ahora todas son miniatura + lupa.
  if (IMG_EXT.test(ruta)) return html`
    <div style="position:relative;display:inline-block;max-width:100%;margin-bottom:10px;line-height:0">
      <img class="md-thumb" src=${url} alt=${nombre} title=${nombre} loading="lazy" style="margin:0"
           role="button" tabindex="0" onClick=${() => setZoom(true)} />
      <${BotonCopiarMedio} ruta=${ruta} estilo="position:absolute;top:7px;right:7px" />
      ${zoom && html`<${Lupa} src=${url} onCerrar=${() => setZoom(false)} />`}
    </div>`;
  if (VID_EXT.test(ruta)) return html`
    <div style="position:relative;display:inline-block;max-width:100%;margin-bottom:10px">
      <video src=${url} controls playsinline preload="metadata"
             style="max-width:100%;max-height:280px;border-radius:var(--radius-md);display:block;box-shadow:var(--shadow-sm)"></video>
      <${BotonCopiarMedio} ruta=${ruta} estilo="position:absolute;top:7px;right:7px" />
    </div>`;
  if (AUD_EXT.test(ruta)) return html`
    <span style="display:flex;align-items:center;gap:7px;margin-bottom:10px">
      <${ReproductorAudio} src=${url} estilo="flex:1;min-width:0" />
      <${BotonCopiarMedio} ruta=${ruta} />
    </span>`;
  return html`
    <div style="border:1px solid var(--color-divider);border-radius:var(--radius-md);padding:12px;display:flex;gap:11px;align-items:center;margin-bottom:10px;background:var(--color-surface)">
      <span style="width:44px;height:44px;border-radius:var(--radius-md);flex-shrink:0;background:color-mix(in srgb,var(--color-accent-2) 24%,transparent);display:flex;align-items:center;justify-content:center;font-family:var(--font-mono);font-size:15px">▦</span>
      <div style="flex:1;min-width:0;font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${nombre}</div>
      <${BotonCopiarMedio} ruta=${ruta} />
      <a href=${url} target="_blank" style="width:32px;height:32px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;justify-content:center;font-size:12px;color:inherit;flex-shrink:0">▷</a>
    </div>`;
}

// El SSE manda {tool, args}: acá se traduce a algo legible. Los que tardan
// (crear_imagen puede ser un minuto largo) son los que más lo necesitan.
const TOOL_LABEL = {
  buscar: ["buscando", "consulta"], leer_pagina: ["leyendo", "path"],
  conexiones: ["conexiones", "slug"],
  grep: ["grep", "patron"], guardar_entrada: ["guardando", "titulo"],
  leer_adjunto: ["leyendo adjunto", "path"],
  fijar_proyecto: ["proyecto", "nombre"], leer_medio: ["mirando el medio", "path"],
  buscar_web: ["buscando en la web", "consulta"],
  leer_web: ["leyendo web", "url"], skill: ["skill", "nombre"],
  crear_imagen: ["generando imagen", "prompt"], guardar_creacion: ["catalogando", "prompt"],
  // el video local son 20-40 min: sin esta etiqueta el chip diría "crear_video" a secas
  // "local"/"nube" en el chip: sin eso, "generando video" al lado de un chip que
  // nombra ComfyUI se lee como que todo salió del PC (2026-08-07)
  crear_video: ["generando video en local (20-40 min)", "prompt"],
  crear_nube: ["generando en la nube (Higgsfield)", "modelo"],
  modelos_nube: ["viendo qué modelos hay en la nube", "tipo"],
  // no es una tool: lo emite chat.py al pasar la sesión a modo media por un pedido
  // de generar medios, para que se vea POR QUÉ cambió el chip de la cabecera
  modo_media: ["pasando al modo media", ""],
  // tampoco son tools: los emite servicios.preparar antes de hablarle a un modelo
  // local que todavía no está en VRAM — si no, son minutos de pantalla muda.
  // Dicen "que escribe" porque Diego leyó "soltando la VRAM de ComfyUI" como que
  // el medio se estaba generando en su PC (2026-08-07): esto es solo el modelo de
  // texto que conversa, todavía no se generó nada.
  cargando_modelo: ["cargando el modelo que escribe, puede tardar", "modelo"],
  liberando_vram: ["soltando la VRAM de ComfyUI para eso", ""],
};

function chipDe(tl) {
  if (tl.label) return tl;                       // ya viene armado
  const [label, argKey] = TOOL_LABEL[tl.tool] ||
    (tl.tool?.startsWith("mcp__") ? [tl.tool.split("__")[1] + ": " + tl.tool.split("__")[2], ""] : [tl.tool || "…", ""]);
  const human = argKey && tl.args?.[argKey] ? String(tl.args[argKey]).slice(0, 40) : "";
  return { label, human, bg: "var(--color-surface)",
           fg: "color-mix(in srgb, var(--color-text) 72%, transparent)", bd: "var(--color-divider)" };
}

// Generar tarda de verdad: un minuto largo una imagen, 20-40 min un video. Esas
// no entran como chip apretado —el prompt no se leía— sino como tarjeta con aire:
// prompt entero, modelo y reloj corriendo. Se queda hasta que la generación
// termina, y ahí el resultado ocupa su lugar (pedido 2026-08-06).
const LENTAS = new Set(["crear_imagen", "crear_video", "crear_nube"]);
const transcurrido = (ms) => {
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
};

function TarjetaGeneracion({ tl, onCancel }) {
  const [ahora, setAhora] = useState(Date.now());
  useEffect(() => {
    if (tl.fin) return;                       // terminada: el reloj se congela
    const t = setInterval(() => setAhora(Date.now()), 1000);
    return () => clearInterval(t);
  }, [tl.fin]);
  const [label] = TOOL_LABEL[tl.tool] || [tl.tool];
  const modelo = tl.args?.modelo || tl.args?.workflow || "";
  const reloj = transcurrido((tl.fin || ahora) - (tl.t0 || ahora));
  // De qué imagen se parte, cuando el pedido era editar una que ya existe: verla
  // es lo que confirma que agarró LA imagen correcta, antes de esperar el minuto
  // de generación (pedido 2026-08-06). Sale del arg `referencia` del evento SSE.
  const ref = String(tl.args?.referencia || "").replace(/^\/?attach\//, "");
  // fill backwards y no both: con both el 100% de chipIn (opacity:1) le gana para
  // siempre al opacity de la tarjeta terminada, y no se apagaba nunca.
  return html`
    <div style="width:100%;padding:13px 15px;border-radius:var(--radius-md);border:1px solid ${tl.fin ? "var(--color-divider)" : "color-mix(in srgb,var(--color-accent) 45%,transparent)"};background:var(--color-surface);animation:chipIn .42s cubic-bezier(.22,1,.36,1) backwards;opacity:${tl.fin ? 0.65 : 1}">
      <div style="display:flex;align-items:center;gap:8px;font-family:var(--font-mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--text-2)">
        ${tl.fin
          ? html`<span style="color:var(--color-accent-2-700)">✓</span>`
          : html`<span style="width:8px;height:8px;background:var(--color-accent);border-radius:var(--radius-md);animation:breathe 1.5s ease-in-out infinite;flex-shrink:0"></span>`}
        <span style="flex:1;min-width:0">${label}</span>
        <span style="flex-shrink:0;font-variant-numeric:tabular-nums">${reloj}</span>
        ${!tl.fin && !!onCancel && html`
          <span role="button" tabindex="0" title="Detener" onClick=${onCancel} class="mem-hit"
                style="flex-shrink:0;width:24px;height:24px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;cursor:pointer;border:1px solid var(--color-divider);font-size:9px;color:inherit">■</span>`}
      </div>
      ${!!ref && html`
        <div style="margin-top:10px;display:flex;gap:9px;align-items:center">
          <img src=${`/attach/${ref}`} alt="" style="width:52px;height:52px;border-radius:var(--radius-md);object-fit:cover;flex-shrink:0;border:1px solid var(--color-divider)" />
          <span style="font-size:12px;color:var(--text-2)">partiendo de esta</span>
        </div>`}
      ${!!modelo && html`
        <div style="margin-top:6px;font-family:var(--font-mono);font-size:11px;color:var(--text-3)">${modelo}</div>`}
      ${!!tl.args?.prompt && html`
        <div style="margin-top:9px;font-size:13.5px;line-height:1.6;color:color-mix(in srgb,var(--color-text) 85%,transparent);white-space:pre-wrap;overflow-wrap:anywhere">${tl.args.prompt}</div>`}
    </div>`;
}

export function ToolChips({ tools, onCancel }) {
  const lentas = tools.filter((t) => LENTAS.has(t.tool));
  const rapidas = tools.filter((t) => !LENTAS.has(t.tool));
  return html`
    <div style="display:flex;flex-direction:column;gap:9px;align-items:flex-start;width:100%">
      ${lentas.map((tl, i) => html`<${TarjetaGeneracion} key=${`${tl.tool}${i}`} tl=${tl} onCancel=${onCancel} />`)}
      <div style="display:flex;flex-wrap:wrap;gap:7px;align-items:center">
        ${rapidas.map(chipDe).map((tl, i) => html`
          <span style="display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 11px;border-radius:var(--radius-md);font-family:var(--font-mono);font-size:11.5px;background:${tl.bg};color:${tl.fg};border:1px solid ${tl.bd};animation:chipIn .42s cubic-bezier(.22,1,.36,1) both;animation-delay:${i * 0.12}s">
            <span style="width:7px;height:7px;background:currentColor;border-radius:var(--radius-md);animation:breathe 1.5s ease-in-out infinite;animation-delay:${i * 0.12}s"></span>
            ${tl.label}${tl.human ? html` · <span style="opacity:.75">${tl.human}</span>` : ""}
          </span>`)}
        <span style="display:inline-flex;gap:3px;padding:0 4px">
          ${[0, 1, 2].map((i) => html`<span style="width:5px;height:5px;border-radius:var(--radius-md);background:currentColor;opacity:.4;animation:dot 1.1s infinite;animation-delay:${i * 0.15}s"></span>`)}
        </span>
      </div>
    </div>`;
}

// -- estilos que no caben cómodos en inline (tab bar flotante + sidebar) --
const CSS = `
/* Opaco de verdad detrás de la fila de botones y recién ahí se desvanece: con el
   82% + mask de antes el degradado nunca llegaba a tapar nada y el texto de la
   lista se leía POR DEBAJO de los iconos (pedido 2026-08-08). El mask sobraba:
   fundía dos veces el mismo degradado, por eso quedaba tan flojo. */
/* 96px y no 118 (pedido 2026-08-11): el contenido descansa donde el degradado ya
   es transparente, así que ese número ES el hueco muerto al pie de Home — 118
   para una tabbar de 64 dejaba media pantalla de aire bajo la última fila. */
.mem-tabbar-scrim{position:fixed;left:0;right:0;bottom:0;height:calc(96px + env(safe-area-inset-bottom,0px));z-index:28;pointer-events:none;background:linear-gradient(to top,var(--color-bg) 0%,var(--color-bg) 58%,color-mix(in srgb,var(--color-bg) 80%,transparent) 78%,transparent 100%)}
/* bottom con max(): en el navegador el inset es 0 y se queda en 0 de siempre;
   en un iPhone con barra de gestos crece para no quedar tapado por el home indicator.
   Sin pastilla flotante ni sombra (Modernist: 1-2px de regla, no elevación) — fila
   plana pegada al borde, como en el mockup. */
.mem-tabbar{position:fixed;left:0;right:0;bottom:max(0px, env(safe-area-inset-bottom,0px));height:64px;display:flex;align-items:center;justify-content:space-around;z-index:30;max-width:420px;margin:0 auto}
.mem-tab{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;cursor:pointer}
.mem-sidebar{display:none}
/* botones: en oscuro el accent a fondo pleno queda fluorescente y los iconos
   sobre neutral-800 desaparecen contra el fondo. Misma jerarquía, sin bofetada. */
.mem-btn-accent{background:var(--color-accent);color:var(--color-bg);border:1px solid transparent}
[data-theme="dark"] .mem-btn-accent{background:color-mix(in srgb,var(--color-accent) 24%,transparent);color:var(--color-accent-700);border-color:color-mix(in srgb,var(--color-accent) 45%,transparent)}
.mem-btn-icon{background:var(--color-neutral-800);color:var(--color-neutral-100);border:1px solid transparent}
[data-theme="dark"] .mem-btn-icon{background:color-mix(in srgb,var(--color-text) 11%,transparent);color:var(--color-text);border-color:var(--color-divider)}
.mem-btn-soft{background:color-mix(in srgb,var(--color-text) 10%,transparent);color:var(--text-2)}
.mem-btn-danger{background:transparent;border:1.5px solid var(--color-accent);color:var(--color-accent)}
/* botón "procesando": barrido sobre superficie neutra, en vez del accent pleno —
   se ve claramente distinto de "activo" y de "inerte" (opacity, sin esto). */
.mem-btn-procesando{cursor:default;color:var(--text-2);border:1px solid var(--color-divider);background:var(--color-surface);background-image:linear-gradient(100deg,transparent 35%,color-mix(in srgb,var(--color-text) 18%,transparent) 50%,transparent 65%);background-size:220% 100%;animation:sweep 1.5s linear infinite}
/* Ajustes: columna que le da a la grilla 3:2 el espacio libre real (el nº de
   columnas lo calcula useGrid32 con ese hueco). 96px libra la tabbar flotante. */
.mem-set-wrap{flex:1;display:flex;flex-direction:column;min-height:0;padding:10px 20px 96px}
/* Interior de una categoría: los controles son angostos (un Seg, un select de
   190px) y quedaban solos en una línea de 1000px — 700px de aire muerto y dos
   pantallas de scroll (pedido 2026-08-11). column-width y NO column-count: el
   propio ancho decide cuántas entran, así el sidebar (que aparece a los 880px)
   no necesita su propio breakpoint y en el celu vuelve a ser una sola columna.
   multicol y no grid: los bloques son de altos muy distintos y una grilla deja
   el hueco muerto debajo del más bajo de cada fila. */
.mem-set-cols{columns:300px;column-gap:28px}
.mem-set-cols>*{break-inside:avoid;margin-bottom:16px}
/* Widget: container query sobre el TAMAÑO real de la celda (la grilla le fija
   ancho y alto) — el contenido se compacta, y lo accesorio se oculta, para que
   la grilla entre en UNA pantalla. El alto cuenta tanto como el ancho: una celda
   ancha y baja (el celu en una columna) recorta igual que una angosta. */
.mem-set-card{container-type:size;min-width:0;overflow:hidden}
/* El padding y el gap salen de la CELDA, no de un número fijo (cq* = unidades de
   container query, que esta tarjeta ya habilita con container-type:size). Con los
   14px fijos de antes, una celda de 460×290 en escritorio se veía con el contenido
   amontonado contra las esquinas y un agujero en el medio; ahora el aire crece con
   la tarjeta. space-between reparte ese hueco entre los tres bloques (icono ·
   título · controles) en vez de dejarlo todo junto abajo (pedido 2026-08-08). */
.mem-set-in{height:100%;display:flex;flex-direction:column;justify-content:space-between;gap:clamp(7px,3.5cqh,18px);padding:clamp(10px,5cqh,26px) clamp(11px,5cqw,26px)}
.mem-set-icon{width:clamp(24px,11cqh,42px);height:clamp(24px,11cqh,42px);flex-shrink:0;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:clamp(12px,5.5cqh,20px);line-height:1}
.mem-set-title{font-size:clamp(var(--fs-2),4.5cqh,var(--fs-5));font-weight:700}
.mem-set-hint{font-family:var(--font-mono);font-size:var(--fs-1);text-transform:uppercase;opacity:.55}
/* controles de más que la tarjeta expone SOLO si le sobra ancho (pedido
   2026-08-04): en el celu, con celdas de ~165px, no aparecen. */
.mem-set-extra{display:none}
@container (min-width:250px) and (min-height:160px){ .mem-set-extra{display:flex;flex-wrap:wrap;gap:5px;align-items:center} }
/* Un escalón más (pedido 2026-08-08): con una celda grande —escritorio, 2×2— la
   tarjeta muestra también lo que hasta ahora había que entrar a ver. Mismo
   criterio que .mem-set-extra: es el TAMAÑO de la celda quien decide, no el
   ancho de la ventana, así que rotar el celu o abrir el sidebar lo recalcula. */
.mem-set-mas{display:none}
@container (min-width:330px) and (min-height:215px){
  .mem-set-mas{display:flex;flex-direction:column;gap:8px;min-width:0;width:100%}
  /* la de paletas es la única lista de DOCE: en filas ocupaba la tarjeta entera.
     Va después de .mem-set-mas a propósito — misma especificidad, gana el orden. */
  .mem-set-pal{display:grid;grid-template-columns:repeat(auto-fill,minmax(92px,1fr));gap:3px}
  /* el resumen comprimido que ese bloque reemplaza: sin esto la tarjeta grande
     dice dos veces lo mismo (la línea de modelos, la fila de swatches mudos) */
  .mem-set-nomas{display:none!important}
}
/* piso de 11px (pedido 2026-08-05): el hint ya no encoge en este escalón medio
   -antes bajaba a 8.5px- así que compacta con menos padding/icono en vez de
   con texto ilegible; si la celda no entra, el escalón de abajo lo esconde. */
@container (max-width:250px) or (max-height:160px){
  .mem-set-in{gap:6px;padding:10px 11px}
  .mem-set-icon{width:24px;height:24px;font-size:12px;border-radius:var(--radius-md)}
  .mem-set-title{font-size:var(--fs-2)}
}
@container (max-width:185px) or (max-height:120px){
  .mem-set-in{gap:4px;padding:8px 9px}
  .mem-set-icon{width:20px;height:20px;font-size:10px;border-radius:var(--radius-md)}
  .mem-set-title{font-size:var(--fs-2)}
  .mem-set-hint{display:none}
  .mem-set-chico{display:none}
}
/* botón de agente: icono + nombre + LLM, uno resaltado */
.mem-ag-btn{display:inline-flex;align-items:center;gap:9px;flex-shrink:0;padding:8px 13px 8px 8px;border-radius:var(--radius-md);cursor:pointer;border:1.5px solid var(--color-divider);background:var(--color-surface);transition:border-color .2s,background .2s}
.mem-ag-btn.on{border-color:var(--color-accent);background:color-mix(in srgb,var(--color-accent) 13%,transparent)}
.mem-ag-glyph{width:30px;height:30px;border-radius:var(--radius-md);flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:15px;line-height:1;background:color-mix(in srgb,var(--color-accent) 20%,transparent);color:var(--color-accent-700)}
.mem-ag-btn.on .mem-ag-glyph{background:var(--color-accent);color:var(--color-bg)}
.mem-ag-nombre{display:block;font-size:var(--fs-3);font-weight:700;white-space:nowrap}
.mem-ag-modelo{display:block;font-family:var(--font-mono);font-size:var(--fs-1);white-space:nowrap;color:var(--text-3)}
.mem-ag-nuevo{width:48px;padding:0;justify-content:center;font-size:19px;border-style:dashed;color:var(--color-accent-700)}
.mem-status-dot{width:7px;height:7px;border-radius:var(--radius-md);flex-shrink:0;background:var(--color-ok)}
.mem-status-dot.off{background:var(--color-neutral-500)}
.mem-status-dot.busy{background:var(--color-accent);animation:sunpulse 1.1s ease-in-out infinite}
.mem-agentes-list{display:flex;flex-direction:column;gap:3px}
.mem-agentes-title{font-family:var(--font-mono);font-size:var(--fs-1);letter-spacing:.14em;text-transform:uppercase;color:var(--text-3);padding:0 10px;margin-bottom:4px}
.mem-agente-row{display:flex;align-items:center;gap:9px;padding:7px 10px;border-radius:var(--radius-md);cursor:pointer;transition:background .2s}
.mem-agente-row:hover{background:color-mix(in srgb,var(--color-text) 6%,transparent)}
.mem-agente-icon{width:20px;text-align:center;font-size:14px;color:var(--color-accent);flex-shrink:0}
.mem-agente-name{display:block;font-size:var(--fs-2);font-weight:600;color:var(--text-2)}
.mem-agente-model{display:block;font-family:var(--font-mono);font-size:var(--fs-1);letter-spacing:.04em;color:var(--text-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* Lo que corre EN la máquina, colgado del agente que usa el LLM local: los dos
   servicios y cuánta RAM/VRAM queda. Un solo bloque para los dos sitios donde se
   listan agentes — la lista del sidebar y el menú de BotonAgente. */
.mem-maq{display:flex;align-items:center;gap:4px 10px;flex-wrap:wrap;padding:2px 0;cursor:pointer;font-family:var(--font-mono);font-size:var(--fs-1);letter-spacing:.06em;text-transform:uppercase;color:var(--text-3)}
.mem-maq-svc,.mem-maq-med{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
.mem-maq-bar{width:30px;height:4px;border-radius:var(--radius-md);flex-shrink:0;overflow:hidden;background:color-mix(in srgb,var(--color-text) 15%,transparent)}
/* la barra normal va NEUTRA y no en el acento: el sistema es mono-rojo, así que
   en la paleta de fábrica el acento ES el mismo rojo del aviso y "casi lleno" no
   se distinguía de "todo bien". Color solo en la excepción. */
.mem-maq-bar>i{display:block;height:100%;background:color-mix(in srgb,var(--color-text) 45%,transparent);transition:width .4s ease}
.mem-maq-num{color:var(--text-2)}
/* rama que baja del icono del agente (los 19px son su centro, tanto en la fila
   del sidebar como en la del menú de BotonAgente: llevan el mismo sangrado). */
.mem-agente-loc{margin:1px 0 3px 19px;padding-left:10px;border-left:1px solid var(--color-divider)}
/* fixed y no absolute (v87): absolute lo ataba al ancestro posicionado más
   cercano, y eso obligaba a montar cada Sheet en la raíz de su pantalla. El chip
   de proyecto lo abre desde la banda superior de Home, que es absolute: el sheet
   salía DENTRO de esa banda, de 267x82. De paso el velo tapa también el sidebar,
   que en escritorio quedaba clickeable detrás del modal. */
.mem-sheet-root{position:fixed;inset:0;z-index:60}
/* Contenido de sheet en dos columnas donde hay ancho: lo que se LEE (cuerpo,
   adjunto, enlaces) a la izquierda y lo que se OPERA (proyecto, tags,
   conexiones, acciones) a la derecha. En una sola columna era una tira larga
   donde el botón de abajo quedaba a tres scrolls del título. La cabecera cruza
   las dos. Abajo de 880 vuelve a ser una sola, en el mismo orden de siempre. */
.mem-sheet-2col{display:grid;grid-template-columns:minmax(0,1fr);gap:0 26px;align-items:start}
.mem-sheet-2col>.cab{grid-column:1/-1}
@media (min-width:880px){
  .mem-sheet-2col{grid-template-columns:minmax(0,1.4fr) minmax(0,1fr)}
  .mem-sheet-2col>.lado{border-left:1px solid var(--color-divider);padding-left:22px}
}
.mem-sheet-panel{position:absolute;left:0;right:0;bottom:0;background:var(--color-bg);border-radius:var(--radius-lg);border-top:2px solid var(--color-text);animation:sheetUp .44s cubic-bezier(.22,1,.36,1);display:flex;flex-direction:column;overflow:hidden;padding-bottom:env(safe-area-inset-bottom,0px)}
/* padding-top con env(): en el navegador el inset es 0 y queda en 16px; en un
   iPhone con notch crece para no meter la cabecera bajo la cámara. */
.mem-scr-head{padding:calc(16px + env(safe-area-inset-top,0px)) 20px 8px}
/* cabecera compartida (ScreenHead): mismo sitio y mismo tamaño en toda pantalla.
   El -10px del botón compensa su caja de 36px para que el ‹ quede ópticamente
   sobre el borde del contenido de abajo, no desplazado hacia adentro. */
.mem-scr-head-row{display:flex;align-items:center;flex-wrap:wrap;gap:6px;min-height:36px}
/* área de toque: el ‹ se ve 36px pero el ::after invisible completa 44px sin
   mover nada (position:relative ya la tenía el flujo del botón). */
.mem-scr-back{position:relative;width:36px;height:36px;margin-left:-10px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:19px;cursor:pointer;opacity:.7;flex-shrink:0;transition:background .2s,opacity .2s}
.mem-scr-back::after{content:"";position:absolute;inset:-4px}
.mem-scr-back:hover{opacity:1;background:color-mix(in srgb,var(--color-text) 6%,transparent)}
.mem-scr-title{font-family:var(--font-mono);font-size:var(--fs-1);letter-spacing:.16em;text-transform:uppercase;color:var(--color-accent-700);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.mem-scr-sub{font-family:var(--font-mono);font-size:var(--fs-1);letter-spacing:.12em;text-transform:uppercase;color:var(--text-3);margin-top:5px}
/* selector de proyecto: chip + menú anclado, para no empujar el layout del chatbox.
   32px visual (antes 24) + ::after hasta 44px de toque real (pedido 2026-08-05,
   DESIGN_BRIEF §7). overflow:clip con margen en vez de hidden: el recorte de la
   elipsis pasa a 8px *fuera* de la caja, así el halo del ::after no se corta. */
.mem-chip-wrap{position:relative;display:inline-flex;flex-shrink:0}
.mem-proy-chip{position:relative;display:inline-flex;align-items:center;gap:5px;max-width:190px;height:32px;padding:0 11px;border-radius:var(--radius-md);font-family:var(--font-mono);font-size:var(--fs-1);letter-spacing:.06em;text-transform:uppercase;white-space:nowrap;overflow:clip;overflow-clip-margin:8px;text-overflow:ellipsis;cursor:pointer;border:1px solid var(--color-divider);color:var(--text-3)}
.mem-proy-chip::after{content:"";position:absolute;inset:-6px 0}
.mem-proy-chip.on{border-color:color-mix(in srgb,var(--color-accent) 55%,transparent);background:color-mix(in srgb,var(--color-accent) 13%,transparent);color:var(--color-accent-700)}
/* anclado a la DERECHA: el chip vive al final de su fila, y abriendo hacia la
   izquierda el menú entra en 375px de ancho en vez de salirse de la pantalla. */
.mem-proy-menu{position:absolute;top:calc(100% + 6px);right:0;z-index:40;width:230px;max-width:calc(100vw - 28px);max-height:300px;overflow:auto;border-radius:var(--radius-md);background:var(--color-bg);border:1px solid var(--color-divider);box-shadow:var(--shadow-lg);padding:4px;animation:pop .1s cubic-bezier(.22,1,.36,1)}
/* scrim de cierre de los menús anclados: 39 queda justo debajo del z-40 del
   .mem-proy-menu (y del Toast) — si esa relación cambia, cambiarla junta. */
.mem-velo{position:fixed;inset:0;z-index:39}
/* Los pocos <select> que SIGUEN siendo nativos (agente por tarea, workflow de
   ComfyUI, modelo de Higgsfield, modelo de nube): en el celular el picker del
   sistema es mejor que cualquier menú propio, así que se quedan — pero con el
   mismo cromo que el resto de los controles en vez del del sistema operativo.
   36px de alto: los 32 de un input no llegaban al piso táctil cómodo. */
.mem-select{height:36px;padding:0 26px 0 9px;border-radius:var(--radius-md);border:1px solid var(--color-divider);background:var(--color-surface);color:var(--color-text);font-family:var(--font-mono);font-size:var(--fs-2);cursor:pointer;appearance:none;background-image:linear-gradient(45deg,transparent 50%,currentColor 50%),linear-gradient(135deg,currentColor 50%,transparent 50%);background-position:calc(100% - 14px) 50%,calc(100% - 9px) 50%;background-size:5px 5px,5px 5px;background-repeat:no-repeat}
.mem-select:focus-visible{outline:2px solid var(--color-accent);outline-offset:1px}
.mem-proy-item{display:flex;align-items:center;gap:8px;padding:10px 10px;min-height:44px;border-radius:var(--radius-md);font-size:var(--fs-3);cursor:pointer;text-transform:none;letter-spacing:0}
.mem-proy-item:hover{background:color-mix(in srgb,var(--color-text) 6%,transparent)}
/* Memory: a la izquierda CÓMO se mira (un menú), acá con qué se ACOTA. Se
   separan con el espacio y una línea, no con otro color: son controles del
   mismo peso. En angosto el margin-left:auto no aplica y el grupo cae a su
   propia línea, que es justo lo que se quiere en un celular. */
.mem-acota{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.mem-fila-acota{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:11px}
@media (min-width:760px){.mem-acota{margin-left:auto;padding-left:11px;border-left:1px solid var(--color-divider)}}
/* Celu: la fila de Memory salía en cuatro renglones con medio ancho vacío, y no
   era por los chips sino por el grupo: .mem-acota es una caja aparte, así que
   empieza en su propio renglón y deja el resto del anterior sin usar.
   display:contents la disuelve —los chips pasan a ser hijos de la misma fila y
   la llenan— y con el respiro de más recortado entran los seis en dos renglones.
   El 11px de --fs-1 no se toca: es el piso legible de la app (pedido 2026-09-06). */
@media (max-width:600px){
  .mem-acota{display:contents}
  .mem-fila-acota{gap:6px}
  .mem-proy-chip{height:29px;padding:0 5px;gap:3px;letter-spacing:0;max-width:150px}
  /* y una vez que entran en dos renglones, que los llenen: cada chip crece a su
     parte del sobrante en vez de dejar un hueco muerto a la derecha. El texto se
     reparte con space-between, así la flecha queda contra el borde como en un
     select y no flotando al medio (pedido 2026-09-06). */
  .mem-fila-acota .mem-chip-wrap{flex:1 1 auto;min-width:0}
  .mem-fila-acota .mem-proy-chip{width:100%;max-width:none;justify-content:space-between}
}
/* 44px es el dedo. Con mouse esa altura obliga a scrollear un menú de ocho
   proyectos que entraría entero — elegir se vuelve dos gestos en vez de uno. */
@media (pointer:fine){.mem-proy-item{min-height:32px;padding:6px 10px}}
.mem-proy-input{width:100%;box-sizing:border-box;height:40px;padding:0 10px;border-radius:var(--radius-md);border:1px solid var(--color-accent);background:var(--color-bg);color:var(--color-text);font-size:var(--fs-3);outline:none}
/* Pastilla que ABRAZA su texto: acciones sueltas (.mem-tog) y casillas
   (.mem-tog.check, con el cuadrito del estado a la izquierda). */
.mem-tog{display:inline-flex;align-items:center;gap:9px;min-height:40px;padding:0 14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);background:var(--color-surface);color:var(--text-2);font-size:var(--fs-2);cursor:pointer}
.mem-tog.check::before{content:"";width:13px;height:13px;flex-shrink:0;border-radius:3px;border:1.5px solid currentColor;opacity:.5}
.mem-tog.on{border-color:var(--color-accent);background:color-mix(in srgb,var(--color-accent) 12%,transparent);color:var(--color-accent-700)}
.mem-tog.check.on::before{background:currentColor;opacity:1;box-shadow:inset 0 0 0 2px var(--color-surface)}
/* la acción que destruye no se disfraza de las otras dos (Von Restorff) */
.mem-tog.peligro{color:var(--color-priv);border-color:color-mix(in srgb,var(--color-priv) 45%,transparent)}
/* Celu: el chatbox no tiene ancho para prosa decorativa — la etiqueta ▸ y el
   modelo/modalidades del chip de agente se esconden (el detalle vive en el menú
   del chip o en el title). El NOMBRE del agente sí se queda: sin él el chip era
   dos glífos sueltos (⌘ ▾) que se leían como algo cortado, no como un botón. */
@media (max-width:600px){
  .mem-ag-chip-extra{display:none}
  .mem-home-etiq{display:none}
  .mem-chip-txt{max-width:7ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
}
/* Un control, dos formas: tira de pastillas donde hay ancho, dropdown donde no.
   Así ninguna pantalla del celu queda con scroll horizontal (pedido 2026-08-04). */
@media (max-width:600px){ .mem-solo-ancho{display:none!important} }
@media (min-width:601px){ .mem-solo-angosto{display:none!important} }
/* privado (pedido 2026-08-05): borde ROJO que destaca sobre las demás fichas.
   El !important gana a los bordes inline de fichas y filas.
   --color-priv y no --color-accent (pedido 2026-08-09): el acento lo redefine
   cada paleta, así que en Fósforo el aviso salía verde y en Ámbar dorado. Este
   rojo no lo cambia ningún tema. El baño del 8% lo hace un highlight y no solo
   un contorno: en una grilla de fichas el borde solo se pierde. */
.mem-privada{border:1.5px solid var(--color-priv)!important;
  background:color-mix(in srgb,var(--color-priv) 8%,var(--color-surface))!important}
/* Barra de controles globales (candado · tema · Ajustes): flota arriba a la
   derecha en todas las pantallas. --ctrl-hueco es lo que las cabeceras se
   reservan para no quedar debajo. */
:root{--ctrl-hueco:146px}
.mem-controles{position:fixed;top:calc(14px + env(safe-area-inset-top,0px));right:20px;z-index:35;display:flex;gap:7px}
.mem-ctrl{width:40px;height:40px;flex-shrink:0;border-radius:var(--radius-md);border:1px solid var(--color-divider);background:var(--color-surface);color:var(--color-text);display:flex;align-items:center;justify-content:center;font-size:15px;cursor:pointer;transition:border-color .2s,color .2s}
.mem-ctrl.on{border-color:var(--color-priv);color:var(--color-priv)}
.mem-scr-head-row,.mem-chat-head>div:first-of-type,.mem-home-banda{padding-right:var(--ctrl-hueco)}
/* DESPUÉS de la regla base y no en el bloque de móvil de más arriba: misma
   especificidad, gana la última que se escribe. */
@media (max-width:600px){
  :root{--ctrl-hueco:118px}
  .mem-controles{top:calc(10px + env(safe-area-inset-top,0px));right:12px;gap:5px}
  .mem-ctrl{width:34px;height:34px;font-size:14px}
}
/* halo de toque genérico para controles inline que no pasan por .mem-proy-chip
   (la ✕ de un tag, etc): mismo patrón, position:relative + ::after invisible. */
.mem-hit{position:relative}
.mem-hit::after{content:"";position:absolute;inset:-13px}
/* material que el LLM no pudo leer: el rojo del DS (mismo de mem-btn-danger) */
.mem-pend{color:var(--color-accent)}
/* tab de Pendientes en Memory: solo existe si hay algo pendiente. Estático, sin
   pulso (Modernist: nada de sombra/animación para llamar la atención, el rojo
   único ya destaca solo). El !important gana al borde inline del strip. */
.mem-vista-pend{color:var(--color-accent)!important;border-color:var(--color-accent)!important;background:var(--color-accent-100)!important}
/* fichas de Memory: una columna en móvil, las que entren en desktop — así el
   ancho de la ventana se usa igual que la grilla de Ajustes. */
.mem-fichas{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:9px;margin-bottom:9px;align-items:start}
/* En el celular la ficha es SOLO EL TÍTULO (pedido 2026-08-09): resumen, sello,
   subjects y tags se van, y con eso entran ~5 veces más memorias en la pantalla.
   Todo lo escondido sigue a un toque de distancia, en el vistazo. */
@media (max-width:600px){
  .mem-fichas{gap:5px}
  .mem-ficha{padding:9px 11px!important}
  .mem-ficha-sub{display:none!important}
  .mem-ficha-tit{font-size:13.5px!important;margin-bottom:0!important;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
}
/* SIN backdrop-filter a propósito: crea containing block nuevo para los hijos
   position:fixed (spec CSS Transforms) — el menú de ChipMenu
   se posicionaba contra ESTA caja en vez del viewport real y, corrido por el
   ancho del sidebar (220px, desktop), salía por el borde derecho de la ventana
   con el texto cortado (bug reportado 2026-08-10). */
.mem-chat-head{padding:calc(16px + env(safe-area-inset-top,0px)) 12px 8px;background:color-mix(in srgb,var(--color-bg) 88%,var(--color-text));border-bottom:1px solid var(--color-divider);z-index:5}
.mem-chat-head-priv{background:color-mix(in srgb,var(--color-priv) 12%,color-mix(in srgb,var(--color-bg) 88%,var(--color-text)))!important}
/* fila 2 (modo/privado/agente/proyecto/guardar/borrar/cerrar): antes envolvía a
   2-3 líneas y se comía ~50px de pantalla — ahora es una tira con scroll propio,
   los chips no se aprietan ni empujan el resto de la cabecera hacia abajo. */
.mem-chat-head-row2{display:flex;align-items:center;flex-wrap:nowrap;gap:6px;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch}
.mem-chat-head-row2::-webkit-scrollbar{display:none}
/* En el flujo, NO flotando encima (pedido 2026-08-08): con el pie absoluto el
   scroll del chat llegaba hasta el borde inferior de la ventana y sus últimos
   ~85px de barra quedaban tapados por el compositor — había que arrastrar el
   pulgar "por detrás" para llegar al final. Ahora el panel termina donde empieza
   el pie. De paso se fueron el blur (su fondo ya era opaco: color-mix de dos
   colores opacos) y toda la maquinaria de --foot-h/.mem-pb-chat que existía solo
   para descontar el alto de un pie flotante. El max-width acompaña al de los
   mensajes, así el filo de arriba es la misma regla vertical que la conversación. */
.mem-chat-foot{flex-shrink:0;width:100%;max-width:1100px;margin:0 auto;padding:10px 14px max(14px, calc(10px + env(safe-area-inset-bottom,0px)));background:var(--color-bg);border-top:1px solid var(--color-divider)}
/* compositor: iconos + input + enviar en UNA fila (pedido 2026-08-06). En
   pantalla angosta los iconos ceden tamaño; con los 44px de siempre el input se
   quedaba con ~80px y no se podía ni leer lo que uno escribe. */
/* Tira de pestañas (las vistas de Memory, el orden del Inbox). Estaba dos veces
   con el terracota escrito en hex, así que en cualquiera de las otras cinco
   paletas —y en oscuro— el tab activo quedaba ilegible. */
.mem-tira{flex:none;height:34px;padding:0 13px;border-radius:var(--radius-md);display:flex;align-items:center;gap:6px;justify-content:center;font-family:var(--font-mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;white-space:nowrap;background:var(--color-surface);color:var(--text-2);border:1px solid var(--color-divider)}
.mem-tira.on{background:var(--color-accent);color:var(--color-bg);border-color:transparent}
[data-theme="dark"] .mem-tira.on{background:color-mix(in srgb,var(--color-accent) 24%,transparent);color:var(--color-accent-700);border-color:color-mix(in srgb,var(--color-accent) 45%,transparent)}
/* el fondo de las pantallas largas ya no va inline: así la regla de escritorio
   puede ganarle. 100px = tabbar flotante + su degradado (.mem-tabbar-scrim, que
   son 96); en escritorio no hay tabbar y el bloque de abajo lo baja a un respiro. */
.mem-pb-mem{padding-bottom:100px}
/* markdown dentro de la burbuja propia: ahí el fondo ES el acento, así que los
   tonos de acento del .md (links de entrada, fondos de code) quedan ilegibles */
.mem-yo .md a,.mem-yo .md a.md-entry{color:inherit;background:color-mix(in srgb,var(--color-bg) 24%,transparent)}
.mem-yo .md code{background:color-mix(in srgb,var(--color-bg) 22%,transparent)}
.mem-comp{display:flex;gap:8px;align-items:center}
/* el input arranca EXACTAMENTE con el alto de los botones y con el texto
   centrado (align-items:center + min-height, no padding a ojo). Al crecer a
   varias líneas se estira y los botones quedan centrados contra él. */
.mem-comp-in{flex:1;min-width:0;min-height:44px;box-sizing:border-box;display:flex;align-items:center;
  background:var(--color-surface);border:1px solid var(--color-divider);border-radius:var(--radius-md);padding:7px 14px}
/* 600px = el corte de "celu" de toda la app (el mismo de .mem-solo-ancho y del
   antizoom de app.css). Este bloque estaba en 560 y dejaba una franja de 40px
   donde el compositor era grande pero los chips ya se habían encogido. */
@media (max-width:600px){
  /* En el celular no entran los 5 botones Y el input en un renglón: el input se
     quedaba con ~90px (pedido 2026-08-07). El input se lleva la línea entera y
     los botones bajan debajo, enviar a la derecha — siguen todos a la misma
     altura entre sí y los targets vuelven a 44px. */
  .mem-comp{flex-wrap:wrap;gap:6px}
  .mem-comp-in{flex-basis:100%;order:-1}
  .mem-comp>[role="button"]:last-child{margin-left:auto}
}
/* el top con max(): en un iPhone con notch, sin esto el toggle de tema y la fecha
   quedaban pegados al borde (o debajo de la muesca) */
.mem-home-outer{padding:max(16px,env(safe-area-inset-top,0px)) 22px 96px}   /* = alto del scrim */
.mem-home-stage{position:relative;flex:1;min-height:280px;display:flex;flex-direction:column}
.mem-home-content{width:100%;max-width:420px;display:flex;flex-direction:column;gap:14px;flex:1;min-height:0}
/* la mascota flota a la izquierda de la misma banda, enfrentada a la fecha, y
   APOYADA sobre la línea de arriba del chatbox (pedido 2026-08-09): el 78px es
   el margin-top del card de abajo y el translateY la sienta encima sea cual sea
   su alto — con un top fijo bastaba cambiarle el alto para descolgarla. */
.mem-home-alpaca{position:absolute;top:90px;left:0;transform:translateY(-100%);z-index:3}
/* clima · fecha · tema · proyecto. El left despeja a la mascota (53px de ancho):
   la tira de clima es flex:1 y mide su propio hueco, así que de ese número sale
   cuántas horas entran. Donde la mascota no está, la banda arranca en el borde. */
.mem-home-banda{position:absolute;top:0;left:64px;right:0;z-index:3;display:flex;align-items:center;gap:6px 10px;flex-wrap:wrap}
/* despeja la banda de arriba (clima/fecha/tema a la derecha, alpaca a la
   izquierda), que flota con position:absolute y por eso no empuja nada por sí
   sola: 44 de la fila + aire. El proyecto se fue al sidebar/tabbar (pedido
   2026-08-31) — ya no le suma un segundo renglón abajo de 880px. */
.mem-home-card{margin-top:52px}
/* sus resultados caen como panel flotante sobre el chatbox (pedido 2026-08-04) */
.mem-home-busca{position:relative;z-index:6;flex-shrink:0}
/* el panel reemplaza visualmente al chatbox (pedido 2026-08-05): cuelga de
   .mem-home-card (no de .mem-home-busca) para poder estirarse top→bottom con
   el mismo ancho y alto exactos del card del chatbox, en vez de un floating
   card angosto que dejaba ver el chatbox vacío por debajo. 49px = fila del
   buscador (40) + gap del card (9), la misma constante de la línea de abajo. */
.mem-home-res{position:absolute;top:49px;left:0;right:0;bottom:0;z-index:30;overflow:auto;padding:8px 8px 1px;border-radius:var(--radius-md);border:1px solid var(--color-divider);background:var(--color-surface);box-shadow:var(--shadow-lg);animation:pop .22s cubic-bezier(.22,1,.36,1)}
/* .mem-home-ta (17/19px) se borró en v87: el chatbox de Home usa el mismo
   textarea de 16px del compositor de la sesión (.mem-comp-in), que además es el
   tamaño que evita el zoom automático de iOS al enfocar. */
/* medida de lectura (45-75 caracteres): antes solo regía desde 880px, así que
   una tablet en vertical (834px) corría el ancho completo del panel — texto
   de pantalla a pantalla, incómodo de leer. A 375px width:100% manda igual. */
.mem-screen{width:100%;max-width:640px;margin:0 auto}
.mem-screen.ancha{max-width:1100px}
@media (min-width:880px){
  .mem-tabbar-scrim,.mem-tabbar{display:none}
  .mem-scr-head{padding:22px 24px 10px}
  .mem-chat-head{padding:22px 16px 8px}
  .mem-home-outer{padding:22px 36px 30px}
  .mem-home-content{max-width:980px}
  /* la mascota está en el sidebar: acá sobra, y sin ella la banda vuelve a lo
     justo para la fila de fecha/tema. !important porque el <video> trae su
     display:block inline (le gana a una clase suelta). */
  .mem-home-alpaca{display:none!important}
  /* la versión también pasa al sidebar acá: duplicada en la banda sobra */
  .mem-home-ver{display:none!important}
  .mem-home-banda{left:0}
  /* el sidebar pinta su propio fondo: sin esto heredaba el del body y en dark
     mode se quedaba claro (bug reportado). */
  .mem-sidebar{display:flex;flex-direction:column;width:220px;flex-shrink:0;padding:28px 14px;gap:22px;background:var(--color-surface);color:var(--color-text);border-right:1px solid var(--color-divider)}
  .mem-sidebar-brand{font-family:var(--font-heading);font-size:20px;padding:0 10px}
  .mem-sidebar-nav{display:flex;flex-direction:column;gap:3px}
  .mem-sidebar-item{display:flex;align-items:center;gap:11px;padding:9px 11px;border:1px solid transparent;cursor:pointer;font-size:14px;font-weight:600;color:var(--text-2);transition:background .2s,border-color .2s}
  .mem-sidebar-item:hover{background:color-mix(in srgb,var(--color-text) 6%,transparent)}
  .mem-sidebar-item.on{background:var(--color-bg);border-color:var(--color-divider);color:var(--color-text)}
  .mem-sidebar-glyph{width:20px;text-align:center;font-size:15px}
  /* flex:1 y no auto: es lo único de la columna que debe crecer y scrollear
     por su cuenta — nav y foot quedan fijos arriba/abajo (foot ya usa
     margin-top:auto, así que esto solo le hace lugar en el medio). */
  .mem-sidebar-ses-wrap{flex:1;min-height:0;display:flex;flex-direction:column;gap:8px}
  .mem-sidebar-ses-list{flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:1px;margin:0 -6px}
  .mem-sidebar-ses{padding:7px 10px;border-radius:var(--radius-md);cursor:pointer;font-size:12.5px;
    color:var(--text-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mem-sidebar-ses:hover{background:color-mix(in srgb,var(--color-text) 6%,transparent)}
  .mem-sidebar-ses.on{background:var(--color-bg);color:var(--color-text);font-weight:600}
  .mem-sidebar-foot{margin-top:auto;display:flex;flex-direction:column;gap:14px}
  /* la mascota, a lo ancho de la columna y sangrada hasta los filos */
  .mem-sidebar-alpaca{display:flex;justify-content:center;margin:0 -14px}
  /* el bloque de agentes como un plano ADELANTE: fondo opaco propio, línea de
     canto y sombra hacia ARRIBA. El margin-top negativo se come el gap de 14px
     del contenedor y 22px más, que es lo que de la alpaca queda tapado. */
  .mem-sidebar-capa{position:relative;z-index:1;margin:-36px -14px -28px;padding:15px 14px 28px;
    display:flex;flex-direction:column;gap:14px;background:var(--color-surface);
    border-top:1px solid var(--color-divider);
    box-shadow:0 -10px 18px -8px color-mix(in srgb,var(--color-text) 34%,transparent)}
  /* sin tabbar flotante en desktop, así que la lista no necesita despejar nada:
     solo un respiro para que el último ítem no quede pegado al filo. Esto valía
     antes para TODA .mem-screen y, como cada pantalla es una columna flex con su
     propio panel de scroll adentro, esos 40px eran una banda muerta al pie de la
     ventana — el "borde invisible" que se veía en Memory (pedido 2026-08-08). */
  .mem-pb-mem{padding-bottom:16px}
  .mem-set-wrap{padding:12px 24px 16px}  /* sin tabbar flotante en desktop */
  .mem-sheet-root{display:flex;align-items:center;justify-content:center;background:transparent}
  /* el panel medía 480px y 80vh para TODOS: una ficha con cuerpo, tags y
     conexiones entraba por un canal más angosto que la pantalla que la abrió, y
     de ahí salían los scrolls anidados. Ahora cada sheet dice cuánto quiere
     (--sheet-w) y la ventana pone el techo (pedido 2026-09-06). */
  .mem-sheet-panel{position:relative;left:auto;right:auto;bottom:auto;width:min(var(--sheet-w,720px),92vw);max-height:92vh!important;border-radius:var(--radius-lg);border:1px solid var(--color-divider);animation:pop .3s cubic-bezier(.22,1,.36,1)}
}
/* viewport bajo (celular apaisado, ventana corta): manda el ancho, no el alto. */
@media (max-height:560px){
  .mem-scr-head{padding:16px 20px 6px}
  .mem-chat-head{padding:14px 12px 6px}
  .mem-chat-foot{padding:8px 12px 12px}
  .mem-tabbar-scrim{height:74px}
  .mem-tabbar{height:48px}
  .mem-home-outer{padding:8px 18px 70px}
  .mem-home-content{max-width:900px;gap:8px}
  .mem-home-stage{min-height:0}
  /* apaisado: el alto es lo escaso, la mascota es lo primero que sobra */
  .mem-home-alpaca{display:none!important}
  .mem-home-banda{left:0}
  .mem-home-card{margin-top:82px}   /* dos filas de banda: clima/fecha/tema + proyecto */
}
@media (min-width:880px) and (max-height:560px){
  .mem-home-outer{padding:12px 28px 16px}
}
/* CELULAR — dos arreglos de la misma causa (reportado 2026-09-05).
   1. El "vN" al día se metía entre la fecha y los botones y apretaba la fila
      entera para decir algo que también está en Ajustes. El AVISO de
      actualización se queda: ese hay que verlo.
   2. La banda deja de flotar y EMPUJA el card. Flotando, el card despejaba con
      un margin-top fijo de 52px; cuando el aviso hacía wrapear la banda a dos
      filas, la segunda caía debajo del buscador y no se veía. En flujo el hueco
      lo mide el contenido, sea una fila o dos. El margin-left deja el gutter de
      la mascota, que sigue flotando (y se apoya detrás del card). */
@media (max-width:879px){
  .mem-home-ver.mem-ver-alDia{display:none}
  /* 58 y no 64: los 6px que sobran del gutter son los que le faltaban a la tira
     de clima para una tercera hora. La mascota mide 53 de ancho, o sea que sigue
     habiendo aire entre las dos. */
  .mem-home-banda{position:static;margin-left:58px}
  .mem-home-card{margin-top:8px}
  /* de la mascota asomaban 33px por encima del card y el resto quedaba tapado,
     con 40px de aire libre arriba sin usar. 74 la sube esos 16px sin sacarle la
     cabeza del stage (pedido 2026-09-06). */
  .mem-home-alpaca{top:74px}
}
@media (max-width:879px) and (max-height:560px){
  .mem-home-banda{margin-left:0}   /* apaisado: la mascota no está, no hay gutter que dejar */
}`;
if (!document.getElementById("mem-ui-css")) {
  const st = document.createElement("style"); st.id = "mem-ui-css"; st.textContent = CSS;
  document.head.appendChild(st);
}

// soltar un archivo fuera del chatbox no debe navegar a file:// y tumbar el SPA
// (el chatbox mismo previene el default en su propio handler, más específico).
if (!window.__memDragGuard) {
  window.__memDragGuard = true;
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());
}
