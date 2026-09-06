// ITEM DE MEMORIA — spec §6.1: ver, editar subjects/tags (acotado), conexiones,
// registro de cambios. Nunca reescribe historia — toda edición queda fechada.
import { html, useState, useEffect } from "../../vendor/preact-htm.js";
import { useStore, go, GENERAL } from "../state.js";
import { dict } from "../i18n.js";
import { get, patch, post } from "../api.js";
import { AddChip, Adjunto, BotonCompartir, ScreenHead, Sheet, TITULO_SEC,
         useProyectos, proyectosListos, ChipMenu, itemsDeProyectos } from "../ui.js";
import { usePrivado, privadosDe, esMemoriaPrivada, PantallaPrivada, proyectoDe, conProyecto } from "../privado.js";
import { Markdown } from "../md.js";

// Lo que el modelo sacó del adjunto (lo que se ve en la foto o el video, lo que
// dice la página, el prompt de una creación) lo escribe memoria.py bajo esta
// cabecera. Se muestra aparte, plegado: al abrir una memoria se lee primero el
// contenido base, no diez párrafos de descripción (pedido 2026-08-06).
const CAB_DETALLE = "## Contenido del adjunto";

function partirCuerpo(md) {
  const marcador = "## Registro histórico";
  const i = md.indexOf(marcador);
  const crudo = i < 0 ? md : md.slice(0, i);
  const j = crudo.indexOf(CAB_DETALLE);
  const cuerpo = (j < 0 ? crudo : crudo.slice(0, j)).replace(/^##\s*Resumen\s*\n/, "").trim();
  const detalle = j < 0 ? "" : crudo.slice(j + CAB_DETALLE.length).trim();
  if (i < 0) return { cuerpo, detalle, historial: [] };
  const resto = md.slice(i + marcador.length);
  const historial = [...resto.matchAll(/\*\*(.+?)\*\*\s*\(([^)]*)\)\s*—\s*(.+)/g)]
    .map(([, fecha, origen, texto]) => ({ fecha, origen, texto: texto.trim() })).reverse();
  return { cuerpo, detalle, historial };
}

function Chip({ children, onRemove, tone }) {
  const bg = tone === "accent" ? "color-mix(in srgb,var(--color-accent) 16%,transparent)" : "color-mix(in srgb,var(--color-accent-2) 20%,transparent)";
  const fg = tone === "accent" ? "var(--color-accent-700)" : "var(--color-accent-2-700)";
  return html`
    <span style="height:28px;max-width:180px;padding:0 5px 0 10px;border-radius:var(--radius-md);display:flex;align-items:center;gap:4px;font-family:var(--font-mono);font-size:10.5px;background:${bg};color:${fg}">
      <span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${children}</span>
      <span role="button" tabindex="0" onClick=${onRemove} class="mem-hit" style="width:18px;height:18px;flex-shrink:0;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:10px;cursor:pointer;opacity:.65">✕</span>
    </span>`;
}

