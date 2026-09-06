// Cliente HTTP: fetch JSON, lector SSE, y la cola offline de capturas
// ("cero pérdida" — spec §8.1: toda captura persiste local antes de la red).
import { getState, setState } from "./state.js";
import { candadoAbierto } from "./privado.js";

function headers(extra) {
  const token = localStorage.getItem("mem.token") || "";
  // Dónde está parado quien pregunta (pedido 2026-08-12). Va en TODAS las
  // llamadas y no como query param de las ocho que lo usan: no es un filtro de
  // la consulta sino el contexto de quien la hace, y así ninguna pantalla tiene
  // que acordarse de mandarlo. El server lo usa para una sola cosa: lo privado
  // de otro proyecto no se ve. "-" = Sin proyecto (una cabecera vacía no viaja
  // distinto de una ausente, y ausente significa "sin contexto": MCP, curl).
  // X-Privado: el candado de ESTE aparato, abierto (o sea, ya hubo huella). El
  // muro del server acota lo privado al proyecto donde uno está parado, que es
  // lo correcto para el MCP y el CLI; para la app, verificarse es justamente
  // pedir verlo todo. Sin esta cabecera había que pararse en cada proyecto
  // privado para que su contenido apareciera en la búsqueda (pedido 2026-09-06).
  return { "Content-Type": "application/json", "X-Proyecto": getState().proyecto || "-",
           ...(candadoAbierto() ? { "X-Privado": "1" } : {}),
           ...(token ? { Authorization: `Bearer ${token}` } : {}), ...extra };
}

// El navegador solo avisa si el DISPOSITIVO pierde red; que el server no
// conteste (Tailscale apagado en el celu, tray caído) solo se ve acá. Al primer
// fallo se marca offline y se sondea /health hasta que vuelva — la UI dice
// "sin conexión con el servidor" en vez de un TypeError crudo.
let sondeo = null;
function marcarOffline() {
  setState({ online: false });
  if (sondeo) return;
  sondeo = setInterval(() => {
    fetch("/health").then((r) => {
      if (!r.ok) return;
      clearInterval(sondeo); sondeo = null;
      setState({ online: true });
      flushQueue();
    }).catch(() => {});
  }, 5000);
}
const sinRed = () => new Error("sin conexión con el servidor");

async function j(path, opts = {}) {
  let r;
  try {
    r = await fetch(path, { ...opts, headers: headers(opts.headers) });
  } catch {
    marcarOffline();
    throw sinRed();
  }
  if (!r.ok) {
    const cuerpo = await r.text().catch(() => "");
    // FastAPI manda {"detail": "..."} — sin esto la pantalla mostraba el JSON crudo
    let msg = cuerpo;
    try { msg = JSON.parse(cuerpo).detail || cuerpo; } catch { /* texto plano */ }
    throw new Error(msg || `${r.status} ${r.statusText}`);
  }
  return r.status === 204 ? null : r.json();
}

export const get = (path) => j(path);
export const post = (path, body) => j(path, { method: "POST", body: JSON.stringify(body ?? {}) });
export const patch = (path, body) => j(path, { method: "PATCH", body: JSON.stringify(body ?? {}) });
export const del = (path) => j(path, { method: "DELETE" });

export async function postAttach(file) {
  const r = await fetch(`/attach?nombre=${encodeURIComponent(file.name)}`, {
    method: "POST", headers: headers({ "Content-Type": "application/octet-stream" }), body: file,
  });
  if (!r.ok) throw new Error(await r.text().catch(() => "no se pudo subir el adjunto"));
  return r.json();
}

/** Un adjunto de la base como File, para poder mandarlo por el share nativo
 *  (navigator.share necesita el archivo en mano, no una URL). */
export async function bajarAdjunto(ruta) {
  const r = await fetch(`/attach/${ruta}`, { headers: headers() });
  if (!r.ok) throw new Error("no se pudo leer el adjunto");
  const blob = await r.blob();
  return new File([blob], ruta.split("/").pop(), { type: blob.type || "application/octet-stream" });
}

// --- SSE: POST /sessions/{sid}/messages -----------------------------------
// El server manda los fallos ya traducidos ({problema, sugerencia, detalle} —
// mem/fallas.py); los de acá (sin red, HTTP crudo) se visten igual para que el
// chat tenga una sola forma que pintar.
const comoFalla = (x) => (typeof x === "string" ? { problema: x, sugerencia: "", detalle: x } : x);

