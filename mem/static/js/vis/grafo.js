// GRAFO — la memoria como red: nodos memoria (círculo, color por grupo) y
// subject (punto mono), aristas por subject, [[wikilink]] y similitud semántica.
// d3-force para el layout, SVG propio para pintar. Arrastrar un nodo lo acomoda
// (se suelta y el layout respira); tap en un subject resalta su vecindario; tap
// en una memoria abre su ficha EN UN POPUP — el mapa queda montado detrás con su
// zoom y su foco, y cerrar te devuelve exacto donde estabas (pedido 2026-08-08).
//
// Dos usos con el mismo componente: sin `slugs` es la base entera (vista Grafo
// de Memory); con `slugs` es el vecindario de esas memorias (el mindmap de una
// sesión), y `extra` inyecta nodos que no están en la base — el centro de la
// sesión y las ideas sueltas que salieron conversando.
//
// Limpieza (pedido 2026-08-08): el mapa entero se dibujaba siempre completo —
// las tres capas de aristas a la vez y una etiqueta bajo cada nodo — y con unas
// pocas decenas de memorias eso es una madeja ilegible. Ahora cada capa se
// prende y apaga (la similitud arranca apagada en la base entera: es top-3 por
// nodo y es la que más satura), las etiquetas salen solo donde importan, y un
// buscador apaga todo lo que no coincide.
import { html, useEffect, useRef, useState } from "../../vendor/preact-htm.js";
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from "../../vendor/d3-force.js";
import { go } from "../state.js";
import { get } from "../api.js";
import { dict } from "../i18n.js";
import { norm } from "../ui.js";
import { Vistazo } from "../vistazo.js";
import { colorGrupo, useMedida, usePanZoom } from "./util.js";

const RADIO = { centro: 34, memoria: 30, idea: 26, subject: 14, lugar: 14, tag: 14 };
// Nodos-eje: por qué dos memorias están conectadas. Cada uno es una capa que se
// prende y se apaga; memoria/centro/idea no son ejes y no tienen capa.
const EJES = ["subject", "lugar", "tag"];
const GRADO_TODO = 4;   // el tope del slider = sin filtro de parentesco

function Chip({ on, onClick, children }) {
  return html`
    <div role="button" tabindex="0" onClick=${onClick}
         style="height:30px;padding:0 10px;display:flex;align-items:center;gap:5px;cursor:pointer;font-family:var(--font-mono);font-size:9.5px;letter-spacing:.07em;text-transform:uppercase;background:${on ? "color-mix(in srgb,var(--color-accent) 14%,transparent)" : "transparent"};color:${on ? "var(--color-accent-700)" : "color-mix(in srgb,var(--color-text) 50%,transparent)"};border:1px solid ${on ? "color-mix(in srgb,var(--color-accent) 55%,transparent)" : "var(--color-divider)"}">
      ${children}</div>`;
}

/** Slider compacto de la barra: rótulo + valor + rango. */
function Deslizador({ etiqueta, valor, texto, min, max, paso, onCambio }) {
  return html`
    <label style="display:flex;align-items:center;gap:7px;height:30px;padding:0 10px;border:1px solid var(--color-divider);font-family:var(--font-mono);font-size:9.5px;letter-spacing:.07em;text-transform:uppercase;color:color-mix(in srgb,var(--color-text) 62%,transparent)">
      ${etiqueta}
      <input type="range" min=${min} max=${max} step=${paso} value=${valor}
             onInput=${(e) => onCambio(Number(e.target.value))}
             style="width:74px;height:14px;accent-color:var(--color-accent);cursor:pointer;touch-action:none" />
      <span style="min-width:26px;color:var(--color-accent-700)">${texto}</span>
    </label>`;
}

