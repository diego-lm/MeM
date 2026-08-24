// Store global mínimo (prefs + ruta) + router por hash. Estado de pantalla
// (inputs, filtros, sheets) vive local en cada screens/*.js vía useState —
// solo lo transversal entra acá.
import { useState, useEffect } from "../vendor/preact-htm.js";

const PREF_KEY = "mem.prefs";
// `proyecto` = proyecto activo del chatbox (lo que se captura sin sesión abierta).
// Es una preferencia y no estado de pantalla: sobrevive al reload y lo heredan
// las sesiones nuevas. Una sesión ya guardada manda sobre este valor.
// `sesionActiva` = {id, titulo} de la sesión en la que se está trabajando. Sigue
// activa al cambiar de pantalla y al recargar la app; se reemplaza al abrir otra
// y se suelta al cerrar el chat (✕) o al borrarla (pedido 2026-08-05).
// radio/borde/burbuja/fuente: knobs de estilo (pedido 2026-08-09). Los
// defaults son el look de SIEMPRE — nadie nota el cambio hasta que entra a
// Ajustes a tocarlos (ver app.css: cada default es "sin bloque [data-*]").
const PREF_FIELDS = ["theme", "themePref", "palette", "radio", "borde", "burbuja", "fuente",
                      "lang", "voiceLang", "proyecto", "sesionActiva"];
const PREF_DEFAULTS = {
  theme: "light", themePref: "light", palette: "terracota",
  radio: "recto", borde: "normal", burbuja: "llena", fuente: "archivo",
  lang: "es", voiceLang: "es-ES", proyecto: "", sesionActiva: null,
};

function cargarPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
    return { ...PREF_DEFAULTS, ...p };
  } catch { return { ...PREF_DEFAULTS }; }
}

function guardarPrefs(s) {
  const p = {}; for (const k of PREF_FIELDS) p[k] = s[k];
  localStorage.setItem(PREF_KEY, JSON.stringify(p));
}

export function resolverAuto(pref) {
  if (pref !== "auto") return pref;
  return new Date().getHours() > 19 ? "dark" : "light";
}

function leerRuta() {
  const h = (location.hash || "#home").slice(1);
  const i = h.indexOf("/");
  return i < 0 ? { screen: h || "home", param: null } : { screen: h.slice(0, i), param: decodeURIComponent(h.slice(i + 1)) };
}

let state = { ...leerRuta(), ...cargarPrefs(), online: true, queuePendientes: 0 };
const subs = new Set();

function aplicarAlDOM() {
  document.documentElement.dataset.theme = resolverAuto(state.themePref);
  document.documentElement.dataset.pal = state.palette;
  document.documentElement.dataset.radio = state.radio;
  document.documentElement.dataset.borde = state.borde;
  document.documentElement.dataset.burbuja = state.burbuja;
  document.documentElement.dataset.fuente = state.fuente;
  document.documentElement.lang = state.lang;
}
aplicarAlDOM();

export function getState() { return state; }

export function setState(patch) {
  state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
  aplicarAlDOM();
  guardarPrefs(state);
  subs.forEach((fn) => fn(state));
}

export function useStore() {
  const [, force] = useState(0);
  useEffect(() => {
    const fn = () => force((n) => n + 1);
    subs.add(fn);
    return () => subs.delete(fn);
  }, []);
  return state;
}

export function go(screen, param) {
  location.hash = param != null ? `${screen}/${encodeURIComponent(param)}` : screen;
}
/** Como go() pero sin dejar la pantalla actual en el historial: el back siguiente
 *  salta a lo que había antes, no vuelve acá. */
export function reemplazar(screen, param) {
  location.replace(`#${param != null ? `${screen}/${encodeURIComponent(param)}` : screen}`);
}
export function back() {
  history.back();
}
window.addEventListener("hashchange", () => setState(leerRuta()));

window.addEventListener("online", () => setState({ online: true }));
window.addEventListener("offline", () => setState({ online: false }));
