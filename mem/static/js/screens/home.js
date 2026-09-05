// MAIN — captura + buscador de sesiones. Pegar algo → Add (triaje → inbox).
// Escribir/dictar → ↵: crea una sesión temporal y entra en ella A PANTALLA
// COMPLETA (#chat). Home no muestra sesiones: el buscador de arriba es un panel
// flotante sobre el chatbox y abrir una de sus filas lleva a la vista completa.
import { html, useState, useEffect, useRef } from "../../vendor/preact-htm.js";
import { useStore, setState, go } from "../state.js";
import { dict, MODE_FALLBACK, fechaRelativa } from "../i18n.js";
import { capturar as encolarCaptura, get, post, del } from "../api.js";
import { Markdown } from "../md.js";
import { CornerBrackets, MicButton, Toast, useDictado, dictadoSoportado,
         useAgentes, agenteDe, BotonAgente, ConfirmarBorradoSesion,
         Camara, camaraSoportada, useProyectos, proyectosListos,
         AccionesAdjunto, norm,
         useAdjuntos, TiraAdjuntos, archivosDelPortapapeles, Alpaca, BTN_ICONO,
         IndicadorVersion } from "../ui.js";
import { usePrivado, privadosDe, esSesionPrivada, BotonVerPrivado } from "../privado.js";
import { Clima } from "../clima.js";

// botón cuadrado de la barra de acciones: 44px = el piso táctil del brief §7


// BORRADOR SIN GUARDAR (pedido 2026-08-05). Home se DESMONTA al cambiar de
// pantalla, así que su estado moría ahí: lo escrito, el adjunto y la conversación
// de triaje. Ahora sobrevive a cambiar de pantalla, a abrir y cerrar una sesión
// guardada y a recargar la app, y solo se va cuando se guarda (Add → memoria, ↵ →
// sesión) o se descarta a mano.
const BORRADOR = "mem.borrador";
// Los File elegidos no son serializables, así que viven en el módulo: sobreviven
// al desmontaje de Home (que es el caso frecuente) pero no a una recarga. Lo que
// sí sobrevive a todo son los adjuntos YA subidos, que son rutas.
let archivosVivos = [];

function leerBorrador() {
  try { return JSON.parse(localStorage.getItem(BORRADOR) || "null") || {}; }
  catch { return {}; }                        // localStorage corrupto: empezar limpio
}

function escribirBorrador(b) {
  try {
    if (b) localStorage.setItem(BORRADOR, JSON.stringify(b));
    else localStorage.removeItem(BORRADOR);
  } catch { /* modo privado o cuota llena: el borrador vive solo en memoria */ }
}

// fila de un resultado del buscador de sesiones
function FilaSes({ ses, modos, lang, theme, L, onOpen, onBorrar, privada }) {
  const modoDe = (nombre) => modos.find((m) => m.nombre === nombre) || MODE_FALLBACK[nombre] || MODE_FALLBACK.chat;
  const mo = modoDe(ses.modo);
  const arch = ses.estado === "archivada";
  // en qué modos se trabajó la sesión (meta modos_usados); las viejas solo traen el actual
  const usados = (ses.modos_usados || [ses.modo]).filter(Boolean);
  const caja = (m) => `background:color-mix(in srgb, ${m.color} ${theme === "dark" ? 28 : 16}%, transparent);color:${theme === "dark" ? "#f0c9a5" : m.color}`;
  return html`
    <div role="button" tabindex="0" onClick=${onOpen} class=${privada ? "mem-privada" : ""}
         style="display:flex;align-items:center;gap:10px;padding:11px 12px;border-radius:var(--radius-md);border:1px ${arch ? "dashed" : "solid"} var(--color-divider);background:var(--color-surface);margin-bottom:8px;cursor:pointer;opacity:${arch ? 0.75 : 1}">
      <span style="width:28px;height:28px;border-radius:var(--radius-md);flex-shrink:0;display:flex;align-items:center;justify-content:center;font-family:var(--font-mono);font-size:12px;${caja(mo)}">${arch ? "⌖" : (mo.glyph || "▮")}</span>
      ${privada && html`<span style="flex-shrink:0;font-size:12px" title=${L.tPriv}>⚿</span>`}
      <span style="flex:1;min-width:0">
        <span style="display:block;font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${ses.titulo || ses.id}</span>
        <span style="display:block;font-family:var(--font-mono);font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;opacity:.55;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${ses.modo} · ${ses.turnos || 0} ${L.tTurns}${(ses.subjects || []).length ? ` · ${ses.subjects.join(" · ")}` : ""}</span>
      </span>
      <span style="display:inline-flex;gap:3px;flex-shrink:0" title=${usados.join(" · ")}>
        ${usados.map((nombre) => html`
          <span style="width:17px;height:17px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-family:var(--font-mono);font-size:9px;${caja(modoDe(nombre))}">${modoDe(nombre).glyph || "▮"}</span>`)}
      </span>
      <span style="font-family:var(--font-mono);font-size:9.5px;opacity:.5;flex-shrink:0">${fechaRelativa(ses.actualizada, lang)}</span>
      <span role="button" tabindex="0" title=${L.tDelete} onClick=${(e) => { e.stopPropagation(); onBorrar(); }} class="mem-hit"
            style="width:26px;height:26px;flex-shrink:0;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:12px;cursor:pointer;opacity:.4">✕</span>
    </div>`;
}

