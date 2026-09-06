import { html, render, useEffect } from "../vendor/preact-htm.js";
import { useStore, getState, go, reemplazar, setState, GENERAL } from "./state.js";
import { get, post } from "./api.js";
import { TabBar, Sidebar, ControlesGlobales, useAutoActualizar, useProyectos } from "./ui.js";
import { usePrivado } from "./privado.js";
import { EditorProyectos } from "./proyectos.js";
import { Home } from "./screens/home.js";
import { Inbox } from "./screens/inbox.js";
import { Memory } from "./screens/memory.js";
import { Entry } from "./screens/entry.js";
import { Lint } from "./screens/lint.js";
import { Sessions } from "./screens/sessions.js";
import { Chat } from "./screens/chat.js";
import { Settings } from "./screens/settings.js";

// COMPARTIR DESDE OTRA APP (pedido 2026-08-06). Android postea a /share, el
// server deja lo compartido en un buzón y redirige acá con su id. Va a la sesión
// que esté abierta; si no hay ninguna, a una nueva en blanco. Se manda como turno
// por la misma vía que el ↵ de Home (sessionStorage mem.pending.<sid>).
let recogiendo = "";
async function recoger(ident) {
  if (recogiendo === ident) return;      // el efecto puede correr dos veces
  recogiendo = ident;
  let d = { texto: "", adjunto: "" };
  try { d = await get(`/share/${ident}`); } catch { /* buzón vacío: se abre la sesión igual */ }
  const st = getState();
  let sid = st.sesionActiva?.id || "";
  if (!sid) {
    try { sid = (await post("/sessions", { subjects: [], titulo: "", modo: "chat", temporal: true, proyecto: st.proyecto || "" })).id; }
    catch { reemplazar("home"); return; }
  }
  if (d.texto || d.adjunto) sessionStorage.setItem(`mem.pending.${sid}`, JSON.stringify(d));
  reemplazar("chat", sid);   // replace: el back no vuelve a #share
}

function Recibido() {
  const s = useStore();
  useEffect(() => { recoger(s.param); }, [s.param]);
  return html`<div style="flex:1"></div>`;
}

const SCREENS = {
  share: () => html`<${Recibido} />`,
  home: () => html`<${Home} />`,
  inbox: () => html`<${Inbox} />`,
  sessions: () => html`<${Sessions} />`,
  chat: () => html`<${Chat} />`,
  memory: () => html`<${Memory} />`,
  entry: () => html`<${Entry} />`,
  settings: () => html`<${Settings} />`,
  lint: () => html`<${Lint} />`,
};

function App() {
  const s = useStore();
  // Acá y no en Home (pedido 2026-09-06): en compu el shell viejo se notaba
  // entrando directo a Memory o a una sesión, donde Home no monta nunca.
  useAutoActualizar();
  // Con el candado cerrado no se puede estar PARADO en un proyecto privado: su
  // nombre se lee en el chip, en la tabbar y en cada cabecera. Se sale a
  // General, que es donde el candado cerrado deja ver todo lo que hay.
  const { oculto } = usePrivado();
  const proyectos = useProyectos();
  useEffect(() => {
    if (oculto && proyectos.some((p) => p.privado && p.nombre === s.proyecto)) setState({ proyecto: GENERAL });
  }, [oculto, proyectos, s.proyecto]);
  const Screen = SCREENS[s.screen] || SCREENS.home;
  return html`
    <${ControlesGlobales} />
    <${Sidebar} EditorProyectos=${EditorProyectos} />
    <div class="mem-content">
      <div class="mem-stage" style="position:relative;z-index:1;flex:1;display:flex;flex-direction:column;min-height:0">
        ${Screen()}
      </div>
      <${TabBar} EditorProyectos=${EditorProyectos} />
    </div>
  `;
}

render(html`<${App} />`, document.getElementById("app"));

