// LINT — spec §6.1/§9: avisos de consistencia. Acción primaria = deep-link a la
// entidad para corregirla a mano. Dos categorías (duplicado, medio huérfano)
// además pueden actuarse ACÁ MISMO (fusionar / a papelera) porque el propio lint
// ya identifica exactamente qué archivo y por qué — pedirle a Diego que vaya a
// buscarlo a Memory es trabajo de más (pedido 2026-08-09). El resto sigue con el
// "Ignorar" de siempre, local al dispositivo; estas dos usan el descarte del
// server (lint.ignorar / lint_ignorar.json) porque Diego usa la app desde varios
// dispositivos y un descarte tiene que valer en todos.
import { html, useState, useEffect } from "../../vendor/preact-htm.js";
import { useStore, go } from "../state.js";
import { dict } from "../i18n.js";
import { get, post } from "../api.js";
import { ScreenHead } from "../ui.js";

const IGNORADOS_KEY = "mem.lint.ignorados";
const leerIgnorados = () => { try { return new Set(JSON.parse(localStorage.getItem(IGNORADOS_KEY) || "[]")); } catch { return new Set(); } };
const RX_DUP = /^posible duplicado \(similitud ([\d.]+), (.+?) ≈ (.+?)\): Entradas\/([\w-]+)\.md ≈ Entradas\/([\w-]+)\.md$/;
const RX_MEDIO = /^medio sin entrada \(.*\): (.+)$/;
// los cuatro tipos de aviso semántico llevan su clave adelante: sirve para
// aceptarlo (anotar) y para descartarlo en el server, sin reconstruir nada
const RX_AVISO = /^aviso semántico \[([^\]]+)\]: (.+)$/;
const TITULO_AVISO = {
  contradiccion: ["Posible contradicción", "Possible contradiction"],
  obsoleta: ["Posible dato obsoleto", "Possibly stale claim"],
  falta_pagina: ["Concepto sin página", "Concept with no page"],
  hueco: ["Hueco de información", "Information gap"],
};

function parsear(s) {
  let m;
  if ((m = RX_DUP.exec(s)))
    return { tipo: "duplicado", path: s, sim: m[1], a: { slug: m[4], fecha: m[2] }, b: { slug: m[5], fecha: m[3] } };
  if ((m = RX_MEDIO.exec(s)))
    return { tipo: "medio", path: s, title: "Medio sin entrada", ruta: m[1] };
  if ((m = RX_AVISO.exec(s))) {
    const [clave, texto] = [m[1], m[2]];
    const kind = clave.split("|")[0];
    // solo se puede anotar donde hay una memoria concreta afectada
    return { tipo: "aviso", path: s, clave, kind, texto, anotable: kind === "contradiccion" || kind === "obsoleta" };
  }
  if ((m = /^entrada huérfana \(.*\): Entradas\/([\w-]+)\.md$/.exec(s)))
    return { tipo: "otro", title: "Entrada huérfana", path: s, accion: "Abrir entrada", ir: () => go("entry", m[1]) };
  if ((m = /^sesión activa sin uso >30 días: 10_Sesiones\/([^/]+)\.md/.exec(s)))
    return { tipo: "otro", title: "Sesión sin uso", path: s, accion: "Abrir sesión", ir: () => go("chat", m[1]) };
  if (/^link roto:/.test(s))
    return { tipo: "otro", title: "Link roto", path: s, accion: null, ir: null };
  return { tipo: "otro", title: "Aviso", path: s, accion: null, ir: null };
}

// la más vieja absorbe a la más nueva (fechas ISO ordenan lexicográficamente
// bien; "?" — sin fecha — cae al final, que es el peor caso aceptable)
function porFusionar(l) {
  return l.a.fecha <= l.b.fecha ? { absorbe: l.a.slug, absorbida: l.b.slug } : { absorbe: l.b.slug, absorbida: l.a.slug };
}