export function Entry() {
  const s = useStore();
  const L = dict(s.lang);
  const slug = s.param;
  const [e, setE] = useState(null);
  const [err, setErr] = useState("");
  const [reproc, setReproc] = useState(false);
  const [vers, setVers] = useState([]);   // versiones anteriores (la viva es `e`)
  const [ver, setVer] = useState(null);   // la que se está mirando en el sheet
  const [detalles, setDetalles] = useState(false);   // lo extraído del adjunto, plegado
  const { oculto } = usePrivado();
  const proyectos = useProyectos();
  const privs = privadosDe(proyectos);

  useEffect(() => {
    setE(null); setErr(""); setVers([]); setVer(null); setDetalles(false);
    get(`/memory/entry/${slug}`).then(setE).catch((ex) => setErr(String(ex)));
    get(`/memory/entry/${slug}/versions`).then(setVers).catch(() => setVers([]));
  }, [slug]);

  /** El adjunto sigue en la base: reprocesar con otro LLM lo recupera sin
   *  volver a capturar nada. */
  async function reprocesar() {
    if (reproc) return;
    setReproc(true);
    try {
      await post("/memory/reprocess", { slugs: [slug] });
      setE(await get(`/memory/entry/${slug}`));
      setVers(await get(`/memory/entry/${slug}/versions`));   // el reproceso dejó la anterior guardada
    } catch (ex) { setErr(String(ex)); }
    finally { setReproc(false); }
  }

  async function verVersion(n) {
    try { setVer(await get(`/memory/entry/${slug}/versions/${n}`)); }
    catch (ex) { setErr(String(ex)); }
  }

  /** Confirmar una sugerencia: el server escribe el [[wikilink]] fechado y la
   *  ficha se recarga — la sugerencia pasa a Conexiones. */
  async function enlazar(destino) {
    try { await post(`/memory/entry/${slug}/link`, { destino }); setE(await get(`/memory/entry/${slug}`)); }
    catch (ex) { setErr(String(ex.message || ex)); }
  }

  async function guardar(campos) {
    // el estado se toca DESPUÉS del server: si el patch falla, la pantalla sigue
    // mostrando lo que hay guardado de verdad (y el catch evita que la promesa
    // rechazada se pierda en el aire)
    try { await patch(`/memory/entry/${slug}`, campos); setE((p) => ({ ...p, ...campos })); }
    catch (ex) { setErr(String(ex.message || ex)); }
  }
  const quitarSubject = (x) => guardar({ subjects: (e.subjects || []).filter((s2) => s2 !== x) });
  const agregarSubject = (x) => guardar({ subjects: [...(e.subjects || []), x] });
  const quitarTag = (x) => guardar({ tags: (e.tags || []).filter((t) => t !== x) });
  const agregarTag = (x) => guardar({ tags: [...(e.tags || []), x] });

  if (err) return html`
    <div class="mem-screen" style="flex:1;display:flex;flex-direction:column">
      <${ScreenHead} titulo=${L.tMemoryItem} />
      <div style="padding:40px 20px;opacity:.6;font-size:13px">${err}</div>
    </div>`;
  if (!e) return html`<div style="flex:1"></div>`;

  // memoria privada: en el celular sin verificar no se muestra nada. Lo dice
  // el proyecto del que cuelga (pedido 2026-09-05), así que hasta que /projects
  // conteste la ficha queda tapada — abrirla por link es justo el camino por
  // el que se colaba.
  const memPrivada = esMemoriaPrivada(e, privs);
  if (oculto && (!proyectosListos() || memPrivada)) return html`
    <div class="mem-screen" style="flex:1;display:flex;flex-direction:column">
      <${ScreenHead} titulo=${L.tMemoryItem} />
      <${PantallaPrivada} lang=${s.lang} />
    </div>`;

  const { cuerpo, detalle, historial } = partirCuerpo(e.contenido || "");
  const cuando = (e.cuando || "").replace("T", " ").slice(0, 16);
  const meta = [e.creada, (e.origen || "").toUpperCase(), e.origen === "inbox" ? "PROCESADO" : "",
    e.version ? `V${e.version}` : "",
    cuando ? `${L.tWhen}: ${cuando}` : "", e.lugar ? `${L.tPlace}: ${e.lugar}` : "",
    // desde dónde se capturó: el par espacial de `capturado`. Sin nombre resuelto
    // (Nominatim caído al procesar) igual se muestran las coordenadas.
    (e.lugar_captura || e.coords_captura) ? `${L.tFrom}: ${e.lugar_captura || e.coords_captura}` : ""].filter(Boolean);

  return html`
    <div class="mem-screen" style="position:relative;flex:1;display:flex;flex-direction:column;animation:scIn .38s cubic-bezier(.22,1,.36,1);min-height:0">
      <${ScreenHead} titulo=${L.tMemoryItem}>
        <!-- compartir la memoria por la hoja nativa del móvil: el adjunto va como
             archivo (una URL /attach no abre fuera de la tailnet) y, si hay texto
             seleccionado, se comparte solo esa selección -->
        <${BotonCompartir} lang=${s.lang} titulo=${e.titulo} adjunto=${e.adjunto || ""}
                           texto=${`${e.titulo}\n\n${cuerpo}`}
                           estilo="margin-left:auto" />
      <//>
      <div style="flex:1;overflow:auto;padding:18px 20px 60px">
        <h3 style="margin:0 0 10px;font-family:var(--font-heading);font-size:25px;line-height:1.14">${e.titulo}</h3>
        <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;font-family:var(--font-mono);font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--text-3);margin-bottom:14px">
          ${meta.map((x) => html`<span>${x}</span>`)}
          <!-- proyecto de la memoria: tocarlo abre "Mover a…" — sacar un privado
               de su proyecto (a General, p.ej.) es la forma de compartirla -->
          <${ChipMenu} etiqueta=${`${memPrivada ? "⚿" : "◈"} ${proyectoDe(e) || GENERAL}`}
                       items=${itemsDeProyectos(proyectos, proyectoDe(e), [], s.lang)}
                       onPick=${(n) => guardar({ subjects: conProyecto(e.subjects, n) })} />
        </div>
        ${e.sesion && html`
          <!-- dónde nació esta memoria (frontmatter sesion): un toque y estás en esa conversación -->
          <div role="button" tabindex="0" onClick=${() => go("chat", e.sesion)}
               style="display:inline-flex;align-items:center;gap:7px;margin:-4px 0 14px;padding:5px 11px;border-radius:var(--radius-md);border:1px solid var(--color-divider);cursor:pointer;font-family:var(--font-mono);font-size:10px;letter-spacing:.07em;text-transform:uppercase;color:var(--color-accent-700)">
            ▮ ${s.lang === "en" ? "Born in session" : "Creada en la sesión"}
            <span style="opacity:.55;text-transform:none">${String(e.sesion).slice(0, 17)}</span> ›
          </div>`}
        ${!!(e.pendiente || []).length && html`
          <div style="border:1px solid color-mix(in srgb,var(--color-accent) 45%,transparent);border-radius:var(--radius-md);padding:12px 13px;margin-bottom:14px">
            <div class="mem-pend" style="font-family:var(--font-mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:6px">⚠ ${L.tUnprocessed}</div>
            ${e.pendiente.map((x) => html`<div style="font-size:13px;line-height:1.45">${x}</div>`)}
            <div style="font-size:12.5px;opacity:.6;margin:8px 0 10px">${L.tPendingHint}</div>
            <div role="button" tabindex="0" onClick=${reprocesar} class=${reproc ? "mem-btn-procesando" : "mem-btn-accent"}
                 style="height:36px;padding:0 15px;border-radius:var(--radius-md);display:inline-flex;align-items:center;font-size:13px;cursor:${reproc ? "default" : "pointer"}">
              ${reproc ? "…" : `⟳ ${L.tReprocess}`}</div>
          </div>`}
        ${e.adjunto && html`<${Adjunto} ruta=${e.adjunto} />`}
        <${Markdown} texto=${cuerpo} />
        ${!!detalle && html`
          <div style="margin-top:14px">
            <div role="button" tabindex="0" onClick=${() => setDetalles(!detalles)}
                 style="display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 13px;border-radius:var(--radius-md);cursor:pointer;border:1px solid var(--color-divider);background:var(--color-surface);font-family:var(--font-mono);font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--text-2)">
              ${detalles ? "▾" : "▸"} ${detalles ? L.tLessDetails : L.tMoreDetails}</div>
            ${detalles && html`
              <div style="margin-top:10px;padding-left:13px;border-left:2px solid var(--color-divider);animation:fadeUp .3s both">
                <${Markdown} texto=${detalle} />
              </div>`}
          </div>`}
        ${!!(e.enlaces || []).length && html`
          <div style="${TITULO_SEC};margin:16px 0 8px">${L.tLinks}</div>
          <div style="display:flex;flex-direction:column;gap:6px">
            ${e.enlaces.map((u) => html`
              <a href=${u} target="_blank" rel="noreferrer"
                 style="font-size:12.5px;color:var(--color-accent-700);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">⚯ ${u}</a>`)}
          </div>`}
        <div style="${TITULO_SEC};margin:16px 0 8px">${L.tSubjects}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:16px">
          ${(e.subjects || []).map((x) => html`<${Chip} tone="accent" onRemove=${() => quitarSubject(x)}>${x}<//>`)}
          <${AddChip} onAdd=${agregarSubject} placeholder="subject" dashed=${false} />
        </div>
        <div style="${TITULO_SEC};margin-bottom:8px">TAGS</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:18px">
          ${(e.tags || []).map((x) => html`<${Chip} tone="accent2" onRemove=${() => quitarTag(x)}>${x}<//>`)}
          <${AddChip} onAdd=${agregarTag} placeholder="tag" dashed=${false} />
        </div>
        ${!!(e.conexiones?.sesiones?.length || e.conexiones?.entradas?.length
             || e.conexiones?.wikilinks?.length || e.conexiones?.backlinks?.length) && html`
          <div style="${TITULO_SEC};margin-bottom:8px">${L.tConnections}</div>
          <div style="display:flex;flex-direction:column;gap:7px;margin-bottom:18px">
            ${(e.conexiones.sesiones || []).map((c) => html`
              <div role="button" tabindex="0" onClick=${() => go("chat", c.id)}
                   style="display:flex;align-items:center;gap:10px;padding:11px 12px;border-radius:var(--radius-md);border:1px solid var(--color-divider);cursor:pointer">
                <span style="font-family:var(--font-mono);font-size:11px;color:var(--color-accent-700)">▮</span>
                <span style="flex:1;font-size:13.5px">${c.titulo}</span><span style="opacity:.35">›</span>
              </div>`)}
            <!-- [[wikilinks]] confirmados: salientes ⇄ y entrantes ⇠, glifo propio -->
            ${(e.conexiones.wikilinks || []).map((c) => html`
              <div role="button" tabindex="0" onClick=${() => go("entry", c.slug)}
                   style="display:flex;align-items:center;gap:10px;padding:11px 12px;border-radius:var(--radius-md);border:1px solid var(--color-divider);cursor:pointer">
                <span style="font-family:var(--font-mono);font-size:11px;color:var(--color-accent-700)">⇄</span>
                <span style="flex:1;font-size:13.5px">${c.titulo}</span><span style="opacity:.35">›</span>
              </div>`)}
            ${(e.conexiones.backlinks || []).map((c) => html`
              <div role="button" tabindex="0" onClick=${() => go("entry", c.slug)}
                   style="display:flex;align-items:center;gap:10px;padding:11px 12px;border-radius:var(--radius-md);border:1px solid var(--color-divider);cursor:pointer">
                <span style="font-family:var(--font-mono);font-size:11px;color:var(--color-accent-700)">⇠</span>
                <span style="flex:1;font-size:13.5px">${c.titulo}</span><span style="opacity:.35">›</span>
              </div>`)}
            ${(e.conexiones.entradas || []).map((c) => html`
              <div role="button" tabindex="0" onClick=${() => go("entry", c.slug)}
                   style="display:flex;align-items:center;gap:10px;padding:11px 12px;border-radius:var(--radius-md);border:1px solid var(--color-divider);cursor:pointer">
                <span style="font-family:var(--font-mono);font-size:11px;color:var(--color-accent-700)">⌗</span>
                <span style="flex:1;font-size:13.5px">${c.titulo}</span><span style="opacity:.35">›</span>
              </div>`)}
          </div>`}
        ${!!(e.conexiones?.relacionadas || []).length && html`
          <!-- vecinas por similitud semántica: SUGERENCIAS — el ＋ escribe el
               [[wikilink]] fechado; nada se enlaza sin este toque -->
          <div style="${TITULO_SEC};margin-bottom:8px">${L.tRelated}</div>
          <div style="display:flex;flex-direction:column;gap:7px;margin-bottom:18px">
            ${e.conexiones.relacionadas.map((c) => html`
              <div style="display:flex;align-items:center;gap:10px;padding:8px 8px 8px 12px;border-radius:var(--radius-md);border:1px dashed var(--color-divider)">
                <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-3)">≈</span>
                <span role="button" tabindex="0" onClick=${() => go("entry", c.slug)}
                      style="flex:1;font-size:13.5px;cursor:pointer;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${c.titulo}</span>
                <span style="font-family:var(--font-mono);font-size:10px;opacity:.5">${c.score}</span>
                <span role="button" tabindex="0" title=${L.tLinkConfirm} onClick=${() => enlazar(c.slug)} class="mem-hit"
                      style="width:32px;height:32px;flex-shrink:0;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;cursor:pointer;border:1px solid var(--color-divider);color:var(--color-accent-700)">＋</span>
              </div>`)}
          </div>`}
        ${!!vers.length && html`
          <!-- cada edición de contenido guarda la memoria entera como estaba; acá se leen -->
          <div style="${TITULO_SEC};margin-bottom:8px">${L.tVersions}</div>
          <div style="display:flex;flex-direction:column;gap:7px;margin-bottom:18px">
            ${vers.map((v) => html`
              <div role="button" tabindex="0" onClick=${() => verVersion(v.version)}
                   style="display:flex;align-items:center;gap:10px;padding:11px 12px;border-radius:var(--radius-md);border:1px solid var(--color-divider);cursor:pointer">
                <span style="font-family:var(--font-mono);font-size:11px;color:var(--color-accent-700)">v${v.version}</span>
                <span style="flex:1;font-size:13.5px;opacity:.7">${v.fecha}</span><span style="opacity:.35">›</span>
              </div>`)}
          </div>`}
        ${!!historial.length && html`
          <div style="${TITULO_SEC};margin-bottom:8px">${L.tHistory}</div>
          ${historial.map((h) => html`
            <div style="display:flex;gap:12px;padding:9px 0;border-top:1px solid var(--color-divider)">
              <span style="font-family:var(--font-mono);font-size:10.5px;opacity:.55;width:70px;flex-shrink:0">${h.fecha}</span>
              <span style="font-size:13.5px;flex:1">${h.texto} <span style="opacity:.45">(${h.origen})</span></span>
            </div>`)}`}
      </div>
      ${ver && html`
        <${Sheet} onClose=${() => setVer(null)}>
          <div style="padding:4px 20px 30px;overflow:auto">
            <div style="${TITULO_SEC};margin-bottom:8px">${L.tVersions} · v${ver.version}</div>
            <h3 style="margin:0 0 12px;font-family:var(--font-heading);font-size:21px;line-height:1.14">${ver.titulo}</h3>
            <${Markdown} texto=${ver.contenido || ""} />
          </div>
        <//>`}
    </div>`;
}
