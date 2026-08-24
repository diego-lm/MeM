// TIMELINE GLOBAL — eje temporal real y zoomable sobre "cuando" (la fecha del
// evento), con "capturado" (la fecha de captura) como capa secundaria opcional:
// el modelo bitemporal de MeM, visible. Lejos = barras por mes (tap: acerca);
// cerca = memorias individuales (tap: abre su ficha en un popup, sin salir de la
// línea). Rueda/pinch = zoom, arrastre = pan.
//
// Limpieza (pedido 2026-08-08): tres cosas la ensuciaban y ya no.
//   1. El eje ponía una etiqueta por mes SIEMPRE — cinco años eran sesenta
//      rótulos pisándose. Ahora el paso sale del ancho real: mes, trimestre,
//      semestre, año o lustro, lo que entre con aire.
//   2. Los títulos se apilaban: cuando ningún carril tenía lugar, el evento se
//      metía igual en el más vacío y las etiquetas se solapaban. Ahora si no
//      entra queda el punto solo — se ve que hay algo y no tapa nada.
//   3. La capa "capturado" tiraba una diagonal por evento, casi todas verticales
//      y sin decir nada. Solo se dibuja cuando el desfase es real (más de dos
//      días): justo los casos donde el modelo bitemporal cuenta algo.
import { html, useMemo, useRef, useState } from "../../vendor/preact-htm.js";
import { Vistazo } from "../vistazo.js";
import { useMedida } from "./util.js";

