// Utilería compartida de las vistas 2D (grafo, mapa semántico, timeline).
// usePanZoom: arrastre = pan, rueda/pinch = zoom anclado al puntero. El contenedor
// que recibe `eventos` necesita `touch-action:none` para que el táctil funcione.
import { useEffect, useRef, useState } from "../../vendor/preact-htm.js";

// la misma paleta de puntos por grupo que usa la vista Temas (memory.js)
export const COLORES = ["#b2622d", "#728157", "#8c491a", "#d67f48", "#56633f", "#8fa073"];
export const colorGrupo = (grupos, g) => COLORES[Math.max(0, grupos.indexOf(g)) % COLORES.length];

/** `centrado`: el lienzo dibuja con el origen en el CENTRO del contenedor
 *  (`translate(w/2 + t.x …)`, grafo y mapa semántico); sin él, en la esquina.
 *  El zoom ancla en el punto que se toca, así que necesita las
 *  coordenadas medidas desde ESE origen: con el centro sin descontar, el mapa
 *  crecía desde un punto a media pantalla del cursor. */
export function usePanZoom({ min = 0.15, max = 10, centrado = false } = {}) {
  const [t, setT] = useState({ x: 0, y: 0, k: 1 });
  const punteros = useRef(new Map());
  const pellizco = useRef(0);      // distancia entre dos dedos en el último frame
  const arrastro = useRef(false);  // hubo pan real: los onClick de los nodos lo consultan

  // (px,py) relativos al origen del lienzo. El punto tocado queda quieto: se
  // despeja de  pantalla = origen + t + k·mundo  para el nuevo k.
  const zoomEn = (px, py, factor) => setT((p) => {
    const k = Math.min(max, Math.max(min, p.k * factor));
    const f = k / p.k;
    return { k, x: px - (px - p.x) * f, y: py - (py - p.y) * f };
  });
  // punto del evento → coordenadas del origen del lienzo
  const desdeOrigen = (el, cx, cy) => {
    const r = el.getBoundingClientRect();
    return [cx - r.left - (centrado ? r.width / 2 : 0),
            cy - r.top - (centrado ? r.height / 2 : 0)];
  };

  const eventos = {
    onPointerDown: (e) => {
      if (e.target.closest("[data-nodrag]")) return;   // ese elemento arrastra lo suyo
      e.currentTarget.setPointerCapture(e.pointerId);
      punteros.current.set(e.pointerId, [e.clientX, e.clientY]);
      if (punteros.current.size === 1) arrastro.current = false;
    },
    onPointerMove: (e) => {
      const ps = punteros.current;
      if (!ps.has(e.pointerId)) return;
      const previo = ps.get(e.pointerId);
      ps.set(e.pointerId, [e.clientX, e.clientY]);
      if (ps.size === 1) {
        const dx = e.clientX - previo[0], dy = e.clientY - previo[1];
        if (Math.abs(dx) + Math.abs(dy) > 2) arrastro.current = true;
        setT((p) => ({ ...p, x: p.x + dx, y: p.y + dy }));
      } else if (ps.size === 2) {
        arrastro.current = true;
        const [a, b] = [...ps.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        // el ancla del pellizco es el punto medio entre los dos dedos
        if (pellizco.current) {
          const [px, py] = desdeOrigen(e.currentTarget, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
          zoomEn(px, py, d / pellizco.current);
        }
        pellizco.current = d;
      }
    },
    onPointerUp: (e) => { punteros.current.delete(e.pointerId); pellizco.current = 0; },
    onPointerCancel: (e) => { punteros.current.delete(e.pointerId); pellizco.current = 0; },
    onWheel: (e) => {
      e.preventDefault();
      const [px, py] = desdeOrigen(e.currentTarget, e.clientX, e.clientY);
      zoomEn(px, py, Math.exp(-e.deltaY * 0.002));
    },
  };
  return { t, setT, eventos, arrastro };
}

/** Tamaño real del contenedor (para centrar el origen del lienzo en píxeles). */
export function useMedida() {
  const ref = useRef(null);
  const [medida, setMedida] = useState({ w: 0, h: 0 });
  const ro = useRef(null);
  // sin deps: el div puede no existir en el primer render (estado "cargando" de
  // la pantalla) — se reintenta en cada render hasta engancharse una sola vez
  useEffect(() => {
    if (!ref.current || ro.current) return;
    const medir = () => setMedida({ w: ref.current.offsetWidth, h: ref.current.offsetHeight });
    medir();
    ro.current = new ResizeObserver(medir);
    ro.current.observe(ref.current);
  });
  useEffect(() => () => ro.current?.disconnect(), []);
  return [ref, medida];
}