function TarjetaDuplicado({ l, L, en, onFusionar, onIgnorar }) {
  const [armado, setArmado] = useState(false);
  const { absorbe, absorbida } = porFusionar(l);
  if (armado) return html`
    <div style="padding:14px;border-radius:var(--radius-md);border:1px solid var(--color-accent);margin-bottom:10px">
      <div style="font-size:13.5px;margin-bottom:10px">${L.tMergeQ} <b>${absorbida}</b> ${L.tMergeInto} <b>${absorbe}</b>?</div>
      <div style="display:flex;gap:8px">
        <div role="button" tabindex="0" onClick=${() => onFusionar(absorbe, absorbida)}
             class="mem-btn-accent" style="height:36px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:13px;cursor:pointer">${L.tMergeGo}</div>
        <div role="button" tabindex="0" onClick=${() => setArmado(false)}
             style="height:36px;padding:0 14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;font-size:13px;cursor:pointer">${L.tCancel}</div>
      </div>
    </div>`;
  return html`
    <div style="padding:14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);margin-bottom:10px">
      <div style="font-size:14.5px;font-weight:600;margin-bottom:3px">${en ? "Possible duplicate" : "Posible duplicado"} · ${l.sim}</div>
      <div style="font-size:12.5px;font-family:var(--font-mono);opacity:.55;margin-bottom:10px">${l.a.slug} (${l.a.fecha}) ≈ ${l.b.slug} (${l.b.fecha})</div>
      <div style="display:flex;gap:8px">
        <div role="button" tabindex="0" onClick=${() => setArmado(true)}
             class="mem-btn-accent" style="height:36px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:13px;cursor:pointer">${L.tMerge}</div>
        <div role="button" tabindex="0" onClick=${() => onIgnorar("duplicados", `${l.a.slug}|${l.b.slug}`)}
             style="height:36px;padding:0 14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;font-size:13px;cursor:pointer">${L.tIgnore}</div>
      </div>
    </div>`;
}

