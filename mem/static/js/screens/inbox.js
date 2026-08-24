// INBOX — spec §3.3: lo pendiente (y lo que falló) como matriz de cubos. Lo ya
// procesado NO vive acá: salió a la Biblioteca y se consulta desde Memory.
// Buscar por texto/tag/subject/tipo, ordenar por hora o por contenido, ver el
// adjunto, editar o mandar a papelera — todo desde el cubo, que abre su detalle.
import { html, useState, useEffect, useMemo } from "../../vendor/preact-htm.js";
import { useStore } from "../state.js";
import { dict, fechaRelativa } from "../i18n.js";
import { get, patch, del } from "../api.js";
import { AddChip, Adjunto, Sheet, useProcesando, procesarInbox, ScreenHead, norm, IMG_EXT, TITULO_SEC,
         useProyectos, proyectosListos } from "../ui.js";
import { usePrivado, privadosDe, esMemoriaPrivada, BotonVerPrivado } from "../privado.js";
import { Markdown } from "../md.js";

const buscable = (it) =>
  norm([it.texto, it.tipo, it.contexto_usuario, (it.tags || []).join(" "), (it.subjects || []).join(" ")].join(" "));

function estiloItem(estado) {
  const err = estado === "error";
  return {
    bg: err ? "color-mix(in srgb, var(--color-accent) 10%, var(--color-surface))" : "var(--color-surface)",
    bd: err ? "color-mix(in srgb, var(--color-accent) 45%, transparent)" : "var(--color-divider)",
    tinte: err ? "color-mix(in srgb, var(--color-accent) 20%, transparent)" : "color-mix(in srgb, var(--color-text) 10%, transparent)",
    ink: err ? "var(--color-accent-700)" : "var(--text-2)",
  };
}

function Etiqueta({ children, onRemove, tone = "accent-2" }) {
  const bg = tone === "accent" ? "color-mix(in srgb,var(--color-accent) 16%,transparent)" : "color-mix(in srgb,var(--color-accent-2) 20%,transparent)";
  const fg = tone === "accent" ? "var(--color-accent-700)" : "var(--color-accent-2-700)";
  return html`
    <span style="height:28px;max-width:200px;padding:0 5px 0 10px;border-radius:var(--radius-md);display:flex;align-items:center;gap:4px;font-size:11.5px;background:${bg};color:${fg}">
      <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${children}</span>
      <span role="button" tabindex="0" onClick=${onRemove} style="width:18px;height:18px;border-radius:var(--radius-md);display:flex;align-items:center;justify-content:center;font-size:10px;cursor:pointer;opacity:.65">✕</span>
    </span>`;
}

