// VISTAZO — la ficha de una memoria en un popup, sin salir de donde estás.
//
// Es el mismo panel para las tres puertas de entrada: una ficha de la lista de
// Memory, un nodo del mindmap y un punto de la línea temporal (pedido
// 2026-08-08). Que sea un sheet y no una pantalla es justamente el punto: el
// grafo y la línea quedan montados detrás con su zoom, su foco y su ventana
// intactos, así que cerrar el popup te devuelve exactamente donde estabas.
//
// `onIr` es lo que hace útil al popup DENTRO del mapa: cuando lo abre el grafo,
// tocar una conexión no navega a otra pantalla — mueve el foco a ese nodo y
// sigue el recorrido en el mismo mapa. Sin `onIr` (la lista, la línea temporal)
// las conexiones navegan a la ficha completa, como siempre.
import { html, useState, useEffect } from "../vendor/preact-htm.js";
import { go } from "./state.js";
import { dict } from "./i18n.js";
import { get, patch } from "./api.js";
import { Sheet, AddChip, Adjunto, TITULO_SEC, ChipMenu, itemsDeProyectos, useProyectos } from "./ui.js";
import { proyectoDe, conProyecto, privadosDe } from "./privado.js";
import { Markdown } from "./md.js";

const GLIFOS = { sesiones: "▮", wikilinks: "⇄", backlinks: "⇠", entradas: "⌗", relacionadas: "≈" };

function FilaConex({ glifo, titulo, extra, onClick }) {
  return html`
    <div role="button" tabindex="0" onClick=${onClick}
         style="display:flex;align-items:center;gap:9px;padding:9px 11px;border:1px solid var(--color-divider);cursor:pointer">
      <span style="font-family:var(--font-mono);font-size:11px;color:var(--color-accent-700);flex-shrink:0">${glifo}</span>
      <span style="flex:1;min-width:0;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${titulo}</span>
      ${extra && html`<span style="font-family:var(--font-mono);font-size:9.5px;opacity:.45;flex-shrink:0">${extra}</span>`}
      <span style="opacity:.3;flex-shrink:0">›</span>
    </div>`;
}