// TECLADO. Casi todo lo pulsable de la app es un <div role="button" tabindex="0">
// —hace falta para que un chip, una celda o una fila se vean como se ven—, así que
// recibían el foco pero Enter y Espacio no hacían nada: parecía navegable con
// teclado sin serlo. Un solo listener delegado lo arregla en toda la app, en vez
// de un onKeyDown por componente (eran 134).
addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  // un campo de texto dentro de algo pulsable se queda con su tecla (el ↵ del
  // compositor manda el mensaje, no re-dispara el botón que lo contiene)
  if (e.target.matches?.("input,textarea,select,[contenteditable]")) return;
  const el = e.target.closest?.('[role="button"],[role="checkbox"]');
  if (!el) return;
  e.preventDefault();          // Espacio no debe scrollear la página
  el.click();
});

// BACK CIERRA POPUP (pedido 2026-08-10). Con un sheet abierto, ⌫ es "cerrar
// esto", no "navegar atrás" — history.back() dispara el popstate que el
// propio Sheet (ui.js) escucha para cerrarse.
addEventListener("keydown", (e) => {
  if (e.key !== "Backspace") return;
  if (e.target.matches?.("input,textarea,select,[contenteditable]")) return;
  if (!document.querySelector(".mem-sheet-root")) return;
  e.preventDefault();
  history.back();
});

// NAVEGACIÓN POR DESLIZAMIENTO (pedido 2026-08-05). Solo eventos touch: un
// arrastre con ratón nunca navega, y por eso no hace falta preguntar por
// (pointer:coarse) — lo único que se comprueba es el ancho, porque a partir de
// 880px manda el sidebar y ahí el gesto no tendría sentido.
// Arrastrar ←  en Home  → Ajustes entra por la derecha.
// Arrastrar →  en cualquier otra pantalla → Home entra por la izquierda.
// px horizontales mínimos para que el arrastre complete el viaje. No hay tope de
// tiempo: como el stage sigue al dedo, un arrastre lento se ve venir y es tan
// intencional como uno rápido — quien decide es la distancia.
const SWIPE_MIN = 70;

// Un gesto que arranca sobre algo que ya usa el arrastre horizontal para lo
// suyo no es para navegar. Dos casos, un solo recorrido hacia arriba:
//   1. una tira con scroll horizontal (la fila 2 de la cabecera del chat, las
//      pastillas de vista);
//   2. una superficie que hace pan/zoom propio (grafo, timeline, mapa
//      semántico, mapa geográfico). No hace falta marcarlas a mano: ya
//      lo declaran con `touch-action`, que existe justo para esto — si no deja
//      pasar el pan horizontal (auto/manipulation/pan-x), el gesto es del
//      elemento. Sin esto, arrastrar la línea temporal en el celular movía
//      además la pantalla entera y terminaba navegando a Home.
function gestoAjeno(el) {
  for (; el && el !== document.body; el = el.parentElement) {
    const cs = getComputedStyle(el);
    if ((cs.overflowX === "auto" || cs.overflowX === "scroll") && el.scrollWidth > el.clientWidth + 2) return true;
    const ta = cs.touchAction;
    if (ta && ta !== "auto" && ta !== "manipulation" && !ta.includes("pan-x")) return true;
  }
  return false;
}

const stage = () => document.querySelector(".mem-stage");

// A dónde lleva este arrastre, o null si en esta pantalla no lleva a ninguna
// parte (→ en Home, ← fuera de Home): entonces el dedo no arrastra nada, que es
// la forma de decir "por aquí no hay nada".
// Con un sheet abierto el gesto es otro: solo → cierra (como back), y no
// arrastra el stage — abajo del popup sigue la pantalla que NO se está
// navegando, arrastrarla junto al dedo se vería mal.
function destino(dx) {
  if (document.querySelector(".mem-sheet-root")) return dx > 0 ? { cerrar: true } : null;
  const p = getState().screen;
  if (dx < 0 && p === "home") return { lado: "der", pantalla: "settings" };
  if (dx > 0 && p !== "home") return { lado: "izq", pantalla: "home" };
  return null;
}

function soltar(st, transicion) {
  st.style.transition = transicion;
  st.style.transform = "";
  st.style.opacity = "";
}