// Cubo: la unidad de la matriz. Cuadrado, con lo justo para reconocer la captura
// (tipo, hora, adjunto, primeras líneas, tags); el detalle vive en la hoja.
function Cubo({ it, i, L, lang, onOpen }) {
  const e = estiloItem(it.estado);
  const img = it.adjunto && IMG_EXT.test(it.adjunto);
  return html`
    <div role="button" tabindex="0" onClick=${onOpen} title=${it.texto.slice(0, 200)}
         style="aspect-ratio:1/1;display:flex;flex-direction:column;gap:7px;padding:11px;border-radius:var(--radius-md);background:${e.bg};border:1px solid ${e.bd};box-shadow:var(--shadow-sm);cursor:pointer;overflow:hidden;animation:fadeUp .34s both;animation-delay:${i * 0.03}s">
      <div style="display:flex;align-items:center;gap:5px;flex-shrink:0">
        <span style="height:20px;padding:0 7px;border-radius:var(--radius-md);font-family:var(--font-mono);font-size:9.5px;display:flex;align-items:center;background:${e.tinte};color:${e.ink}">${L.inboxTypes[it.tipo] || it.tipo || "nota"}</span>
        ${it.adjunto && html`<span style="font-size:10px;opacity:.5">▦</span>`}
        ${it.estado === "error" && html`<span style="font-size:10px;color:var(--color-accent-700)">⚠</span>`}
        <span style="margin-left:auto;font-family:var(--font-mono);font-size:9.5px;opacity:.5;white-space:nowrap">${fechaRelativa(it.capturado, lang)}</span>
      </div>
      ${img && html`
        <img src=${`/attach/${it.adjunto}`} alt="" loading="lazy"
             style="width:100%;height:42%;object-fit:cover;border-radius:var(--radius-md);flex-shrink:0" />`}
      <div style="flex:1;min-height:0;font-size:12.5px;line-height:1.42;overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:${img ? 3 : 6}">${it.texto || "—"}</div>
      ${!!(it.tags || []).length && html`
        <div style="flex-shrink:0;font-family:var(--font-mono);font-size:9.5px;color:var(--color-accent-2-700);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${it.tags.join(" · ")}</div>`}
    </div>`;
}

function Detalle({ it, L, lang, onClose, onGuardarTexto, onTags, onSubjects, onEliminar }) {
  const [editando, setEditando] = useState(false);
  const [borrador, setBorrador] = useState(it.texto);

  function guardar() {
    const val = borrador.trim();
    setEditando(false);
    if (val && val !== it.texto) onGuardarTexto(it, val);
  }

  return html`
    <${Sheet} onClose=${onClose}>
      <div style="padding:4px 20px 26px;overflow:auto">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
          <span style="font-family:var(--font-mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;opacity:.6">${L.inboxTypes[it.tipo] || it.tipo} · ${L.inboxStatus[it.estado] || it.estado}</span>
          <span style="margin-left:auto;font-family:var(--font-mono);font-size:10.5px;opacity:.5">${(it.capturado || "").replace("T", " ").slice(0, 16)}</span>
        </div>
        ${it.error && html`<div style="font-size:12px;color:var(--color-accent-700);margin-bottom:10px">⚠ ${it.error}</div>`}
        ${it.contexto_usuario && html`<div style="font-size:12.5px;opacity:.7;margin-bottom:10px">“${it.contexto_usuario}”</div>`}
        ${it.adjunto && html`<${Adjunto} ruta=${it.adjunto} />`}
        ${editando
          ? html`
            <textarea value=${borrador} onInput=${(ev) => setBorrador(ev.target.value)} rows="8"
                      style="width:100%;resize:vertical;border-radius:var(--radius-md);border:1px solid var(--color-accent);background:var(--color-bg);color:var(--color-text);font-family:var(--font-body);font-size:14.5px;padding:9px 10px;margin-bottom:10px" />
            <div style="display:flex;gap:8px;margin-bottom:14px">
              <span role="button" tabindex="0" onClick=${guardar}
                    style="height:34px;padding:0 16px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:13px;cursor:pointer;background:var(--color-accent-2);color:var(--color-bg)">${L.tSave}</span>
              <span role="button" tabindex="0" onClick=${() => setEditando(false)}
                    style="height:34px;padding:0 16px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:13px;cursor:pointer;border:1px solid var(--color-divider)">${L.tCancel}</span>
            </div>`
          : html`<${Markdown} texto=${it.texto} style="font-size:14.5px;line-height:1.55;margin-bottom:14px" />`}

        <div style="${TITULO_SEC};margin-bottom:7px">${L.tSubjects}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:14px">
          ${(it.subjects || []).map((x) => html`
            <${Etiqueta} tone="accent" onRemove=${() => onSubjects(it, (it.subjects || []).filter((y) => y !== x))}>${x}<//>`)}
          <${AddChip} onAdd=${(x) => onSubjects(it, [...(it.subjects || []), x])} placeholder="subject" />
        </div>
        <div style="${TITULO_SEC};margin-bottom:7px">TAGS</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:18px">
          ${(it.tags || []).map((x) => html`
            <${Etiqueta} onRemove=${() => onTags(it, (it.tags || []).filter((y) => y !== x))}>${x}<//>`)}
          <${AddChip} onAdd=${(x) => onTags(it, [...(it.tags || []), x])} placeholder="tag" />
        </div>

        <div style="display:flex;gap:8px">
          ${!editando && html`
            <span role="button" tabindex="0" onClick=${() => { setBorrador(it.texto); setEditando(true); }}
                  style="height:38px;padding:0 16px;border-radius:var(--radius-md);display:flex;align-items:center;gap:7px;font-size:13.5px;cursor:pointer;border:1px solid var(--color-divider)">✎ ${L.tEdit}</span>`}
          <span role="button" tabindex="0" onClick=${() => { onEliminar(it); onClose(); }}
                style="height:38px;padding:0 16px;border-radius:var(--radius-md);display:flex;align-items:center;gap:7px;font-size:13.5px;cursor:pointer;margin-left:auto;color:var(--color-accent-700);border:1px solid color-mix(in srgb,var(--color-accent) 45%,transparent)">🗑 ${L.tDelete}</span>
        </div>
      </div>
    <//>`;
}