export function Vistazo({ slug, lang, onClose, onIr = null }) {
  const L = dict(lang);
  const proyectos = useProyectos();
  const privs = privadosDe(proyectos);
  const [e, setE] = useState(null);
  useEffect(() => {
    setE(null);
    get(`/memory/entry/${slug}`).then(setE).catch(() => setE({ titulo: slug, contenido: "" }));
  }, [slug]);

  const cuerpo = (e?.contenido || "").split("## Registro histórico")[0]
    .replace(/^##\s*Resumen\s*\n/, "").trim();
  const cuando = (e?.cuando || "").replace("T", " ").slice(0, 16);
  const capturado = (e?.capturado || "").replace("T", " ").slice(0, 16);
  const meta = e ? [e.creada, cuando ? `${L.tWhen}: ${cuando}` : "",
                    capturado ? `${L.tCaptured}: ${capturado}` : "",
                    e.lugar ? `${L.tPlace}: ${e.lugar}` : ""].filter(Boolean) : [];

  // patch optimista con vuelta atrás: si el server rechaza, la UI vuelve a
  // mostrar lo que hay guardado de verdad
  function guardar(campos) {
    const previo = e;
    setE((p) => ({ ...p, ...campos }));
    patch(`/memory/entry/${slug}`, campos).catch(() => setE(previo));
  }
  // el server manda la misma vecina por varias vías (enlazada Y del mismo tema Y
  // parecida): en el popup eso son tres filas idénticas. Gana la más fuerte.
  const cx = e?.conexiones || {};
  const yaEsta = new Set([...(cx.wikilinks || []), ...(cx.backlinks || [])].map((c) => c.slug));
  const entradas = (cx.entradas || []).filter((c) => !yaEsta.has(c.slug));
  entradas.forEach((c) => yaEsta.add(c.slug));
  const relacionadas = (cx.relacionadas || []).filter((c) => !yaEsta.has(c.slug));
  // con onIr el popup NO se cierra: el mapa mueve el foco a esa memoria y este
  // mismo panel pasa a mostrarla — se recorre la red sin perder el mapa de vista
  const irA = (s) => { if (onIr) onIr(s); else { onClose(); go("entry", s); } };
  const grupos = [["sesiones", cx.sesiones, (c) => { onClose(); go("chat", c.id); }],
                  ["wikilinks", cx.wikilinks, (c) => irA(c.slug)],
                  ["backlinks", cx.backlinks, (c) => irA(c.slug)],
                  ["entradas", entradas, (c) => irA(c.slug)],
                  ["relacionadas", relacionadas, (c) => irA(c.slug)]];
  const hayConex = grupos.some(([, lista]) => (lista || []).length);

  return html`
    <${Sheet} onClose=${onClose}>
      <div style="padding:4px 20px 26px;overflow:auto">
        ${!e && html`<div style="opacity:.5;font-size:13px;padding:20px 0">…</div>`}
        ${e && html`
          <h3 style="margin:0 0 8px;font-family:var(--font-heading);font-size:22px;line-height:1.18">${e.titulo}</h3>
          <div style="display:flex;gap:12px;flex-wrap:wrap;font-family:var(--font-mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;opacity:.55;margin-bottom:10px">
            ${meta.map((x) => html`<span>${x}</span>`)}
          </div>
          <div style="margin-bottom:12px">
            <!-- proyecto de la memoria: tocarlo abre "Mover a…" (mismo mecanismo
                 que la sesión) — mover un privado a Todo es la forma de compartirla,
                 una acción con nombre y no un checkbox con ayuda (pedido 2026-09-05) -->
            <${ChipMenu} etiqueta=${`${privs.has(proyectoDe(e)) ? "⚿" : "◈"} ${proyectoDe(e) || L.tProjAll}`}
                         items=${itemsDeProyectos(proyectos, proyectoDe(e), [{ id: "", label: L.tProjAll }], lang)}
                         onPick=${(n) => guardar({ subjects: conProyecto(e.subjects, n) })} />
          </div>
          ${e.adjunto && html`<${Adjunto} ruta=${e.adjunto} />`}
          <div style="max-height:30vh;overflow:auto;margin-bottom:12px">
            <${Markdown} texto=${cuerpo} />
          </div>
          ${!!(e.enlaces || []).length && html`
            <div style="display:flex;flex-direction:column;gap:5px;margin-bottom:12px">
              ${e.enlaces.map((u) => html`
                <a href=${u} target="_blank" rel="noreferrer"
                   style="font-size:12px;color:var(--color-accent-700);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">⚯ ${u}</a>`)}
            </div>`}
          <div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center;margin-bottom:16px">
            ${(e.subjects || []).map((x) => html`
              <span style="font-family:var(--font-mono);font-size:10px;padding:3px 9px;background:color-mix(in srgb,var(--color-accent) 16%,transparent);color:var(--color-accent-700)">${x}</span>`)}
            ${(e.tags || []).map((x) => html`
              <span style="font-family:var(--font-mono);font-size:10px;padding:3px 9px;background:color-mix(in srgb,var(--color-accent-2) 20%,transparent);color:var(--color-accent-2-700)">${x}</span>`)}
            <${AddChip} onAdd=${(x) => guardar({ tags: [...(e.tags || []), x] })} placeholder="tag" />
          </div>
          ${hayConex && html`
            <div style="${TITULO_SEC};margin-bottom:8px">${L.tConnections}</div>
            <div style="display:flex;flex-direction:column;gap:6px;margin-bottom:16px">
              ${grupos.map(([clave, lista, alTocar]) => (lista || []).map((c) => html`
                <${FilaConex} key=${clave + (c.slug || c.id)} glifo=${GLIFOS[clave]} titulo=${c.titulo}
                              extra=${clave === "relacionadas" ? c.score : ""} onClick=${() => alTocar(c)} />`))}
            </div>`}
          <div role="button" tabindex="0" onClick=${() => { onClose(); go("entry", slug); }} class="mem-btn-accent"
               style="height:46px;display:flex;align-items:center;justify-content:center;font-family:var(--font-heading);font-size:15px;cursor:pointer">${L.tOpenFull}</div>`}
      </div>
    <//>`;
}