export function GraphView({ lang, ocultas, slugs = null, extra = null, onNodo = null,
                            alto = "calc(100vh - 320px)" }) {
  const L = dict(lang);
  const [estado, setEstado] = useState("cargando");   // cargando | listo | vacio
  const [foco, setFoco] = useState(null);             // id de nodo cuyo vecindario se resalta
  const [pop, setPop] = useState(null);               // slug abierto en el popup
  const [buscar, setBuscar] = useState("");
  // capas/sliders arrancan colgados: son refinamiento, no lo primero que hace
  // falta ver, y desplegados de entrada era la barra sobrecargada del pedido
  // 2026-08-10 (buscador + 5 chips + 2 sliders, todo junto y siempre visible).
  const [filtros, setFiltros] = useState(false);
  // La similitud y los ejes lugar/tag arrancan prendidos SOLO en el mindmap: ahí
  // el mapa está acotado a una sesión y ver POR QUÉ se conectan las cosas es el
  // punto. En la base entera saturan, y se prenden con su chip.
  const [capas, setCapas] = useState(() => ({ subject: true, wikilink: true,
                                              semantico: !!slugs, lugar: !!slugs, tag: !!slugs }));
  // cuántos saltos desde el centro se dibujan ("niveles de parentesco") y desde
  // qué similitud cuenta una arista semántica ("cercanía")
  const [grado, setGrado] = useState(2);
  const [cerca, setCerca] = useState(0.55);
  const [, setTick] = useState(0);
  const sim = useRef(null);
  const nodos = useRef([]);
  const links = useRef([]);
  const arrastre = useRef(null);
  const bruto = useRef(null);       // {clave, g} — el fetch se reusa mientras `slugs` no cambie
  const { t, setT, eventos, arrastro } = usePanZoom({ centrado: true });
  const [caja, medida] = useMedida();
  const clave = slugs ? slugs.join(",") : "*";
  const claveExtra = extra ? JSON.stringify(extra) : "";
  const claveCapas = `${Object.values(capas).join("|")}|${grado}|${cerca}`;

  // al asentarse el layout, encuadrar el grafo entero en el contenedor
  function encuadrar() {
    const el = caja.current;
    if (!el || !nodos.current.length) return;
    const xs = nodos.current.map((n) => n.x), ys = nodos.current.map((n) => n.y);
    const bw = Math.max(...xs) - Math.min(...xs) + 120, bh = Math.max(...ys) - Math.min(...ys) + 120;
    const k = Math.min(1.4, el.offsetWidth / bw, el.offsetHeight / bh);
    const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
    setT({ k, x: -cx * k, y: -cy * k });
  }

  function construir(g) {
    // los nodos que sobreviven a un cambio de capa conservan su posición: sin
    // esto, apagar la similitud volaba el mapa entero y había que reorientarse
    const previos = new Map(nodos.current.map((n) => [n.id, n]));
    let ns = [...g.nodos.filter((n) => n.tipo !== "memoria" || !ocultas?.has(n.id.slice(2))),
              ...(extra?.nodos || [])]
      .filter((n) => !EJES.includes(n.tipo) || capas[n.tipo]);
    let ids = new Set(ns.map((n) => n.id));
    // sin nodos de un eje, sus aristas se caen solas por el filtro de ids.
    // `sesion` (el radio del centro del mindmap) no tiene capa: es el esqueleto.
    let ls = [...g.aristas, ...(extra?.aristas || [])]
      .filter((a) => capas[a.tipo] !== false)
      .filter((a) => a.tipo !== "semantico" || (a.peso ?? 1) >= cerca)
      .filter((a) => ids.has(a.a) && ids.has(a.b));
    // NIVELES DE PARENTESCO: a cuántos saltos del centro se deja de dibujar. Se
    // recorre sobre las aristas YA filtradas, así que apagar una capa también
    // acorta el alcance — el mapa se limpia de verdad, no solo se despinta.
    const raiz = ns.find((n) => n.tipo === "centro")?.id;
    if (raiz && grado < GRADO_TODO) {
      const vecinos = new Map();
      const anota = (a, b) => vecinos.set(a, [...(vecinos.get(a) || []), b]);
      for (const a of ls) { anota(a.a, a.b); anota(a.b, a.a); }
      const alcance = new Set([raiz]);
      let frente = [raiz];
      for (let d = 0; d < grado && frente.length; d++) {
        frente = frente.flatMap((id) => vecinos.get(id) || []).filter((id) => !alcance.has(id));
        frente.forEach((id) => alcance.add(id));
      }
      ns = ns.filter((n) => alcance.has(n.id));
      ids = new Set(ns.map((n) => n.id));
      ls = ls.filter((a) => ids.has(a.a) && ids.has(a.b));
    }
    ns = ns.map((n) => {
      const p = previos.get(n.id);
      return p ? { ...n, x: p.x, y: p.y, vx: p.vx, vy: p.vy } : { ...n };
    });
    ls = ls.map((a) => ({ ...a, source: a.a, target: a.b }));
    nodos.current = ns;
    links.current = ls;
    // nodos/links son REFS: mutarlas no repinta nada por sí solo, y el repintado
    // colgaba de que la simulación siguiera latiendo. Ya asentada (alpha en 0),
    // prender o apagar una capa no se veía hasta el próximo clic en cualquier
    // otra cosa — el mapa mostraba una capa de atraso. Un tick explícito acá lo
    // ata a construir(), que es lo que de verdad cambió el dibujo.
    setTick((x) => x + 1);
    sim.current?.stop();
    if (!ns.length) { setEstado("vacio"); return; }
    sim.current = forceSimulation(ns)
      .force("link", forceLink(ls).id((n) => n.id)
        .distance((l) => (EJES.includes(l.tipo) ? 62 : l.tipo === "sesion" ? 90 : 120))
        .strength((l) => (l.tipo === "semantico" ? 0.12 : 0.4)))
      .force("carga", forceManyBody().strength(-260))
      .force("centro", forceCenter(0, 0))
      .force("choque", forceCollide().radius((n) => RADIO[n.tipo] || 14).iterations(2))
      .on("tick", () => setTick((x) => x + 1))
      .on("end", encuadrar);
    setEstado("listo");
  }

  useEffect(() => {
    let vivo = true;
    const url = slugs ? `/memory/graph?slugs=${encodeURIComponent(clave)}` : "/memory/graph";
    const pedido = bruto.current?.clave === clave
      ? Promise.resolve(bruto.current.g)
      : get(url).then((g) => { bruto.current = { clave, g }; return g; });
    pedido.then((g) => vivo && construir(g)).catch(() => setEstado("vacio"));
    return () => { vivo = false; sim.current?.stop(); };
  }, [clave, claveExtra, ocultas, claveCapas]);

  // arrastre de un nodo: pointer capture sobre el propio círculo; al soltar el
  // layout respira (no queda fijado) y un toque sin arrastre es un clic
  function bajarNodo(e, n) {
    e.stopPropagation();
    e.target.setPointerCapture(e.pointerId);
    arrastre.current = { n, x: e.clientX, y: e.clientY, movio: false };
    n.fx = n.x; n.fy = n.y;
    sim.current?.alphaTarget(0.22).restart();
  }
  function moverNodo(e) {
    const a = arrastre.current;
    if (!a) return;
    const dx = e.clientX - a.x, dy = e.clientY - a.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) a.movio = true;
    a.x = e.clientX; a.y = e.clientY;
    a.n.fx += dx / t.k; a.n.fy += dy / t.k;
  }
  function soltarNodo(n) {
    const a = arrastre.current;
    arrastre.current = null;
    sim.current?.alphaTarget(0);
    n.fx = n.fy = null;
    if (!a || a.movio) return;
    if (onNodo?.(n)) return;                    // la pantalla se lo quedó
    if (n.tipo === "memoria") setPop(n.id.slice(2));
    else if (EJES.includes(n.tipo)) setFoco((f) => (f === n.id ? null : n.id));
  }

  // seguir una conexión desde el popup SIN salir del mapa: centra ese nodo, lo
  // pone en foco y el popup pasa a mostrarlo. Si no está dibujado (una vecina
  // fuera del vecindario del mindmap) no queda más que abrir su ficha.
  function irANodo(slug) {
    const n = nodos.current.find((x) => x.id === `e:${slug}`);
    if (!n) { setPop(null); go("entry", slug); return; }
    setFoco(n.id);
    setT((p) => ({ ...p, x: -n.x * p.k, y: -n.y * p.k }));
    setPop(slug);
  }

  if (estado === "cargando") return html`<div style="opacity:.5;font-size:13px">…</div>`;
  if (estado === "vacio") return html`<div style="opacity:.5;font-size:13px">${L.tNothingToDraw}</div>`;

  const grupos = [...new Set(nodos.current.map((n) => n.grupo))];
  // sale de `extra` y no de los nodos dibujados: si saliera de ahí, bajar el
  // slider hasta esconder el centro escondería el propio slider
  const hayCentro = (extra?.nodos || []).some((n) => n.tipo === "centro");
  const vecinos = new Set();
  if (foco) {
    vecinos.add(foco);
    for (const l of links.current) {
      if (l.source.id === foco) vecinos.add(l.target.id);
      if (l.target.id === foco) vecinos.add(l.source.id);
    }
  }
  const q = norm(buscar.trim());
  const coincide = (n) => !q || norm(`${n.label} ${n.id.slice(2)}`).includes(q);
  const apagados = new Set();
  if (foco || q) {
    for (const n of nodos.current) {
      if ((foco && !vecinos.has(n.id)) || (q && !coincide(n))) apagados.add(n.id);
    }
  }
  const apagado = (id) => apagados.has(id);
  // Etiquetas solo donde aportan: los subjects de primer nivel (los rótulos del
  // mapa), lo que esté en foco o coincida con la búsqueda, y los títulos de
  // memoria recién cuando el zoom da lugar para leerlos. Lugar y tag SIEMPRE
  // llevan rótulo: son pocos y el rótulo ES el motivo de la conexión.
  const rotulaEje = (n) => n.tipo !== "subject" || !n.id.slice(2).includes("/")
    || vecinos.has(n.id) || (q && coincide(n));
  const rotulaMemoria = (n) => t.k >= 0.95 || vecinos.has(n.id) || (q && coincide(n));
  const estiloArista = (l) =>
    l.tipo === "wikilink" ? "stroke:var(--color-accent);stroke-width:1.7"
    : l.tipo === "semantico" ? `stroke:var(--color-accent-2);stroke-width:1.1;stroke-dasharray:4 3;opacity:${(0.18 + (l.peso || 0.6) * 0.4).toFixed(2)}`
    : l.tipo === "sesion" ? "stroke:var(--color-accent);stroke-width:1.2;opacity:.5"
    : l.tipo === "lugar" ? "stroke:var(--color-accent-2);stroke-width:1;opacity:.45"
    : l.tipo === "tag" ? "stroke:color-mix(in srgb,var(--color-accent) 45%,transparent);stroke-width:1;stroke-dasharray:2 3"
    : "stroke:color-mix(in srgb,var(--color-text) 14%,transparent);stroke-width:1";
  const corto = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
  const etiquetaFoco = nodos.current.find((n) => n.id === foco)?.label || String(foco || "").slice(2);

  return html`
    <div style="display:flex;flex-direction:column;height:${alto};min-height:380px;gap:8px">
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;flex-shrink:0">
        <div style="height:26px;min-width:120px;flex:0 1 200px;display:flex;align-items:center;gap:6px;padding:0 8px;border:1px solid var(--color-divider);background:var(--color-surface)">
          <span style="font-family:var(--font-mono);opacity:.45;font-size:11px">⌕</span>
          <input value=${buscar} onInput=${(e) => setBuscar(e.target.value)} placeholder=${L.phFindNode}
                 style="flex:1;min-width:0;border:0;background:transparent;outline:none;font-family:var(--font-body);font-size:12.5px;color:var(--color-text)" />
          ${buscar && html`<span role="button" tabindex="0" onClick=${() => setBuscar("")} style="cursor:pointer;opacity:.45;font-size:11px">✕</span>`}
        </div>
        <${Chip} on=${filtros} onClick=${() => setFiltros((f) => !f)}>⚙ ${L.tFilters}<//>
        ${extra && html`<span style="font-family:var(--font-mono);font-size:9.5px;letter-spacing:.07em;text-transform:uppercase;opacity:.45">※ idea</span>`}
        <span style="margin-left:auto;font-family:var(--font-mono);font-size:10px;opacity:.45">${nodos.current.filter((n) => n.tipo === "memoria").length}</span>
      </div>
      ${filtros && html`
        <!-- POR QUÉ se conectan dos memorias: cada motivo es una capa que se
             prende y se apaga (pedido 2026-08-09) -->
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;flex-shrink:0">
          ${[["subject", "—", L.tGraphTopics], ["tag", "○", L.tGraphTags], ["lugar", "◇", L.tGraphPlaces],
             ["wikilink", "⇄", L.tGraphLinks], ["semantico", "┈", L.tGraphSim]]
            .map(([k, glifo, txt]) => html`
              <${Chip} key=${k} on=${capas[k]} onClick=${() => setCapas((p) => ({ ...p, [k]: !p[k] }))}>
                ${glifo} ${txt}<//>`)}
        </div>
        <!-- cuánto se dibuja: saltos desde el centro y desde qué similitud cuenta
             una arista semántica. El de parentesco solo existe donde hay centro
             (el mindmap de una sesión); en la base entera no hay desde dónde contar. -->
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;flex-shrink:0">
          ${hayCentro && html`
            <${Deslizador} etiqueta=${L.tGraphDegree} valor=${grado} min=${1} max=${GRADO_TODO} paso=${1}
                           texto=${grado >= GRADO_TODO ? "∞" : grado} onCambio=${setGrado} />`}
          ${capas.semantico && html`
            <${Deslizador} etiqueta=${L.tGraphNear} valor=${cerca} min=${0.55} max=${0.95} paso=${0.05}
                           texto=${cerca.toFixed(2)} onCambio=${setCerca} />`}
        </div>`}
      <div ref=${caja} style="position:relative;flex:1;min-height:0;border:1px solid var(--color-divider);background:var(--color-surface);overflow:hidden">
        <svg width="100%" height="100%" style="display:block;touch-action:none;cursor:grab" ...${eventos}>
          <g transform="translate(${medida.w / 2 + t.x} ${medida.h / 2 + t.y}) scale(${t.k})">
            ${links.current.map((l) => html`
              <line x1=${l.source.x} y1=${l.source.y} x2=${l.target.x} y2=${l.target.y}
                    style="${estiloArista(l)};${apagado(l.source.id) || apagado(l.target.id) ? "opacity:.05" : ""}" />`)}
            ${nodos.current.map((n) => html`
              <g transform="translate(${n.x} ${n.y})" data-nodrag
                 style="opacity:${apagado(n.id) ? 0.12 : 1};transition:opacity .25s;cursor:pointer"
                 onPointerDown=${(e) => bajarNodo(e, n)} onPointerMove=${moverNodo}
                 onPointerUp=${() => soltarNodo(n)} onPointerCancel=${() => soltarNodo(n)}>
                ${n.tipo === "centro" ? html`
                  <circle r="13" style="fill:var(--color-accent);stroke:var(--color-bg);stroke-width:2">
                    <title>${n.label}</title></circle>
                  <text y="27" text-anchor="middle"
                        style="font-family:var(--font-body);font-size:11.5px;font-weight:600;fill:var(--color-text);pointer-events:none">
                    ${corto(n.label, 30)}</text>
                ` : n.tipo === "idea" ? html`
                  <circle r="7" style="fill:var(--color-accent-2);stroke:var(--color-bg);stroke-width:1.5">
                    <title>${n.label}</title></circle>
                  <text y="20" text-anchor="middle"
                        style="font-family:var(--font-body);font-size:9.5px;fill:var(--color-accent-2-700);pointer-events:none">
                    ※ ${corto(n.label, 26)}</text>
                ` : n.tipo === "memoria" ? html`
                  <circle r="9" fill=${colorGrupo(grupos, n.grupo)}
                          style="stroke:var(--color-bg);stroke-width:1.5"><title>${n.label}</title></circle>
                  ${rotulaMemoria(n) && html`
                    <text y="20" text-anchor="middle"
                          style="font-family:var(--font-body);font-size:9.5px;fill:var(--color-text);opacity:.78;pointer-events:none">
                      ${corto(n.label, 22)}</text>`}
                ` : html`
                  <!-- cada eje con su forma, no solo su color: punto=tema,
                       rombo=lugar, anillo=tag (la paleta es mono-rojo) -->
                  ${n.tipo === "lugar" ? html`
                    <rect x="-4" y="-4" width="8" height="8" transform="rotate(45)"
                          style="fill:none;stroke:var(--color-accent-2);stroke-width:1.6">
                      <title>${n.label}</title></rect>`
                    : n.tipo === "tag" ? html`
                    <circle r="4.5" style="fill:none;stroke:color-mix(in srgb,var(--color-accent) 70%,transparent);stroke-width:1.6">
                      <title>${n.label}</title></circle>`
                    : html`
                    <circle r="4.5" style="fill:color-mix(in srgb,var(--color-text) 45%,transparent)">
                      <title>${n.id.slice(2)}</title></circle>`}
                  ${rotulaEje(n) && html`
                    <text y="-8" text-anchor="middle"
                          style="font-family:var(--font-mono);font-size:8px;letter-spacing:.08em;text-transform:uppercase;fill:var(--color-text);opacity:.55;pointer-events:none">
                      ${n.tipo === "lugar" ? "◇ " : n.tipo === "tag" ? "○ " : ""}${corto(n.label, 22)}</text>`}`}
              </g>`)}
          </g>
        </svg>
        ${foco && html`
          <div role="button" tabindex="0" onClick=${() => setFoco(null)}
               style="position:absolute;right:10px;top:10px;max-width:60%;height:30px;padding:0 12px;display:flex;align-items:center;gap:6px;cursor:pointer;background:color-mix(in srgb,var(--color-accent) 15%,transparent);border:1px solid var(--color-accent);color:var(--color-accent-700);font-family:var(--font-mono);font-size:10px;letter-spacing:.07em;text-transform:uppercase">
            <span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${etiquetaFoco}</span> ✕</div>`}
      </div>
      ${pop && html`<${Vistazo} slug=${pop} lang=${lang} onClose=${() => setPop(null)} onIr=${irANodo} />`}
    </div>`;
}