export function Inbox() {
  const s = useStore();
  const L = dict(s.lang);
  const [items, setItems] = useState(null);
  const [query, setQuery] = useState("");
  const [orden, setOrden] = useState(0);      // 0 = hora, 1 = contenido
  const [abierto, setAbierto] = useState(null);
  const [resultado, setResultado] = useState(null);
  const { procesando, ultimoLog } = useProcesando();
  // el candado del celular también acá: una captura pendiente de un proyecto
  // privado es su texto entero a la vista, sin procesar (pedido 2026-08-23).
  // Memory ya la filtraba en su omnibox; esta pantalla la mostraba igual.
  const { oculto } = usePrivado();
  const proyectos = useProyectos();
  const privs = privadosDe(proyectos);
  const ocultarMem = (it) => oculto && (!proyectosListos() || esMemoriaPrivada(it, privs));

  const cargar = () => get("/inbox").then(setItems).catch(() => setItems([]));
  useEffect(() => {
    // deep-link desde la omnibox de Memory: #inbox/<id> abre ese item directo
    get("/inbox").then((its) => {
      setItems(its);
      const pedido = s.param && its.find((x) => x.id === s.param);
      if (pedido && !ocultarMem(pedido)) setAbierto(pedido);
    }).catch(() => setItems([]));
  }, []);

  /** El ciclo capturar→procesar→memoria se cierra acá, sin ir a Ajustes. */
  async function procesarAhora() {
    if (procesando) return;
    setResultado(null);
    const r = await procesarInbox();
    if (r) { setResultado(r); await cargar(); }
  }

  const visibles = useMemo(() => {
    const q = norm(query.trim());
    const out = (items || []).filter((it) => !ocultarMem(it) && (!q || buscable(it).includes(q)));
    return orden === 1
      ? [...out].sort((a, b) => norm(a.texto).localeCompare(norm(b.texto)))
      : [...out].sort((a, b) => String(b.capturado || b.id).localeCompare(String(a.capturado || a.id)));
  }, [items, query, orden, oculto, proyectos]);

  const aplicar = (item, campos) => {
    const poner = (c) => {
      setItems((p) => p.map((x) => (x.id === item.id ? { ...x, ...c } : x)));
      setAbierto((a) => (a && a.id === item.id ? { ...a, ...c } : a));
    };
    poner(campos);
    // si el server rechaza, se deshace: la pantalla no puede decir "guardado"
    // cuando no se guardó nada. Ver el tag volver a su sitio ES el aviso.
    const previo = Object.fromEntries(Object.keys(campos).map((k) => [k, item[k]]));
    return patch(`/inbox/${item.id}`, campos).catch(() => poner(previo));
  };
  const guardarTexto = (item, texto) => aplicar(item, { texto });
  const cambiarTags = (item, tags) => aplicar(item, { tags });
  const cambiarSubjects = (item, subjects) => aplicar(item, { subjects });
  async function eliminar(item) {
    setItems((p) => p.filter((x) => x.id !== item.id));
    await del(`/inbox/${item.id}`);
  }

  const vacio = s.lang === "en" ? "Inbox empty — everything processed." : "Inbox vacío — todo procesado.";

  return html`
    <div class="mem-screen ancha" style="position:relative;flex:1;display:flex;flex-direction:column;animation:scIn .38s cubic-bezier(.22,1,.36,1);min-height:0">
      <${ScreenHead} titulo=${L.tInbox}>
        ${!!items?.length && html`
          <div role="button" tabindex="0" onClick=${procesando ? null : procesarAhora}
               class=${procesando ? "mem-btn-procesando" : "mem-btn-accent"}
               style="height:30px;padding:0 13px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:11.5px;font-weight:600;cursor:${procesando ? "default" : "pointer"};margin-left:6px">
            ${procesando ? "…" : resultado ? `${resultado.procesadas} ✓${resultado.errores ? ` · ${resultado.errores} ⚠` : ""}` : L.tProcessNow}
          </div>`}
        <span style="margin-left:auto;font-family:var(--font-mono);font-size:10.5px;opacity:.5">${items ? `${visibles.length}/${items.length}` : "…"}</span>
      <//>
      ${procesando && ultimoLog && html`
        <div style="padding:0 20px 8px;margin-top:-6px;flex-shrink:0;font-family:var(--font-mono);font-size:10px;opacity:.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
          ${ultimoLog.replace(/^##\s*\[[^\]]*\]\s*/, "")}</div>`}
      <div style="padding:0 20px 12px;flex-shrink:0">
        <div style="height:44px;border-radius:var(--radius-md);background:var(--color-surface);border:1px solid var(--color-divider);display:flex;align-items:center;padding:0 12px;gap:9px;box-shadow:var(--shadow-sm)">
          <span style="font-family:var(--font-mono);opacity:.5;font-size:13px">⌕</span>
          <input value=${query} onInput=${(ev) => setQuery(ev.target.value)} placeholder=${L.phInboxSearch}
                 style="flex:1;min-width:0;border:0;background:transparent;outline:none;font-family:var(--font-body);font-size:15px;color:var(--color-text)" />
          ${query && html`<span role="button" tabindex="0" onClick=${() => setQuery("")} style="cursor:pointer;opacity:.45;font-size:12px">✕</span>`}
        </div>
        <div style="display:flex;align-items:center;gap:7px;margin-top:11px">
          ${[L.tSortTime, L.tSortText].map((nombre, i) => html`
            <div role="button" tabindex="0" onClick=${() => setOrden(i)}
                 class="mem-tira ${orden === i ? "on" : ""}" style="height:32px">${nombre}</div>`)}
        </div>
      </div>
      <div style="flex:1;min-height:0;overflow:auto;padding:2px 20px 60px">
        ${items === null && html`<div style="opacity:.5;font-size:13px;padding:20px 4px">…</div>`}
        ${items && !items.length && html`<div style="opacity:.5;font-size:13px;padding:20px 4px">${vacio}</div>`}
        ${items && !!items.length && !visibles.length && html`<div style="opacity:.5;font-size:13px;padding:20px 4px">${L.tNoRes}</div>`}
        ${items?.some(ocultarMem) && html`<div style="padding:4px 0 12px"><${BotonVerPrivado} lang=${s.lang} /></div>`}
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:10px">
          ${visibles.map((it, i) => html`
            <${Cubo} key=${it.id} it=${it} i=${i} L=${L} lang=${s.lang} onOpen=${() => setAbierto(it)} />`)}
        </div>
      </div>
      ${abierto && html`
        <${Detalle} it=${abierto} L=${L} lang=${s.lang} onClose=${() => setAbierto(null)}
                    onGuardarTexto=${guardarTexto} onTags=${cambiarTags}
                    onSubjects=${cambiarSubjects} onEliminar=${eliminar} />`}
    </div>`;
}