export function Home() {
  const s = useStore();
  const L = dict(s.lang);
  // lo que quedó sin guardar la última vez (otra pantalla, otra sesión, o la app cerrada)
  const [previo] = useState(leerBorrador);
  const [texto, setTexto] = useState(previo.texto || "");
  // adjuntos del chatbox: rutas ya subidas del borrador + los File que sobrevivieron
  // al desmontaje. Mismo hook que el compositor de la sesión (pedido 2026-08-07).
  const adjs = useAdjuntos([...(previo.adjuntos || []), ...(previo.adjunto ? [previo.adjunto] : []), ...archivosVivos]);
  const [arrastrando, setArrastrando] = useState(false); // drag&drop sobre el chatbox
  const [saved, setSaved] = useState(false);    // false | "info" (toast)
  const [subiendo, setSubiendo] = useState(false);
  const [error, setError] = useState("");
  // conversación del triaje: [{rol:"diego"|"agente", texto}]. Vacía = captura limpia.
  const [conv, setConv] = useState(previo.conv || []);
  const [pensando, setPensando] = useState(false);
  const [manual, setManual] = useState(!!previo.manual); // true = tecleado/dictado; pegar no cuenta
  // buscador de sesiones (arriba del chatbox)
  const [busca, setBusca] = useState("");
  const [abierto, setAbierto] = useState(false);    // panel de sesiones desplegado
  const [sesiones, setSesiones] = useState(null);   // null = sin cargar
  const [archivadas, setArchivadas] = useState([]);
  const [modos, setModos] = useState([]);
  const [porBorrar, setPorBorrar] = useState(null);
  const [camara, setCamara] = useState(false);
  const fileRef = useRef(null);
  const camRef = useRef(null);
  const dragDepth = useRef(0);
  const buscaRef = useRef(null);   // caja + panel: un clic afuera lo cierra
  const inputRef = useRef(null);
  const arrancando = useRef(false);  // ya se está abriendo la sesión: no abrir otra por tecla
  const textoRef = useRef("");       // lo tecleado AHORA (el state llega un render tarde)
  const agentes = useAgentes();
  const agente = agenteDe(agentes, "chat");
  const proyecto = String(s.proyecto || "");
  // candado de lo privado: en el celular sin verificar, las sesiones de un
  // proyecto privado no aparecen; hasta cargar /projects se oculta todo
  const { oculto } = usePrivado();
  const proyectosTodos = useProyectos();
  const privs = privadosDe(proyectosTodos);
  const esPriv = (ses) => esSesionPrivada(ses, privs);
  // Dos candados distintos: el del celular (arriba) tapa TODO lo privado hasta
  // verificar; este otro es la regla del 2026-08-12 y vale también en escritorio
  // — lo privado no sale de su proyecto, ni siquiera pidiendo "Todos".
  const fuera = (ses) => esPriv(ses) && String(ses.proyecto || "") !== proyecto;
  const ocultar = (ses) => (oculto && (!proyectosListos() || esPriv(ses))) || fuera(ses);
  // dictar también es "decirle algo al asistente": enciende el ↵ igual que teclear
  const dictado = useDictado(s.voiceLang || (s.lang === "en" ? "en-US" : "es-ES"),
    (t) => { setTexto((p) => (p ? p + " " : "") + t); setManual(true); });

  textoRef.current = texto;   // pegado, dictado, borrador restaurado: el ref sigue al state
  const hoy = new Date();
  const dark = s.theme === "dark";
  const buscando = busca.trim().length > 0;
  // panel visible = se tocó el buscador o hay texto. Ya no depende del foco:
  // perderlo por un re-render cerraba la lista sola.
  const lista = buscando || abierto;
  const enTriaje = conv.length > 0;

  useEffect(() => { get("/modes").then(setModos).catch(() => {}); }, []);

  // el buscador carga las listas la primera vez que se usa (foco o texto)
  useEffect(() => {
    if (!lista || sesiones !== null) return;
    get("/sessions").then(setSesiones).catch(() => setSesiones([]));
    get("/sessions?archivadas=1").then(setArchivadas).catch(() => {});
  }, [lista]);

  // cerrar el panel es un clic AFUERA, no un blur: el foco se pierde con
  // cualquier re-render (y ahí la lista se cerraba sola — bug reportado).
  useEffect(() => {
    if (!lista) return;
    const fuera = (e) => { if (!buscaRef.current?.contains(e.target)) cerrarBusca(); };
    document.addEventListener("pointerdown", fuera);
    return () => document.removeEventListener("pointerdown", fuera);
  }, [lista]);
  // el back del celular cierra el panel en vez de salir de la pantalla: se
  // apila un estado propio y el cleanup lo saca si se cerró por otra vía
  // (navegar a una sesión ya dejó otro estado encima → no hay nada que sacar).
  useEffect(() => {
    if (!lista) return;
    history.pushState({ memBusca: 1 }, "");
    const pop = () => { setAbierto(false); setBusca(""); };
    addEventListener("popstate", pop);
    return () => {
      removeEventListener("popstate", pop);
      if (history.state?.memBusca) history.back();
    };
  }, [lista]);
  // desplegado y sin texto: el cursor va al input aunque el nodo sea nuevo
  useEffect(() => { if (abierto) inputRef.current?.focus(); }, [abierto]);

  function cerrarBusca() { setAbierto(false); setBusca(""); }

  /** Abrir una sesión = trabajar en ella a pantalla completa. El estado que apiló
   *  el panel se reemplaza en vez de quedar debajo: volver de la sesión no debe
   *  dejar un back de más que no hace nada. */
  function abrirSesion(id) {
    if (history.state?.memBusca) history.replaceState(null, "");
    cerrarBusca();
    go("chat", id);
  }

  /** El botón de pegar: primero mira si el portapapeles trae un ARCHIVO (una
   *  captura de pantalla, un audio) y lo adjunta; si no, pega el texto. Antes solo
   *  leía texto y una captura copiada se perdía (pedido 2026-08-07). */
  async function paste() {
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
      const texto2 = await navigator.clipboard.readText();
      if (texto2) setTexto((p) => (p ? p + "\n" : "") + texto2);
    } catch { /* sin permiso de portapapeles: no-op */ }
  }

  function abrirArchivo() { fileRef.current?.click(); }
  function onArchivo(e) {
    adjs.agregar(e.target.files);
    e.target.value = "";
  }
  // sin getUserMedia (PWA por http:// en la LAN) el input con capture abre la
  // cámara nativa del celular igual: mismo botón, un camino u otro
  function abrirCamara() { if (camaraSoportada()) setCamara(true); else camRef.current?.click(); }

  // drag&drop del chatbox: mismo destino que el picker de arriba
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
    adjs.agregar(e.dataTransfer.files);
  }

  function limpiar() {
    setTexto(""); adjs.limpiar(); setConv([]);
    setManual(false); setError("");
    // sincrónico además del efecto: con ↵ (preguntar) Home se desmonta en el mismo
    // commit y el efecto no llegaría a correr, así que el borrador volvería al
    // cerrar la sesión que se acaba de crear con él.
    archivosVivos = [];
    escribirBorrador(null);
  }

  // Persistir el borrador en cada cambio. Va acá y no dentro de cada setter para
  // que ninguna vía de edición nueva se olvide de guardar: si el estado cambió,
  // el borrador ya está al día. Vacío = se borra, así no queda basura ni un
  // "hay algo sin guardar" fantasma.
  const hayBorrador = !!(texto.trim() || adjs.items.length || conv.length);
  useEffect(() => {
    archivosVivos = adjs.items.filter((it) => it.file && !it.ruta).map((it) => it.file);
    escribirBorrador(hayBorrador ? { texto, adjuntos: adjs.rutas, conv, manual } : null);
  }, [texto, adjs.items, conv, manual, hayBorrador]);

  // El proyecto viaja EN la captura y no en la cabecera: la cola es offline y
  // puede vaciarse horas después, ya parado en otro proyecto — lo que vale es
  // dónde se capturó (pedido 2026-08-12).
  function encolar(payload, avisoProyecto = "") {
    encolarCaptura({ proyecto, ...payload });
    limpiar();
    setSaved(avisoProyecto || "info");
    setTimeout(() => setSaved(false), 2400);
  }

  /** Add: el agente revisa la captura y decide si guardarla o preguntar algo.
   *  Sin servidor (o si el triaje falla) se encola tal cual: cero pérdida. */
  async function guardar() {
    if (!texto.trim() && !adjs.items.length) return;
    setError("");

    let rutas = [];
    if (adjs.items.length) {
      setSubiendo(true);
      try { rutas = await adjs.subir(); }
      catch (e) { setSubiendo(false); setError(String(e)); return; }
      setSubiendo(false);
    }
    // ponytail: una captura del inbox tiene UN `adjunto` en su frontmatter, así que
    // el primero va ahí y los demás quedan como markdown en el cuerpo — se ven
    // igual en la ficha y en la memoria que salga de ella. Plural de verdad sería
    // tocar el modelo del inbox entero (procesar, lint, la pantalla de entrada).
    const adjunto = rutas[0] || "";
    const texto2 = [texto, ...rutas.slice(1).map((r) => `![${r.split("/").pop()}](/attach/${r})`)]
      .filter(Boolean).join("\n\n");

    const mensajes = [...conv, { rol: "diego", texto: texto2 }];
    const crudo = mensajes.find((m) => m.rol === "diego").texto;
    setConv(mensajes); setTexto(""); setPensando(true);
    setState({ agenteTrabajando: agente?.id || null });
    try {
      const r = await post("/capture/triage", { mensajes, lang: s.lang, adjunto, proyecto });
      // "guardá esto en el proyecto X" dicho al agente manda la captura ahí SIN
      // mover a Diego de dónde está parado — antes cambiaba el proyecto activo
      // en silencio; ahora un toast avisa a dónde fue (pedido 2026-09-05).
      if (r.guardar) { encolar(r.payload, r.proyecto && r.proyecto !== proyecto ? r.proyecto : ""); }
      else { setConv([...mensajes, { rol: "agente", texto: r.mensaje }]); }
    } catch (e) {
      // el agente no contestó: la captura no se pierde, va al inbox tal cual
      encolar({ contenido: crudo, tipo: "nota", contexto: "", tags: [], subjects: [], adjunto, origen: "app" });
      setError(String(e));
    } finally {
      setPensando(false);
      setState({ agenteTrabajando: null });
    }
  }

  /** Salida de emergencia de la conversación: guarda lo capturado sin más vueltas. */
  function guardarTalCual() {
    const crudo = conv.find((m) => m.rol === "diego")?.texto || texto;
    const rutas = adjs.rutas;
    if (!crudo.trim() && !rutas.length) return;
    encolar({ contenido: crudo, tipo: "nota", contexto: "", tags: [], subjects: [],
              adjunto: rutas[0] || "", origen: "app" });
  }

  /** Abre una sesión NUEVA con lo que haya en el chatbox — siempre nueva, aunque
   *  haya una activa. Escribir en el chatbox de Home es empezar algo; para seguir
   *  la sesión activa está su fila de arriba (un toque entra al chat), que es donde
   *  Diego espera continuarla. La nueva pasa a ser la activa sola: chat.js la marca
   *  al montar, y el servidor la nombra con su fecha y hora.
   *
   *  `enviar` (↵): chat.js manda el turno solo al montar. Sin él lo escrito y lo
   *  subido llegan como BORRADOR y Diego sigue tecleando allá — teclear en el
   *  chatbox ya es empezar una sesión (pedido 2026-08-10). La sesión nace
   *  `temporal`: si la deja sin decir nada, no ensucia la lista.
   *
   *  El texto se lee al final y no al entrar: lo que se teclea mientras suben los
   *  adjuntos (o mientras el servidor crea la sesión) también viaja. */
  async function arrancarSesion(enviar) {
    if (arrancando.current || pensando) return;
    if (!textoRef.current.trim() && !adjs.items.length) return;
    arrancando.current = true;
    setError("");
    // los adjuntos se suben una vez y viajan con el turno: el servidor los lee con
    // el mismo lector del inbox y los pega al mensaje, así quedan en el historial
    let rutas = [];
    if (adjs.items.length) {
      setSubiendo(true);
      try { rutas = await adjs.subir(); }
      catch (e) { setSubiendo(false); setError(String(e)); arrancando.current = false; return; }
      setSubiendo(false);
    }
    let id;
    try {
      // titulo:"" = que la nombre el servidor con la fecha y hora (pedido 2026-08-05)
      id = (await post("/sessions", { subjects: [], titulo: "", modo: "chat", temporal: true, proyecto })).id;
    } catch (e) { setError(String(e)); arrancando.current = false; return; }
    sessionStorage.setItem(`mem.${enviar ? "pending" : "draft"}.${id}`,
                           JSON.stringify({ texto: textoRef.current.trim(), adjuntos: rutas }));
    limpiar();   // ya vive como sesión: el borrador no debe volver al salir de ella
    go("chat", id);
  }
  const preguntar = () => arrancarSesion(true);

  /** Abre el diálogo de borrado sabiendo qué memorias produjo esa sesión (para
   *  poder ofrecer "la sesión y sus memorias"). */
  async function pedirBorrado(ses) {
    setPorBorrar({ ...ses, memorias: [], adjuntos: [] });
    try {
      const r = await get(`/sessions/${ses.id}`);
      setPorBorrar((p) => (p && p.id === ses.id ? { ...p, memorias: r.memorias || [], adjuntos: r.adjuntos || [] } : p));
    } catch { /* sin la lista, el diálogo ofrece solo borrar la sesión */ }
  }

  async function borrarSesion(ses, conContenido = false) {
    await del(`/sessions/${ses.id}${conContenido ? "?contenido=1" : ""}`).catch(() => {});
    setSesiones((p) => (p || []).filter((x) => x.id !== ses.id));
    setArchivadas((p) => p.filter((x) => x.id !== ses.id));
    setPorBorrar(null);
  }

  function recargarSesiones() {
    get("/sessions").then(setSesiones).catch(() => {});
    get("/sessions?archivadas=1").then(setArchivadas).catch(() => {});
  }

  // el proyecto ahora se elige en el sidebar/tabbar (pedido 2026-08-31): las
  // sesiones se recargan porque el editor pudo renombrarlo, unirlo o borrarlo
  // (Home queda montado detrás del sidebar). El buscador ya no filtra por
  // proyecto aparte: `ocultar` sola decide qué se ve, parado donde se esté
  // (pedido 2026-09-05 — el proyecto activo ES el filtro, sin un segundo estado).
  useEffect(() => {
    if (sesiones !== null) recargarSesiones();
  }, [s.proyecto]);

  const q = norm(busca.trim());
  const hits = !q ? [] : [...(sesiones || []), ...archivadas].filter((x) =>
    norm([x.titulo, x.modo, (x.subjects || []).join(" ")].join(" ")).includes(q) && !ocultar(x));
  const hayPrivadas = oculto && proyectosListos() &&
    [...(sesiones || []), ...archivadas].some(esPriv);

  // foco sin texto: las últimas sesiones de nueva a vieja, agrupadas por proyecto
  // (los grupos quedan ordenados por su sesión más reciente, no alfabéticamente)
  const grupos = [];
  if (!q && abierto) {
    const recientes = [...(sesiones || [])].filter((x) => !ocultar(x))
      .sort((a, b) => String(b.actualizada || "").localeCompare(String(a.actualizada || "")))
      .slice(0, 15);
    for (const ses of recientes) {
      const nombre = String(ses.proyecto || "");
      (grupos.find((g) => g.nombre === nombre) || grupos[grupos.push({ nombre, sesiones: [] }) - 1]).sesiones.push(ses);
    }
  }

  function onKey(e) {
    if (e.key !== "Enter" || e.shiftKey) return;
    if (!enTriaje && manual) { e.preventDefault(); preguntar(); }
    else if (enTriaje) { e.preventDefault(); guardar(); }
    // pegado limpio: Enter escribe una línea nueva, como siempre
  }

  const bordeCard = arrastrando ? "dashed var(--color-accent)" : "solid var(--color-divider)";
  const fondoCard = arrastrando ? "color-mix(in srgb,var(--color-accent) 10%,var(--color-surface))" : "var(--color-surface)";

  return html`
    <div class="mem-home-outer" style="position:relative;flex:1;overflow:auto;display:flex;flex-direction:column;align-items:center;animation:scIn .42s cubic-bezier(.22,1,.36,1)">
      <div class="mem-home-content">
        <div class="mem-home-stage">
          <!-- el saludo ("Buenas tardes. …") se quitó (pedido 2026-08-05): esa banda
               ahora es aire, y el buscador + chatbox crecen hacia arriba en su lugar.
               El texto sigue en i18n (greetings/welcomes) por si vuelve. -->
          <!-- La mascota vuelve, chica y en la misma banda superior que la fecha
               (pedido 2026-08-08). Solo en móvil: en escritorio vive en el
               sidebar, y dos alpacas a la vez serían una de más. -->
          <${Alpaca} clase="mem-home-alpaca" alto=${72} />
          <div class="mem-home-banda">
            <!-- acá decía "22° Soleado" SIEMPRE, en cualquier lugar y estación: un
                 stub sin API de clima que se leía como un dato real. Lo reemplazó
                 un sol decorativo, y desde 2026-08-11 el pronóstico de verdad:
                 cuántas horas se ven depende del ancho, y sin permiso de
                 ubicación no aparece nada (clima.js). -->
            <${Clima} lang=${s.lang} />
            <!-- día · mes · día de la semana en dos líneas y no en tres: la banda
                 no puede crecer, se la come el margin-top del chatbox -->
            <span style="flex-shrink:0;display:flex;align-items:center;gap:5px">
              <span style="font-family:var(--font-heading);font-size:23px;letter-spacing:-.01em">${hoy.getDate()}</span>
              <span style="display:flex;flex-direction:column;line-height:1.15;font-family:var(--font-mono);font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--text-3)">
                <span>${hoy.toLocaleDateString(s.lang === "en" ? "en-GB" : "es-ES", { weekday: "short" })}</span>
                <span>${hoy.toLocaleDateString(s.lang === "en" ? "en-GB" : "es-ES", { month: "short" })}</span>
              </span>
            </span>
            <${IndicadorVersion} lang=${s.lang} />
            <div role="button" tabindex="0" onClick=${() => setState({ theme: dark ? "light" : "dark", themePref: dark ? "light" : "dark" })}
                 style="width:44px;height:44px;flex-shrink:0;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;justify-content:center;font-size:15px;cursor:pointer;background:var(--color-surface)">
              ${dark ? "☾" : "☀"}
            </div>
            <div role="button" tabindex="0" onClick=${() => go("settings")}
                 style="width:44px;height:44px;flex-shrink:0;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;justify-content:center;font-size:15px;cursor:pointer;background:var(--color-surface)">
              ⚙
            </div>
          </div>

          <div class="mem-home-card" ref=${buscaRef} style="position:relative;z-index:2;flex:1;min-height:0;display:flex;flex-direction:column;gap:9px">
            <!-- BUSCADOR: vive en la banda de la alpaca, indentado a su derecha
                 para no taparla. Sus resultados son un panel flotante que cae
                 sobre el chatbox: ni el input ni la alpaca se mueven. Cerrar es
                 un clic afuera, Escape o el back del celular — nunca un blur. -->
            <div class="mem-home-busca">
              <div style="display:flex;align-items:center;gap:9px;height:44px;border-radius:var(--radius-md);background:var(--color-surface);border:1px solid ${lista ? "color-mix(in srgb,var(--color-accent) 55%,var(--color-divider))" : "var(--color-divider)"};padding:0 12px;box-shadow:var(--shadow-sm)">
                <span role="button" tabindex="0" title=${L.phSearch} onClick=${() => setAbierto(true)}
                      style="font-family:var(--font-mono);opacity:.55;font-size:13px;cursor:pointer;flex-shrink:0">⌕</span>
                <input ref=${inputRef} value=${busca} onInput=${(e) => setBusca(e.target.value)}
                       onFocus=${() => setAbierto(true)} onClick=${() => setAbierto(true)}
                       onKeyDown=${(e) => { if (e.key === "Escape") cerrarBusca(); }}
                       placeholder=${L.phSearch}
                       style="flex:1;min-width:0;border:0;background:transparent;outline:none;font-family:var(--font-body);font-size:14.5px;color:var(--color-text)" />
                ${lista && html`
                  <span role="button" tabindex="0" onClick=${cerrarBusca} style="cursor:pointer;opacity:.45;font-size:12px;flex-shrink:0">✕</span>`}
              </div>
            </div>

            ${lista && html`
              <div class="mem-home-res">
                  ${sesiones === null && html`<div style="opacity:.5;font-size:13px;padding:8px 4px">…</div>`}
                  ${sesiones !== null && buscando && !hits.length && html`
                    <div style="padding:22px 16px;text-align:center;border-radius:var(--radius-md);background:var(--color-bg);border:1px solid var(--color-divider)">
                      <div style="font-family:var(--font-heading);font-size:16px;margin-bottom:4px">${L.tNoRes}</div>
                      <div style="font-size:12.5px;color:var(--text-3)">${L.tNoResSub}</div>
                    </div>`}
                  ${buscando && hits.map((ses) => html`
                    <${FilaSes} key=${ses.id} ses=${ses} modos=${modos} lang=${s.lang} theme=${s.theme} L=${L} privada=${esPriv(ses)}
                                onOpen=${() => abrirSesion(ses.id)} onBorrar=${() => pedirBorrado(ses)} />`)}
                  ${!buscando && grupos.map((g) => html`
                    <div style="display:flex;align-items:center;gap:6px;padding:8px 4px 5px;font-family:var(--font-mono);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--text-3)">
                      ◈ ${g.nombre || L.tProjAll} <span style="opacity:.6">· ${g.sesiones.length}</span>
                    </div>
                    ${g.sesiones.map((ses) => html`
                      <${FilaSes} key=${ses.id} ses=${ses} modos=${modos} lang=${s.lang} theme=${s.theme} L=${L} privada=${esPriv(ses)}
                                  onOpen=${() => abrirSesion(ses.id)} onBorrar=${() => pedirBorrado(ses)} />`)}`)}
                  ${hayPrivadas && html`
                    <div style="display:flex;justify-content:center;padding:6px 0 10px">
                      <${BotonVerPrivado} lang=${s.lang} />
                    </div>`}
                </div>`}

            <!-- SESIÓN ACTIVA (pedido 2026-08-05): la sesión en la que se está
                 trabajando no se pierde al salir del chat — ni cambiando de
                 pantalla ni cerrando la app. Un toque vuelve a ella y el ↵ de
                 abajo escribe ahí; el ✕ la suelta para empezar una limpia. -->
            ${s.sesionActiva && !lista && html`
              <div role="button" tabindex="0" onClick=${() => go("chat", s.sesionActiva.id)}
                   style="display:flex;align-items:center;gap:9px;height:36px;padding:0 10px 0 12px;border-radius:var(--radius-md);cursor:pointer;background:var(--color-surface);border:1px solid color-mix(in srgb,var(--color-accent) 50%,var(--color-divider));box-shadow:var(--shadow-sm)">
                <span style="flex-shrink:0;width:7px;height:7px;border-radius:var(--radius-md);background:var(--color-accent);animation:breathe 2.4s ease-in-out infinite"></span>
                <span style="flex-shrink:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--font-mono);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--text-3)">${L.tActiveSession} · ${s.sesionActiva.proyecto || L.tProjAll}</span>
                <span style="flex:1;min-width:0;font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${s.sesionActiva.titulo}</span>
                <!-- el ✕ vive DENTRO de la fila (antes colgaba afuera y le comía 32px
                     de ancho: la línea no alineaba con el chatbox de abajo). Ocupa el
                     lugar del ‹›› — no pueden convivir: .mem-hit le da 13px de área
                     invisible alrededor y se tragaba el toque de "entrar al chat". -->
                <span role="button" tabindex="0" title=${L.tCloseSession} class="mem-hit"
                      onClick=${(e) => { e.stopPropagation(); setState({ sesionActiva: null }); }}
                      style="flex-shrink:0;width:24px;height:24px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:12px;line-height:1;cursor:pointer;border:1px solid var(--color-divider);background:color-mix(in srgb,var(--color-text) 8%,transparent)">✕</span>
              </div>`}

            <!-- La conversación del triaje ocupa el lugar donde antes estaba el
                 chatbox entero: es el diálogo, y el diálogo va arriba del que
                 escribe — igual que en una sesión. -->
            ${enTriaje && html`
              <div style="flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:10px;padding-right:4px">
                ${conv.map((m) => html`
                  <div class=${m.rol === "diego" ? "mem-yo mem-burb-yo" : "mem-burb-mem"} style="font-size:14.5px;line-height:1.5;animation:pop .28s cubic-bezier(.22,1,.36,1)">
                    ${m.rol === "agente" ? html`<${Markdown} texto=${m.texto} />` : html`<span style="white-space:pre-wrap">${m.texto}</span>`}
                  </div>`)}
                ${pensando && html`
                  <div style="align-self:flex-start;font-family:var(--font-mono);font-size:11px;opacity:.55">${L.tThinking}</div>`}
              </div>`}
          </div>
        </div>

        <!-- EL CHATBOX, AL PIE (pedido 2026-08-12). Antes era una tarjeta alta
             anclada arriba, así que la primera letra —que abre una sesión— hacía
             saltar el cursor de media pantalla al pie, donde vive el compositor
             de la sesión. Ahora los dos están en el mismo sitio y con las mismas
             clases (.mem-comp/.mem-comp-in de ui.js): la transición no se nota.
             Paridad exacta es imposible, Home lleva tabbar y la sesión no. -->
        <div onDragEnter=${onDragEnter} onDragOver=${onDragOver} onDragLeave=${onDragLeave} onDrop=${onDrop}
             style="position:relative;flex-shrink:0;display:flex;flex-direction:column;gap:8px;background:${fondoCard};border:1px ${bordeCard};border-radius:var(--radius-md);padding:12px 14px 11px;box-shadow:var(--shadow-lg);transition:background .15s,border-color .15s">
          <${CornerBrackets} corners="all" color="var(--color-accent)" />
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-family:var(--font-mono);font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--text-3)">
            <span class="mem-home-etiq">▸ ${L.tEntryLabel}</span>
            <${BotonAgente} pensando=${pensando} lang=${s.lang} />
            <!-- Empezar de cero: el borrador sobrevive a todo salvo guardarlo
                 o descartarlo, así que tiene que haber una forma de descartarlo.
                 En triaje ya existe el botón Descartar, y v39 pidió que Home no
                 se sienta atiborrada: acá va un ✕ chico, solo si hay algo. -->
            ${hayBorrador && !enTriaje && html`
              <span role="button" tabindex="0" onClick=${limpiar} title=${L.tDiscard}
                    class="mem-hit" style="margin-left:auto;cursor:pointer;font-size:12px;opacity:.5;padding:0 2px">✕</span>`}
          </div>
          ${enTriaje && html`
            <div style="display:flex;gap:8px;flex-shrink:0">
              <div role="button" tabindex="0" onClick=${guardarTalCual}
                   style="padding:7px 13px;border-radius:var(--radius-md);border:1px solid var(--color-divider);cursor:pointer;font-family:var(--font-mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--text-2)">${L.tSaveAnyway}</div>
              <div role="button" tabindex="0" onClick=${limpiar}
                   style="padding:7px 13px;border-radius:var(--radius-md);border:1px solid var(--color-divider);cursor:pointer;font-family:var(--font-mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--text-3)">${L.tDiscard}</div>
            </div>`}
          <!-- lo adjuntado: miniatura y ✕, nada más (pedido 2026-08-07). La
               vista previa grande con nombre y peso se comía media tarjeta y
               con dos archivos no entraba. -->
          <${TiraAdjuntos} items=${adjs.items} onQuitar=${adjs.quitar} subiendo=${subiendo} tam=${60} />
          <div class="mem-comp">
            ${dictadoSoportado && html`<${MicButton} active=${dictado.activo} onClick=${dictado.toggle} size=${44} />`}
            <${AccionesAdjunto} onPegar=${paste} onClip=${abrirArchivo} onCamara=${abrirCamara}
                                estilo=${BTN_ICONO} tam=${18} lang=${s.lang} />
            <input type="file" multiple ref=${fileRef} style="display:none" onChange=${onArchivo} />
            <input type="file" ref=${camRef} accept="image/*,video/*" capture="environment" style="display:none" onChange=${onArchivo} />
            <div class="mem-comp-in">
              <textarea value=${texto}
                        onInput=${(e) => {
                          const v = e.target.value;
                          setTexto(v);
                          textoRef.current = v;
                          if (!v.trim()) setManual(false);  // se borró todo: el ↵ se va
                          // tecleo real (no pegado; el dictado enciende manual por su lado)
                          else if (e.inputType?.startsWith("insert") && e.inputType !== "insertFromPaste") {
                            setManual(true);
                            // ...y teclear ES empezar una sesión: se abre una nueva y lo
                            // escrito y subido sigue allá (pedido 2026-08-10). En medio de
                            // una charla de triaje no: ahí se le está contestando al agente.
                            if (!enTriaje) arrancarSesion(false);
                          }
                        }}
                        onKeyDown=${onKey}
                        onPaste=${(e) => { if (adjs.agregar(archivosDelPortapapeles(e))) e.preventDefault(); }}
                        placeholder=${L.phCapture} rows=${Math.min(4, texto.split("\n").length)}
                        style="width:100%;display:block;padding:0;border:0;background:transparent;resize:none;outline:none;font-family:var(--font-body);font-size:16px;line-height:1.45;color:var(--color-text);max-height:96px"></textarea>
            </div>
            <div role="button" tabindex="0" onClick=${subiendo || pensando ? null : guardar} title=${L.tStore}
                 class=${manual && !enTriaje ? "mem-btn-icon" : "mem-btn-accent"}
                 style="${BTN_ICONO};font-size:17px;box-shadow:var(--shadow-md);opacity:${subiendo || pensando ? 0.6 : 1}">
              ${subiendo || pensando ? "…" : html`
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.9" stroke-linecap="round" style="display:block"><path d="M12 5.5v13"></path><path d="M5.5 12h13"></path></svg>`}
            </div>
            ${manual && !enTriaje && html`
              <div role="button" tabindex="0" onClick=${pensando ? null : preguntar} class="mem-btn-accent" title=${L.tAsk}
                   style="${BTN_ICONO};font-size:18px;box-shadow:var(--shadow-md);opacity:${pensando ? 0.6 : 1};animation:pop .3s cubic-bezier(.22,1,.36,1)">↵</div>`}
          </div>
          ${!!s.queuePendientes && html`
            <div style="font-family:var(--font-mono);font-size:10.5px;color:var(--text-3)">
              ⟳ ${s.queuePendientes} ${s.lang === "en" ? "pending to sync" : "pendientes de sincronizar"}
            </div>`}
        </div>
        <!-- acá vivía la tira de agentes + servicios locales. Se borró entera
             (pedido 2026-08-11): todo eso está en el menú del chip de agente, que
             es el botón de la izquierda del chatbox — y el renglón que ocupaba se
             lo quedó el chatbox, que es lo escaso en esta pantalla. -->
        ${error && html`<div style="text-align:center;font-size:12px;color:var(--color-accent-700)">${error}</div>`}
      </div>
      ${porBorrar && html`
        <${ConfirmarBorradoSesion} ses=${porBorrar} lang=${s.lang}
          memorias=${porBorrar.memorias || []} adjuntos=${porBorrar.adjuntos || []}
          onClose=${() => setPorBorrar(null)}
          onBorrar=${(conContenido) => borrarSesion(porBorrar, conContenido)} />`}
      ${camara && html`
        <${Camara} lang=${s.lang} onClose=${() => setCamara(false)}
                   onListo=${(f) => { adjs.agregar([f]); setCamara(false); }} />`}
    </div>
    ${saved && html`<${Toast}>${saved === "info" ? L.tSavedToast : `${L.tTriageProj} ${saved}`}<//>`}
  `;
}