function deslizar({ lado, pantalla }, st) {
  // 1. la pantalla que se va termina de salir por su lado (sigue al dedo)
  st.style.transition = "transform .14s ease-out,opacity .14s ease-out";
  st.style.transform = `translate3d(${lado === "der" ? "-100%" : "100%"},0,0)`;
  st.style.opacity = "0";
  setTimeout(() => {
    document.documentElement.dataset.swipe = lado;
    go(pantalla);
    // 2. un respiro para que Preact monte la pantalla nueva DENTRO del stage aún
    //    desplazado (invisible); recolocarlo antes se vería como un salto. Con
    //    requestAnimationFrame no: si la pestaña deja de componer (pantalla
    //    bloqueada a mitad del gesto) el callback no llega y el stage se queda
    //    fuera de cuadro con la app en blanco. Un timer siempre acaba entrando.
    setTimeout(() => {
      soltar(st, "none");
      // 3. al acabar la entrada, congelar la animación del hijo ANTES de quitar
      //    el atributo: sin esto el `animation:scIn` inline de cada screen vuelve
      //    a aplicarse, y cambiar animation-name la relanza desde cero — ese era
      //    el parpadeo de "se redibuja la pantalla al terminar".
      const hijo = st.firstElementChild;
      const fin = (ev) => {
        if (ev && ev.target !== hijo) return;  // animationend burbujea desde dentro
        if (hijo) hijo.style.animation = "none";
        delete document.documentElement.dataset.swipe;
      };
      if (hijo) hijo.addEventListener("animationend", fin);
      setTimeout(fin, 600);  // red por si la animación no llega a correr
    }, 20);
  }, 145);
}

let toque = null;
addEventListener("touchstart", (e) => {
  // con dos dedos es un zoom; y con texto seleccionado el arrastre es del
  // handle de selección, no un swipe: sin esto, copiar un mensaje en el
  // móvil era imposible — el touchmove del handle se leía como swipe, la
  // app hacía preventDefault y la selección moría.
  // Queda vivo un tap "perdido": el que borra la selección no navega. Bien así.
  toque = e.touches.length === 1 && innerWidth < 880 && getSelection().isCollapsed
    && !gestoAjeno(e.target)
    ? { x: e.touches[0].clientX, y: e.touches[0].clientY, fijado: false, dest: null } : null;
}, { passive: true });

// El stage sigue al dedo mientras se arrastra: sin esto el gesto no da ninguna
// señal hasta que se suelta y se siente a tirones. Cerrar un popup no arrastra
// el stage (abajo queda quieto), solo se detecta el gesto completo.
addEventListener("touchmove", (e) => {
  if (!toque) return;
  const dx = e.touches[0].clientX - toque.x;
  const dy = e.touches[0].clientY - toque.y;
  if (!toque.fijado) {
    if (Math.abs(dx) < 12) return;                          // aún no se sabe
    if (Math.abs(dx) < Math.abs(dy) * 1.4) { toque = null; return; }  // es scroll vertical
    toque.fijado = true;
    toque.dest = destino(dx);
    if (toque.dest && !toque.dest.cerrar) stage().style.transition = "none";
  }
  if (!toque.dest) return;
  e.preventDefault();  // ya es un swipe horizontal: que no scrollee además
  if (toque.dest.cerrar) return;
  // 0.55 de resistencia: acompaña al dedo sin que la pantalla se vaya entera
  stage().style.transform = `translate3d(${dx * 0.55}px,0,0)`;
  stage().style.opacity = String(1 - Math.min(Math.abs(dx) / innerWidth, 0.3));
}, { passive: false });

addEventListener("touchcancel", () => {
  if (toque && toque.dest && !toque.dest.cerrar) soltar(stage(), "transform .22s cubic-bezier(.22,1,.36,1),opacity .22s");
  toque = null;
}, { passive: true });

addEventListener("touchend", (e) => {
  const a = toque; toque = null;
  if (!a || !a.dest) return;
  const dx = e.changedTouches[0].clientX - a.x;
  if (Math.abs(dx) >= SWIPE_MIN) {
    if (a.dest.cerrar) history.back(); else deslizar(a.dest, stage());
  } else if (!a.dest.cerrar) {
    soltar(stage(), "transform .22s cubic-bezier(.22,1,.36,1),opacity .22s");  // se queda a medias: vuelve
  }
}, { passive: true });
