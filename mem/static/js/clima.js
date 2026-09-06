// CLIMA — la tira de pronóstico por horas de la banda superior de Home.
//
// Datos: Open-Meteo (sin API key, sin registro, CORS abierto). La posición sale
// del MISMO permiso del navegador que ya usan las capturas (api.posicion): sin
// permiso no hay tira. Acá NO se inventa un "22° Soleado" — ese stub vivía en
// esta misma esquina y se borró justamente por mentir (home.js, 2026-08-05).
//
// Cuántas horas se ven lo decide el ancho REAL de la tira, no un breakpoint: en
// el celu entran tres —ahora · la máxima del día · la noche, el mínimo que pidió
// Diego (2026-08-11)— y en escritorio hasta doce.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.js";
import { posicion } from "./api.js";
import { intervaloVisible } from "./ui.js";

const CACHE = "mem.clima";
const FRESCO = 30 * 60 * 1000;  // el pronóstico POR HORAS no cambia más rápido que esto
const COL = 36;                 // ancho de una columna, en px
const MAX_COL = 12;

/** Última respuesta buena, o la cacheada si la red falla. Nunca rechaza: sin
 *  clima la banda se queda con la fecha, que es lo que había antes. */
async function traer() {
  let previo = null;
  try { previo = JSON.parse(localStorage.getItem(CACHE) || "null"); } catch { /* cache corrupto */ }
  if (previo && Date.now() - previo.ts < FRESCO) return previo;
  // sin permiso o sin GPS seguimos con las últimas coordenadas conocidas: el
  // clima del mismo lugar hace un rato sirve; inventar el lugar no.
  const coords = (await posicion()) || previo?.coords;
  if (!coords) return previo;
  const [lat, lon] = coords.split(",");
  const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}`
    + "&current=temperature_2m,weather_code,is_day&hourly=temperature_2m,weather_code,is_day"
    + "&forecast_days=2&timezone=auto";
  try {
    const d = await fetch(url).then((r) => r.json());
    if (!d?.hourly?.time?.length) return previo;
    const nuevo = { ts: Date.now(), coords, cur: d.current, h: d.hourly };
    try { localStorage.setItem(CACHE, JSON.stringify(nuevo)); } catch { /* sin sitio: igual se muestra */ }
    return nuevo;
  } catch { return previo; }   // sin red: vale la última que haya
}

// --- Iconos ----------------------------------------------------------------
// Trazo de 1.5 en currentColor, como el resto de los glifos de la app. viewBox
// de 25 de alto: la gota y el rayo cuelgan por debajo de la nube.
const RAYOS = "M12 4.6V2.4M12 21.6v-2.2M4.6 12H2.4M21.6 12h-2.2M6.7 6.7 5.1 5.1M18.9 18.9l-1.6-1.6M17.3 6.7l1.6-1.6M5.1 18.9l1.6-1.6";
const SOL = `<circle cx="12" cy="12" r="3.7"/><path d="${RAYOS}"/>`;
const LUNA = '<path d="M19.2 14.3A6.9 6.9 0 0 1 9.7 4.8a7.2 7.2 0 1 0 9.5 9.5Z"/>';
const NUBE = '<path d="M7.6 16.7h8.6a3.3 3.3 0 0 0 .2-6.6 4.7 4.7 0 0 0-8.9-1.1 3.5 3.5 0 0 0 .1 7.7Z"/>';
const ASOMA = (d) => `<g transform="translate(2 .5) scale(.5)">${d}</g>`;  // el astro detrás de la nube
const PATHS = {
  sol: SOL,
  luna: LUNA,
  solnube: ASOMA(SOL) + NUBE,
  lunanube: ASOMA(LUNA) + NUBE,
  nube: NUBE,
  niebla: '<path d="M3.5 8.5h17M5.5 12.5h15M3.5 16.5h13M7 20.5h11"/>',
  lluvia: NUBE + '<path d="M9 18.9l-.9 2.4M13 18.9l-.9 2.4M17 18.9l-.9 2.4"/>',
  nieve: NUBE + '<path d="M9 20.2h.01M13 20.2h.01M17 20.2h.01"/>',
  tormenta: NUBE + '<path d="M13.2 17.6 10.6 21h2.3l-.5 2.4 3-3.9h-2.2z"/>',
};

/** weather_code (tabla WMO de Open-Meteo) -> familia de icono. */
function familia(c) {
  if (c === 0) return "sol";
  if (c === 1 || c === 2) return "solnube";
  if (c === 45 || c === 48) return "niebla";
  if (c >= 95) return "tormenta";
  if ((c >= 71 && c <= 77) || c === 85 || c === 86) return "nieve";
  if (c >= 51) return "lluvia";
  return "nube";   // 3 y cualquier código que no conozcamos
}

function Icono({ code, dia }) {
  let f = familia(code);
  if (!dia && (f === "sol" || f === "solnube")) f = f === "sol" ? "luna" : "lunanube";
  return html`
    <svg width="18" height="18" viewBox="0 0 24 25" fill="none" stroke="currentColor" stroke-width="1.6"
         stroke-linecap="round" stroke-linejoin="round" dangerouslySetInnerHTML=${{ __html: PATHS[f] }} />`;
}

/** Qué horas se muestran, de las 24 que vienen por delante.
 *  Las tres fijas son las que pidió Diego: ahora, la máxima del día y la noche
 *  (21 h). Con más ancho, el resto se reparte parejo por la ventana. Van
 *  ordenadas y sin repetir, así que con poco espacio pueden salir menos de `n`. */
function elegirHoras(h, i0, n) {
  const fin = Math.min(h.time.length - 1, i0 + 24);
  let iMax = i0, iNoche = -1;
  for (let i = i0; i <= fin; i++) {
    if (h.temperature_2m[i] > h.temperature_2m[iMax]) iMax = i;
    if (iNoche < 0 && i > i0 && Number(h.time[i].slice(11, 13)) === 21) iNoche = i;
  }
  const claves = [i0, iMax, iNoche < 0 ? fin : iNoche];
  const paso = (fin - i0) / (n - claves.length + 1);
  for (let k = 1; claves.length < n && k < n; k++) claves.push(i0 + Math.round(paso * k));
  return [...new Set(claves)].filter((i) => i >= i0 && i <= fin).sort((a, b) => a - b).slice(0, n);
}

export function Clima({ lang }) {
  const [d, setD] = useState(null);
  const [ancho, setAncho] = useState(0);
  const [pedible, setPedible] = useState(false);   // el permiso está sin conceder (ni dado ni negado)
  const ref = useRef(null);

  useEffect(() => {
    let vivo = true;
    const cargar = () => traer().then((x) => { if (vivo && x) setD(x); }).catch(() => {});
    cargar();
    // sin ubicación la tira no existe y no habría cómo enterarse: si el permiso
    // sigue en "prompt", un botón lo pide desde acá (denegado = ni se ofrece,
    // eso solo se revierte en los permisos del navegador).
    navigator.permissions?.query?.({ name: "geolocation" })
      .then((p) => vivo && setPedible(p.state === "prompt"))
      .catch(() => vivo && setPedible(true));
    const parar = intervaloVisible(cargar, FRESCO);
    return () => { vivo = false; parar(); };
  }, []);

  // el ancho manda sobre la cantidad de horas. La tira es flex:1, así que su
  // ancho no depende de cuántas columnas tenga: no hay realimentación.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const medir = () => setAncho(el.clientWidth);
    medir();
    const ro = new ResizeObserver(medir);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const h = d?.h;
  let idx = [], i0 = 0;
  if (h) {
    const clave = `${(d.cur?.time || "").slice(0, 13)}:00`;
    i0 = Math.max(0, h.time.indexOf(clave));
    // cuantas ENTRAN, no cuantas nos gustaria: el minimo de 3 de antes pedia
    // 138px y en un celular la tira mide ~112, asi que la tercera columna salia
    // cortada por el overflow (y por justify-content:flex-end la que se comia
    // era "ahora"). Mejor dos horas enteras que tres a medias (2026-09-06).
    idx = elegirHoras(h, i0, Math.max(1, Math.min(MAX_COL, Math.floor(ancho / COL))));
  }
  const iMax = idx.length ? idx.reduce((a, b) => (h.temperature_2m[b] > h.temperature_2m[a] ? b : a)) : -1;

  return html`
    <div ref=${ref} style="flex:1;min-width:0;display:flex;justify-content:flex-end;align-items:center;gap:1px;overflow:hidden">
      ${!h && pedible && html`
        <div role="button" tabindex="0" title=${lang === "en" ? "Weather needs your location" : "El clima necesita tu ubicación"}
             onClick=${() => posicion().then(() => traer()).then((x) => x && setD(x))}
             style="display:flex;align-items:center;gap:6px;height:30px;padding:0 10px;border-radius:var(--radius-md);border:1px solid var(--color-divider);background:var(--color-surface);cursor:pointer;font-family:var(--font-mono);font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--text-3)">
          <span style="color:var(--color-accent);line-height:0"><${Icono} code=${1} dia=${1} /></span>
          ${lang === "en" ? "weather" : "clima"}</div>`}
      ${idx.map((i) => {
        const ahora = i === i0;
        const temp = ahora && d.cur ? d.cur.temperature_2m : h.temperature_2m[i];
        const code = ahora && d.cur ? d.cur.weather_code : h.weather_code[i];
        const dia = ahora && d.cur ? d.cur.is_day : h.is_day[i];
        return html`
          <div key=${h.time[i]} title=${h.time[i].replace("T", " ")}
               style="width:${COL - 2}px;flex-shrink:0;display:flex;flex-direction:column;align-items:center;line-height:1.25;padding:2px 0;border-radius:var(--radius-md);${ahora ? "background:color-mix(in srgb,var(--color-accent) 9%,transparent)" : ""}">
            <span style="font-family:var(--font-mono);font-size:8.5px;letter-spacing:.06em;text-transform:uppercase;white-space:nowrap;color:${ahora ? "var(--color-accent-700)" : "var(--text-3)"}">
              ${ahora ? (lang === "en" ? "now" : "ahora") : `${h.time[i].slice(11, 13)}h`}</span>
            <span style="color:${dia ? "var(--color-accent)" : "var(--text-2)"};line-height:0"><${Icono} code=${code} dia=${dia} /></span>
            <span style="font-family:var(--font-mono);font-size:11px;font-weight:${ahora ? 700 : 500};color:${i === iMax ? "var(--color-accent-700)" : "var(--color-text)"}">
              ${Math.round(temp)}°</span>
          </div>`;
      })}
    </div>`;
}