export function Lint() {
  const s = useStore();
  const L = dict(s.lang);
  const en = s.lang === "en";
  const [problemas, setProblemas] = useState(null);
  const [ignorados, setIgnorados] = useState(leerIgnorados);
  const [analizando, setAnalizando] = useState(false);

  const cargar = () => get("/lint").then((r) => setProblemas(r.problemas)).catch(() => setProblemas([]));
  useEffect(cargar, []);

  function ignorar(texto) {
    const next = new Set(ignorados); next.add(texto);
    localStorage.setItem(IGNORADOS_KEY, JSON.stringify([...next]));
    setIgnorados(next);
  }
  // duplicado/medio: el server es la fuente del descarte (varios dispositivos);
  // la respuesta ya trae la lista recalculada, sin pedir /lint de nuevo.
  async function fusionar(absorbe, absorbida) {
    try { setProblemas((await post("/memory/merge", { absorbe, absorbida })).problemas); } catch { cargar(); }
  }
  async function ignorarServer(tipo, clave) {
    try { setProblemas((await post("/lint/ignore", { tipo, clave })).problemas); } catch { cargar(); }
  }
  async function aPapelera(ruta) {
    try { setProblemas((await post("/lint/discard-media", { ruta })).problemas); } catch { cargar(); }
  }
  async function anotar(clave) {
    try { setProblemas((await post("/lint/annotate", { clave })).problemas); } catch { cargar(); }
  }
  // pase LLM sobre toda la Biblioteca: tarda, así que el botón se bloquea mientras corre
  async function analizar() {
    setAnalizando(true);
    try { setProblemas((await post("/lint/semantic", {})).problemas); } catch { cargar(); }
    setAnalizando(false);
  }

  const visibles = (problemas || []).filter((p) => !ignorados.has(p)).map(parsear);

  return html`
    <div class="mem-screen ancha" style="position:relative;flex:1;display:flex;flex-direction:column;animation:scIn .38s cubic-bezier(.22,1,.36,1)">
      <${ScreenHead} titulo="Lint" />
      <div style="flex:1;overflow:auto;padding:16px 20px 60px">
        <div role="button" tabindex="0" onClick=${() => !analizando && analizar()}
             style="height:34px;padding:0 13px;margin-bottom:14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:inline-flex;align-items:center;font-size:12.5px;cursor:pointer;opacity:${analizando ? 0.5 : 1}">
          ${analizando ? (en ? "Analyzing…" : "Analizando…") : (en ? "✦ Deep analysis (LLM)" : "✦ Análisis profundo (LLM)")}
        </div>
        ${problemas === null && html`<div style="opacity:.5;font-size:13px">…</div>`}
        ${problemas && !visibles.length && html`<div style="opacity:.5;font-size:13px">${en ? "No warnings." : "Sin avisos."}</div>`}
        ${visibles.map((l, i) => {
          if (l.tipo === "duplicado") return html`
            <div key=${l.path} style="animation:fadeUp .38s both;animation-delay:${i * 0.07}s">
              <${TarjetaDuplicado} l=${l} L=${L} en=${en} onFusionar=${fusionar} onIgnorar=${ignorarServer} />
            </div>`;
          if (l.tipo === "aviso") return html`
            <div key=${l.path} style="padding:14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);margin-bottom:10px;animation:fadeUp .38s both;animation-delay:${i * 0.07}s">
              <div style="font-size:14.5px;font-weight:600;margin-bottom:3px">${(TITULO_AVISO[l.kind] || ["Aviso", "Warning"])[en ? 1 : 0]}</div>
              <div style="font-size:12.5px;opacity:.7;margin-bottom:10px">${l.texto}</div>
              <div style="display:flex;gap:8px">
                ${l.anotable && html`
                  <div role="button" tabindex="0" onClick=${() => anotar(l.clave)}
                       class="mem-btn-accent" style="height:36px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:13px;cursor:pointer">${en ? "Note in record" : "Anotar en registro"}</div>`}
                <div role="button" tabindex="0" onClick=${() => ignorarServer("avisos", l.clave)}
                     style="height:36px;padding:0 14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;font-size:13px;cursor:pointer">${L.tIgnore}</div>
              </div>
            </div>`;
          if (l.tipo === "medio") return html`
            <div key=${l.path} style="padding:14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);margin-bottom:10px;animation:fadeUp .38s both;animation-delay:${i * 0.07}s">
              <div style="font-size:14.5px;font-weight:600;margin-bottom:3px">${en ? "Media with no entry" : "Medio sin entrada"}</div>
              <div style="font-size:12.5px;font-family:var(--font-mono);opacity:.55;margin-bottom:10px">${l.ruta}</div>
              <div style="display:flex;gap:8px">
                <div role="button" tabindex="0" onClick=${() => aPapelera(l.ruta)}
                     class="mem-btn-accent" style="height:36px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:13px;cursor:pointer">${L.tToTrash}</div>
                <div role="button" tabindex="0" onClick=${() => ignorarServer("medios", l.ruta)}
                     style="height:36px;padding:0 14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;font-size:13px;cursor:pointer">${L.tIgnore}</div>
              </div>
            </div>`;
          return html`
            <div key=${l.path} style="padding:14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);margin-bottom:10px;animation:fadeUp .38s both;animation-delay:${i * 0.07}s">
              <div style="font-size:14.5px;font-weight:600;margin-bottom:3px">${l.title}</div>
              <div style="font-size:12.5px;font-family:var(--font-mono);opacity:.55;margin-bottom:10px">${l.path}</div>
              <div style="display:flex;gap:8px">
                ${l.accion && html`
                  <div role="button" tabindex="0" onClick=${l.ir}
                       class="mem-btn-accent" style="height:36px;padding:0 14px;border-radius:var(--radius-md);display:flex;align-items:center;font-size:13px;cursor:pointer">${l.accion}</div>`}
                <div role="button" tabindex="0" onClick=${() => ignorar(l.path)}
                     style="height:36px;padding:0 14px;border-radius:var(--radius-md);border:1px solid var(--color-divider);display:flex;align-items:center;font-size:13px;cursor:pointer">${L.tIgnore}</div>
              </div>
            </div>`;
        })}
      </div>
    </div>`;
}