const DIA = 86400e3;
const MES = 30.44 * DIA;
const MES_L = { es: ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"],
                en: ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"] };
const PASOS_MES = [1, 2, 3, 6, 12, 24, 60];      // mes · bimestre · trimestre · semestre · año · …
const SEP = 78;                                   // px mínimos entre etiquetas del eje
const CARRILES = 5;
const DESFASE_MIN = 2 * DIA;                      // capturado vs. cuando: menos que esto no se dibuja

const tsDe = (r) => Date.parse(r.cuando || r.fecha) || null;
const corto = (s) => (s.length > 24 ? s.slice(0, 23) + "…" : s);

export function TimelineGlobal({ items, lang }) {
  const en = lang === "en";
  const meses = MES_L[en ? "en" : "es"];
  const [conCaptura, setConCaptura] = useState(false);
  const [pop, setPop] = useState(null);   // slug abierto en el popup
  const eventos = useMemo(() => items.map((r) => ({ ...r, ts: tsDe(r), cap: Date.parse(r.capturado) || null }))
    .filter((r) => r.ts).sort((a, b) => a.ts - b.ts), [items]);
  const [dom, setDom] = useState(null);   // [t0, t1] ms; null = automático
  const puntero = useRef(null);           // pan horizontal: último clientX
  const [caja, medida] = useMedida();
  const H = 300, EJE = 26;
  const w = medida.w || 1;

  if (!eventos.length) return html`<div style="opacity:.5;font-size:13px">—</div>`;

  const [minTs, maxTs] = [eventos[0].ts, eventos[eventos.length - 1].ts];
  const margen = Math.max((maxTs - minTs) * 0.05, 3 * DIA);
  const [d0, d1] = dom || [minTs - margen, maxTs + margen];
  const span = d1 - d0;
  const x = (ts) => ((ts - d0) / span) * w;

  function zoom(factor, px) {
    const centro = d0 + (px / w) * span;
    const s = Math.max(6 * DIA, Math.min(span * factor, (maxTs - minTs) * 2 + 60 * DIA));
    setDom([centro - (centro - d0) * (s / span), centro + (d1 - centro) * (s / span)]);
  }
  const alRodar = (e) => { e.preventDefault(); zoom(Math.exp(e.deltaY * 0.002), e.clientX - e.currentTarget.getBoundingClientRect().left); };
  const alBajar = (e) => { e.currentTarget.setPointerCapture(e.pointerId); puntero.current = { x: e.clientX, movio: false }; };
  const alMover = (e) => {
    if (!puntero.current) return;
    const dx = e.clientX - puntero.current.x;
    if (Math.abs(dx) > 2) puntero.current.movio = true;
    puntero.current.x = e.clientX;
    setDom([d0 - (dx / w) * span, d1 - (dx / w) * span]);
  };
  const alSoltar = () => { setTimeout(() => { puntero.current = null; }, 0); };
  const clicLimpio = () => !puntero.current?.movio;

  // Grilla: el paso lo manda el ancho disponible, no el calendario. `minimo` es
  // cuánto tiempo ocupan SEP píxeles con este zoom; se elige el primer paso del
  // calendario que lo supere, así nunca hay dos rótulos a menos de SEP.
  const minimo = (span * SEP) / w;
  const porDias = minimo < 20 * DIA;
  const pasoDias = Math.max(1, Math.ceil(minimo / DIA));
  const pasoMeses = PASOS_MES.find((m) => m * MES >= minimo) || 120;
  const soloAnio = !porDias && pasoMeses >= 12;
  const grilla = [];
  const c = new Date(d0);
  c.setHours(0, 0, 0, 0);
  if (porDias) {
    while (c.getTime() < d1) {
      grilla.push({ ts: c.getTime(), etiq: `${c.getDate()} ${meses[c.getMonth()]}` });
      c.setDate(c.getDate() + pasoDias);
    }
  } else {
    // arrancar en un múltiplo del paso para que los rótulos caigan en fechas
    // redondas (enero de años pares, trimestres reales) y no donde quedó el pan
    c.setDate(1);
    c.setMonth(Math.floor(c.getMonth() / Math.min(pasoMeses, 12)) * Math.min(pasoMeses, 12));
    if (soloAnio) c.setMonth(0);
    while (c.getTime() < d1) {
      grilla.push({ ts: c.getTime(),
                    etiq: soloAnio ? String(c.getFullYear())
                                   : `${meses[c.getMonth()]} ${String(c.getFullYear()).slice(2)}` });
      c.setMonth(c.getMonth() + pasoMeses);
    }
  }

  // lejos: barras por mes; cerca: memorias individuales en carriles
  const porMes = span > 120 * DIA;
  let barras = [], carriles = [];
  if (porMes) {
    const cuenta = new Map();
    for (const ev of eventos) {
      const d = new Date(ev.ts);
      const clave = new Date(d.getFullYear(), d.getMonth(), 1).getTime();
      cuenta.set(clave, (cuenta.get(clave) || 0) + 1);
    }
    const tope = Math.max(...cuenta.values());
    barras = [...cuenta.entries()].map(([ts, n]) => {
      const fin = new Date(ts);
      fin.setMonth(fin.getMonth() + 1);
      return { ts, fin: fin.getTime(), n, h: 24 + (n / tope) * (H - EJE - 70) };
    });
  } else {
    const visibles = eventos.filter((ev) => ev.ts >= d0 && ev.ts <= d1);
    const libre = [];   // por carril: x hasta donde llega lo ya dibujado (punto + etiqueta)
    const paso = (H - EJE - 80) / CARRILES;
    for (const ev of visibles) {
      const px = x(ev.ts);
      const etiq = corto(ev.titulo);
      const ancho = 16 + etiq.length * 5.7;       // el <text> real, estimado
      let c2 = libre.findIndex((ult) => px > ult);
      if (c2 < 0 && libre.length < CARRILES) c2 = libre.length;
      if (c2 < 0) {
        // ningún carril libre: el punto se dibuja igual (hay algo ahí) pero sin
        // etiqueta — es preferible a dos títulos encimados
        const menos = libre.indexOf(Math.min(...libre));
        carriles.push({ ev, px, y: 46 + menos * paso, etiq: "" });
        continue;
      }
      libre[c2] = px + ancho;
      carriles.push({ ev, px, y: 46 + c2 * paso, etiq });
    }
  }
  const hoy = Date.now();
  const abrir = (slug) => clicLimpio() && setPop(slug);

  return html`
    <div>
      <div style="margin-bottom:10px;display:flex;align-items:center;gap:8px">
        <div role="button" tabindex="0" onClick=${() => setConCaptura(!conCaptura)}
             style="height:28px;padding:0 11px;display:flex;align-items:center;gap:6px;cursor:pointer;font-family:var(--font-mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;background:${conCaptura ? "color-mix(in srgb,var(--color-accent-2) 20%,transparent)" : "transparent"};color:${conCaptura ? "var(--color-accent-2-700)" : "color-mix(in srgb,var(--color-text) 55%,transparent)"};border:1px solid ${conCaptura ? "var(--color-accent-2)" : "var(--color-divider)"}">
          ◦ ${en ? "captured" : "capturado"}</div>
        ${dom && html`
          <div role="button" tabindex="0" onClick=${() => setDom(null)}
               style="height:28px;padding:0 11px;display:flex;align-items:center;cursor:pointer;font-family:var(--font-mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;border:1px solid var(--color-divider);color:var(--text-2)">
            ⟲ ${en ? "all" : "todo"}</div>`}
        <span style="margin-left:auto;font-family:var(--font-mono);font-size:10.5px;opacity:.5">${eventos.length}</span>
      </div>
      <div ref=${caja} style="border:1px solid var(--color-divider);background:var(--color-surface);overflow:hidden">
        <svg width="100%" height=${H} style="display:block;touch-action:pan-y;cursor:grab"
             onWheel=${alRodar} onPointerDown=${alBajar} onPointerMove=${alMover}
             onPointerUp=${alSoltar} onPointerCancel=${alSoltar}>
          ${grilla.map((g) => html`
            <line x1=${x(g.ts)} y1="0" x2=${x(g.ts)} y2=${H - EJE}
                  style="stroke:color-mix(in srgb,var(--color-text) 9%,transparent)" />
            <text x=${x(g.ts) + 5} y=${H - 9}
                  style="font-family:var(--font-mono);font-size:9px;letter-spacing:.08em;fill:var(--color-text);opacity:.5">${g.etiq}</text>`)}
          ${hoy >= d0 && hoy <= d1 && html`
            <line x1=${x(hoy)} y1="0" x2=${x(hoy)} y2=${H - EJE} style="stroke:var(--color-accent);stroke-dasharray:2 3;opacity:.7" />`}
          ${porMes ? barras.map((b) => html`
            <g style="cursor:pointer" onClick=${() => clicLimpio() && setDom([b.ts - 2 * DIA, b.fin + 2 * DIA])}>
              <rect x=${x(b.ts) + 2} y=${H - EJE - b.h} width=${Math.max(4, x(b.fin) - x(b.ts) - 4)} height=${b.h}
                    style="fill:color-mix(in srgb,var(--color-accent) 32%,transparent);stroke:var(--color-accent);stroke-width:1">
                <title>${b.n}</title></rect>
              <text x=${(x(b.ts) + x(b.fin)) / 2} y=${H - EJE - b.h - 7} text-anchor="middle"
                    style="font-family:var(--font-mono);font-size:10px;fill:var(--color-text);opacity:.6">${b.n}</text>
            </g>`)
          : carriles.map(({ ev, px, y, etiq }) => html`
            <g style="cursor:pointer" onClick=${() => abrir(ev.slug)}>
              ${conCaptura && ev.cap && Math.abs(ev.cap - ev.ts) > DESFASE_MIN && html`
                <line x1=${px} y1=${y} x2=${x(ev.cap)} y2=${H - EJE - 14}
                      style="stroke:var(--color-accent-2);stroke-width:.8;stroke-dasharray:2 3;opacity:.5" />`}
              <circle cx=${px} cy=${y} r="6.5" style="fill:var(--color-accent);stroke:var(--color-bg);stroke-width:1.5">
                <title>${ev.titulo}</title></circle>
              ${!!etiq && html`
                <text x=${px + 11} y=${y + 3.5}
                      style="font-family:var(--font-body);font-size:11px;fill:var(--color-text);opacity:.82;pointer-events:none">
                  ${etiq}</text>`}
            </g>`)}
          ${!porMes && conCaptura && carriles.filter(({ ev }) => ev.cap && ev.cap >= d0 && ev.cap <= d1).map(({ ev }) => html`
            <circle cx=${x(ev.cap)} cy=${H - EJE - 14} r="4" style="fill:var(--color-accent-2);opacity:.85">
              <title>${(en ? "captured: " : "capturado: ") + ev.titulo}</title></circle>`)}
        </svg>
      </div>
      <div style="margin-top:8px;font-family:var(--font-mono);font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;opacity:.45">
        ${en ? "● when it happened · ◦ when it was captured — wheel/pinch: zoom, drag: pan"
             : "● cuándo pasó · ◦ cuándo se capturó — rueda/pinch: zoom, arrastre: pan"}</div>
      ${pop && html`<${Vistazo} slug=${pop} lang=${lang} onClose=${() => setPop(null)} />`}
    </div>`;
}