export async function streamMessage(sid, texto, { onTool, onToolFin, onDone, onError, onConfirm, onCancel, signal, adjuntos = [], agente = "" }) {
  let r;
  try {
    r = await fetch(`/sessions/${sid}/messages`, { method: "POST", headers: headers(), body: JSON.stringify({ texto, adjuntos, agente }), signal });
  } catch (e) {
    // abortado al salir del chat: el turno sigue vivo en el server y se recupera
    // al volver (GET /turn) — no es un error que haya que mostrarle a nadie
    if (e?.name === "AbortError") return;
    marcarOffline(); onError?.(comoFalla(sinRed().message)); return;
  }
  if (!r.ok || !r.body) { onError?.(comoFalla(await r.text().catch(() => "sin respuesta del servidor"))); return; }
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  while (true) {
    let done, value;
    try { ({ done, value } = await reader.read()); } catch { return; }   // abort
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const bloque = buf.slice(0, i); buf = buf.slice(i + 2);
      const ev = /event: (\w+)/.exec(bloque)?.[1];
      const data = JSON.parse(/data: (.+)/s.exec(bloque)?.[1] || "{}");
      if (ev === "tool") onTool?.(data);
      // esa tool terminó: sin esto una generación de 20 min seguía "corriendo"
      // en pantalla mientras el turno ya estaba catalogando
      else if (ev === "tool_fin") onToolFin?.(data);
      // una tool que gasta créditos: el turno quedó esperando el diálogo
      else if (ev === "confirmar") onConfirm?.(data);
      else if (ev === "done") onDone?.(data);
      // Diego apretó detener: el turno cortó donde pudo y no guardó respuesta
      else if (ev === "cancelado") onCancel?.();
      else if (ev === "error") onError?.(comoFalla(data));
    }
  }
}

// --- Cola offline de captura -----------------------------------------------
const QUEUE_KEY = "mem.queue";
const leerCola = () => { try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]"); } catch { return []; } };
const guardarCola = (q) => { localStorage.setItem(QUEUE_KEY, JSON.stringify(q)); setState({ queuePendientes: q.length }); };

// Dónde está el aparato AHORA, como "lat,lon" con 6 decimales (~10 cm, de sobra).
// Nunca rechaza ni tarda más de 3 s: sin permiso, sin GPS o sin contexto seguro
// (http:// que no sea localhost) devuelve "" y la captura sigue igual. El permiso
// del navegador ES el interruptor — revocarlo apaga esto sin tocar la app.
export function posicion() {
  if (!navigator.geolocation) return Promise.resolve("");
  return new Promise((res) => {
    navigator.geolocation.getCurrentPosition(
      (p) => res(`${p.coords.latitude.toFixed(6)},${p.coords.longitude.toFixed(6)}`),
      () => res(""),
      // maximumAge: un fix de los últimos 5 min vale y vuelve al instante, sin
      // despertar el GPS ni pedir permiso de nuevo
      { maximumAge: 300000, timeout: 3000, enableHighAccuracy: false },
    );
  });
}

export async function capturar(payload) {
  const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const q = leerCola();
  q.push({ id, payload });
  guardarCola(q);          // cero pérdida (§8.1): persiste ANTES de esperar nada
  const coords = await posicion();
  // ponytail: si un flush en curso ya la mandó, esa captura se queda sin coords
  // (la marca no encuentra el item). Nunca se pierde la captura, que es lo que importa.
  if (coords) {
    const cola = leerCola();
    const it = cola.find((x) => x.id === id);
    if (it) { it.payload = { ...it.payload, coords }; guardarCola(cola); }
  }
  flushQueue();
}

let flushing = false;
export async function flushQueue() {
  if (flushing || !navigator.onLine) return;
  flushing = true;
  try {
    let q = leerCola();
    while (q.length) {
      try {
        await post("/capture", q[0].payload);
      } catch {
        break; // sin red o el server no responde: se reintenta más tarde, orden preservado
      }
      q = q.slice(1);
      guardarCola(q);
      // la captura ya está en el inbox y el server arrancó a procesarla: que la
      // UI actualice el contador y se enganche al progreso. Evento de ventana y
      // no un import de ui.js — ui.js importa este módulo, sería un ciclo.
      window.dispatchEvent(new Event("mem:capturado"));
    }
  } finally { flushing = false; }
}

setState({ queuePendientes: leerCola().length });
window.addEventListener("online", flushQueue);
flushQueue();
