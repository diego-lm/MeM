// MAPA SEMÁNTICO — los embeddings proyectados a 2D: cerca = parecido en
// significado, los cúmulos son temas. Color por grupo raíz de subject.
// Pan/zoom; tap en un punto abre la ficha; hover muestra el título.
import { html, useEffect, useState } from "../../vendor/preact-htm.js";
import { go } from "../state.js";
import { get } from "../api.js";
import { colorGrupo, useMedida, usePanZoom } from "./util.js";

export function SemanticMap({ lang, ocultas }) {
  const en = lang === "en";
  const [puntos, setPuntos] = useState(null);
  const { t, eventos, arrastro } = usePanZoom({ centrado: true });
  const [caja, medida] = useMedida();

  useEffect(() => {
    get("/memory/semantic")
      .then((r) => setPuntos(r.puntos.filter((p) => !ocultas?.has(p.slug))))
      .catch(() => setPuntos([]));
  }, [ocultas]);

  if (puntos === null) return html`<div style="opacity:.5;font-size:13px">…</div>`;
  if (!puntos.length) return html`
    <div style="opacity:.6;font-size:13px;line-height:1.6">
      ${en ? "No semantic map yet — rebuild the search index in Settings › Processing to compute embeddings."
           : "Sin mapa semántico todavía — reindexá la búsqueda en Ajustes › Procesamiento para calcular los embeddings."}
    </div>`;

  const grupos = [...new Set(puntos.map((p) => p.grupo))];
  const escala = Math.min(medida.w, medida.h) * 0.42 || 1;   // [-1,1] → píxeles
  const conteo = grupos.map((g) => [g, puntos.filter((p) => p.grupo === g).length])
    .sort((a, b) => b[1] - a[1]).slice(0, 6);

  return html`
    <div ref=${caja} style="position:relative;height:calc(100vh - 320px);min-height:380px;border-radius:var(--radius-md);border:1px solid var(--color-divider);background:var(--color-surface);overflow:hidden;box-shadow:var(--shadow-sm)">
      <svg width="100%" height="100%" style="display:block;touch-action:none;cursor:grab" ...${eventos}>
        <g transform="translate(${medida.w / 2 + t.x} ${medida.h / 2 + t.y}) scale(${t.k})">
          ${puntos.map((p) => html`
            <g transform="translate(${p.x * escala} ${p.y * escala})" style="cursor:pointer"
               onClick=${() => !arrastro.current && go("entry", p.slug)}>
              <circle r=${7 / Math.sqrt(t.k)} fill=${colorGrupo(grupos, p.grupo)}
                      style="stroke:var(--color-bg);stroke-width:${1.4 / t.k};opacity:.92">
                <title>${p.titulo}</title></circle>
              ${t.k >= 1.6 && html`
                <text y=${16 / t.k} text-anchor="middle"
                      style="font-family:var(--font-body);font-size:${10 / t.k}px;fill:var(--color-text);opacity:.75;pointer-events:none">
                  ${p.titulo.length > 26 ? p.titulo.slice(0, 25) + "…" : p.titulo}</text>`}
            </g>`)}
        </g>
      </svg>
      <div style="position:absolute;left:10px;bottom:10px;display:flex;flex-wrap:wrap;gap:6px 12px;max-width:70%;padding:8px 11px;border-radius:var(--radius-md);background:color-mix(in srgb,var(--color-bg) 82%,transparent);border:1px solid var(--color-divider);pointer-events:none">
        ${conteo.map(([g, n]) => html`
          <span style="display:flex;align-items:center;gap:5px;font-family:var(--font-mono);font-size:9.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--text-2)">
            <span style="width:8px;height:8px;border-radius:var(--radius-md);background:${colorGrupo(grupos, g)}"></span>${g} · ${n}</span>`)}
      </div>
    </div>`;
}
