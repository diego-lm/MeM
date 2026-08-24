// MAPA GEOGRÁFICO — las memorias con "lugar" geocodificado (Nominatim, cacheado
// en el índice) sobre tiles de OpenStreetMap. Leaflet se carga perezoso desde
// vendor/ (UMD → window.L): solo se paga al abrir esta vista. Los tiles vienen
// de la red; sin conexión el fondo queda vacío pero la PWA no se rompe.
import { html, useEffect, useRef } from "../../vendor/preact-htm.js";

let promesaL = null;
function cargarLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  if (!promesaL) {
    promesaL = new Promise((res, rej) => {
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = "/vendor/leaflet/leaflet.css";
      document.head.appendChild(css);
      const s = document.createElement("script");
      s.src = "/vendor/leaflet/leaflet.js";
      s.onload = () => res(window.L);
      s.onerror = rej;
      document.head.appendChild(s);
    });
  }
  return promesaL;
}

// `lugares` = de qué habla la memoria (frontmatter `lugar`, geocodificado por el
// server). `capturas` = desde dónde se anotó (coordenadas del browser al capturar):
// dos capas distintas sobre el mismo mapa, igual que la línea temporal separa
// `cuando` del instante de captura. Los rombos son las capturas.
export function GeoMap({ lugares, capturas = [] }) {
  const caja = useRef(null);
  const mapa = useRef(null);
  const capa = useRef(null);

  useEffect(() => {
    let vivo = true;
    cargarLeaflet().then((L) => {
      if (!vivo || !caja.current) return;
      if (!mapa.current) {
        mapa.current = L.map(caja.current, { worldCopyJump: true });
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        }).addTo(mapa.current);
      }
      capa.current?.remove();
      capa.current = L.layerGroup().addTo(mapa.current);
      const raiz = getComputedStyle(document.documentElement);
      const acento = raiz.getPropertyValue("--color-accent").trim() || "#ec3013";
      const acento2 = raiz.getPropertyValue("--color-accent-2").trim() || "#728157";
      const puntos = [];
      for (const l of lugares) {
        puntos.push([l.lat, l.lon]);
        const enlaces = l.memorias.map((m) => `<a href="#entry/${m.slug}">${m.titulo}</a>`).join("<br>");
        L.circleMarker([l.lat, l.lon], {
          radius: 9 + Math.min(12, l.n * 1.5), color: acento, weight: 2,
          fillColor: acento, fillOpacity: 0.35,
        }).bindTooltip(`${l.lugar} · ${l.n}`)
          .bindPopup(`<b>${l.lugar}</b><br>${enlaces}`)
          .addTo(capa.current);
      }
      for (const c of capturas) {
        puntos.push([c.lat, c.lon]);
        const enlaces = c.memorias.map((m) => `<a href="#entry/${m.slug}">${m.titulo}</a>`).join("<br>");
        // rombo hueco, no círculo: en esta paleta accent y accent-2 son dos rojos
        // parecidos, así que la forma es lo que distingue las capas de verdad
        const lado = 12 + Math.min(10, c.memorias.length * 2);
        L.marker([c.lat, c.lon], {
          icon: L.divIcon({
            className: "mem-geo-captura",
            iconSize: [lado, lado],
            iconAnchor: [lado / 2, lado / 2],
            html: `<div style="width:100%;height:100%;transform:rotate(45deg);border:2px solid ${acento2};background:color-mix(in srgb,${acento2} 22%,transparent)"></div>`,
          }),
        }).bindTooltip(`◇ ${c.lugar} · ${c.memorias.length}`)
          .bindPopup(`<b>◇ ${c.lugar}</b><br>${enlaces}`)
          .addTo(capa.current);
      }
      if (puntos.length) mapa.current.fitBounds(L.latLngBounds(puntos).pad(0.35));
      // el contenedor recién midió bien después del primer render
      setTimeout(() => mapa.current?.invalidateSize(), 60);
    }).catch(() => {});
    return () => { vivo = false; };
  }, [lugares, capturas]);

  useEffect(() => () => { mapa.current?.remove(); mapa.current = null; }, []);

  return html`
    <div ref=${caja} style="height:calc(100vh - 340px);min-height:360px;border-radius:var(--radius-md);border:1px solid var(--color-divider);overflow:hidden;background:color-mix(in srgb,var(--color-text) 6%,transparent)"></div>`;
}
