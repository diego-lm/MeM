// Candado de lo privado (pedido 2026-08-05, rehecho el 2026-09-06): UN solo
// candado para toda la app, con su botón arriba a la derecha junto al tema y a
// Ajustes (ui.js → ControlesGlobales). No hay ningún otro "ver privado"
// repartido por las listas: los había en Home, Memory, el inbox y el sidebar, y
// cada uno tapaba lo suyo — el mismo gesto cuatro veces y, peor, un botón que
// pedía la huella en pantallas donde después no aparecía nada.
//
// Cerrado, un proyecto privado no existe: ni su nombre en los selectores, ni sus
// sesiones, ni sus memorias. Abierto, se ve todo mientras se use la app.
//
// La ventana de 15 minutos se corre con el USO, no con el reloj: cada toque o
// tecla la renueva, así que trabajando nunca se cierra en la cara; cerrar la app
// (o mandarla al fondo) deja de renovarla y al volver más tarde hay que
// verificarse de nuevo. El sello vive en localStorage, que es lo que sobrevive a
// cerrar la app — en memoria se perdía con cada recarga.
import { html, useState, useEffect } from "../vendor/preact-htm.js";
import { dict } from "./i18n.js";

const CRED_KEY = "mem.privado.cred";
const SELLO = "mem.privado.hasta";
const VENTANA = 15 * 60 * 1000;

const subs = new Set();
const emitir = () => subs.forEach((fn) => fn());
const guardar = (t) => { try { t ? localStorage.setItem(SELLO, String(t)) : localStorage.removeItem(SELLO); } catch { /* modo privado */ } };
const leer = () => { try { return Number(localStorage.getItem(SELLO) || 0); } catch { return 0; } };

let hasta = leer();
const abierto = () => Date.now() < hasta;

function fijar(t) { hasta = t; guardar(t); emitir(); }

// renovar con el uso (sin re-render: el estado no cambia, solo se corre el plazo)
const prorrogar = () => { if (abierto()) { hasta = Date.now() + VENTANA; guardar(hasta); } };
["pointerdown", "keydown"].forEach((ev) => addEventListener(ev, prorrogar, { passive: true, capture: true }));
// volver a la app tras un rato: puede haber vencido mientras no se miraba
document.addEventListener("visibilitychange", () => { if (!document.hidden) emitir(); });
// ...y con la app abierta pero quieta, que el candado se cierre solo igual
setInterval(() => { if (hasta && !abierto()) fijar(0); }, 30000);

const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));
const desB64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

/** ¿Hay lector en ESTE aparato (huella/cara/PIN del sistema)? Sin él no hay nada
 *  que pedir y el botón abre directo; con él, cancelar deja el candado puesto. */
async function hayBiometria() {
  if (!window.isSecureContext || !window.PublicKeyCredential) return false;
  try { return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable(); }
  catch { return false; }
}

/** Registra la credencial de plataforma de este aparato y la deja guardada. */
async function registrar(challenge) {
  const cred = await navigator.credentials.create({ publicKey: {
    challenge, timeout: 60000, rp: { name: "MeM" },
    user: { id: crypto.getRandomValues(new Uint8Array(16)), name: "diego", displayName: "Diego" },
    pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
    authenticatorSelection: { authenticatorAttachment: "platform", residentKey: "preferred",
                              userVerification: "required" } } });
  localStorage.setItem(CRED_KEY, b64(cred.rawId));
}

/** Verificación con el propio dispositivo. La primera vez registra una
 *  credencial de plataforma (el sistema pide huella/cara/PIN); las siguientes
 *  solo piden verificar. No hay firma que validar en el server: el gate es el
 *  prompt del sistema operativo — la API ya tiene su propia auth.
 *  ponytail: sin lector el tap de "Ver" es el único gate. */
async function verificarDispositivo() {
  if (!(await hayBiometria())) return true;
  const challenge = crypto.getRandomValues(new Uint8Array(16));
  const guardada = localStorage.getItem(CRED_KEY);
  if (guardada) {
    try {
      await navigator.credentials.get({ publicKey: {
        challenge, timeout: 60000, userVerification: "required",
        // transports:["internal"] es lo que manda al lector del aparato y NO al
        // menú de passkeys: sin esto Android ofrecía "Passkey on another device"
        // con un QR en vez de la huella (reportado 2026-09-06).
        allowCredentials: [{ type: "public-key", id: desB64(guardada), transports: ["internal"] }] } });
      return true;
    } catch {
      // el id guardado ya no existe en este aparato — credencial borrada, o un
      // gestor de contraseñas que la rotó: el sistema contesta "no available
      // sign-in" y no hay cómo entrar. Se tira y se registra de nuevo.
      localStorage.removeItem(CRED_KEY);
    }
  }
  try { await registrar(challenge); return true; } catch { return false; }
}

export async function desbloquear() {
  if (await verificarDispositivo()) fijar(Date.now() + VENTANA);
}

/** Cerrar es gratis y sin preguntas: el mismo botón, tocado de nuevo. */
export const bloquear = () => fijar(0);

/** oculto=true → lo privado no se muestra en ninguna parte (ni los nombres de
 *  los proyectos privados). */
export function usePrivado() {
  const [, force] = useState(0);
  useEffect(() => {
    const fn = () => force((n) => n + 1);
    subs.add(fn);
    return () => subs.delete(fn);
  }, []);
  return { oculto: !abierto(), desbloquear, bloquear };
}

export const privadosDe = (proyectos) =>
  new Set((proyectos || []).filter((p) => p.privado).map((p) => p.nombre));

export const esSesionPrivada = (ses, privs) => privs.has(String(ses?.proyecto || ""));

// El proyecto de una memoria (o de un item del inbox, o de un medio): el campo
// `proyecto` si lo trae, si no el primer `Proyectos/<n>` de sus subjects — una
// memoria vive en un solo proyecto.
const RX_PROY = /^Proyectos\/([^/]+)/;
export const proyectoDe = (r) =>
  r?.proyecto ? String(r.proyecto) : (r?.subjects || []).map((s) => RX_PROY.exec(String(s))?.[1]).find(Boolean) || "";

// Mover una memoria de proyecto: saca el `Proyectos/<n>` que tuviera (una
// memoria vive en uno solo) y antepone el nuevo, si hay — a "" es Todo.
export const conProyecto = (subjects, nuevo) => {
  const resto = (subjects || []).filter((x) => !RX_PROY.test(String(x)));
  return nuevo ? [`Proyectos/${nuevo}`, ...resto] : resto;
};

// Lo privado es del PROYECTO, no de la memoria (pedido 2026-09-05): togglear un
// proyecto cambia al instante la visibilidad de todo lo suyo, sin campo propio
// que migrar. El candado oculta todo hasta que /projects llega, igual que con
// las sesiones — sin eso, un proyecto recién pasado a privado se vería igual.
export const esMemoriaPrivada = (r, privs) => !!privs?.has(proyectoDe(r));

// -- Aviso de nube (pedido 2026-09-05) --------------------------------------
// Privado es quién lo VE en MeM, no por dónde PASA el texto: un proyecto privado
// atendido por un agente de Claude manda igual cada turno fuera de la máquina.
// Se dice en los tres momentos en que eso se decide —crear un proyecto privado,
// pasar uno a privado, y la sesión donde se va a escribir— y en ningún otro: con
// un agente local no aparece nada.

/** El agente corre EN esta máquina. "openai" ES el proveedor local — Ajustes lo
 *  rotula "Local / OpenAI" y lo prellena con localhost:1234. Si algún día se
 *  apunta a una nube compatible habría que mirar base_url, que /agents/activos
 *  hoy ni manda. Vivía en ui.js; se mudó acá porque el aviso también lo mide y
 *  ui.js ya importa este módulo (al revés sería un ciclo). */
export const esLocal = (a) => a?.proveedor === "openai";

/** null si todos los agentes que le pasan corren en la máquina: sin nube no hay
 *  nada que avisar, y un aviso que aparece siempre no se lee. */
export function AvisoNube({ lang, agentes, estilo = "" }) {
  const nube = (agentes || []).filter((a) => a && !esLocal(a));
  if (!nube.length) return null;
  const L = dict(lang);
  const quienes = `${nube.map((a) => `${a.nombre} (${a.modelo})`).join(" · ")} → ☁ Claude. ${L.tPrivNubeHint}`;
  return html`
    <div style="padding:8px 11px;border-radius:var(--radius-md);border:1px solid color-mix(in srgb,var(--color-priv) 45%,transparent);background:color-mix(in srgb,var(--color-priv) 8%,transparent);${estilo}">
      <span style="font-size:12px;line-height:1.5;color:var(--text-2)"><b style="color:var(--color-priv)">⚿ ${L.tPrivNube}</b> ${quienes}</span>
    </div>`;
}

/** Candado dibujado y no un glifo de fuente: el arco abierto/cerrado se lee de
 *  un vistazo y ninguna fuente del sistema garantiza un par así. */
const Dibujo = ({ abierto }) => html`
  <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
       stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <rect x="4.5" y="10.5" width="15" height="10" rx="2.2" />
    <path d=${abierto ? "M8.5 10.5V7a3.5 3.5 0 0 1 6.8-1.2" : "M8.5 10.5V7a3.5 3.5 0 0 1 7 0v3.5"} />
  </svg>`;

/** EL botón del candado: el único lugar de la app donde se abre y se cierra lo
 *  privado. Vive en la barra de arriba a la derecha, en todas las pantallas. */
export function BotonCandado({ lang }) {
  const L = dict(lang);
  const { oculto, desbloquear: abrir, bloquear: cerrar } = usePrivado();
  return html`
    <div role="button" tabindex="0" onClick=${oculto ? abrir : cerrar}
         title=${oculto ? L.tPrivVer : L.tPrivCerrar} aria-label=${oculto ? L.tPrivVer : L.tPrivCerrar}
         class="mem-ctrl ${oculto ? "" : "on"}"><${Dibujo} abierto=${!oculto} /></div>`;
}

/** Pantalla completa bloqueada (sesión o memoria abierta directo). Sin botón
 *  propio: el candado está arriba, a la vista, en esta misma pantalla. */
export function PantallaPrivada({ lang }) {
  const L = dict(lang);
  return html`
    <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:40px 20px;text-align:center">
      <span style="font-size:34px;opacity:.5">⚿</span>
      <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.12em;text-transform:uppercase;opacity:.6">${L.tPriv}</span>
      <span style="font-size:12.5px;color:var(--text-2);max-width:32ch;line-height:1.5">${L.tPrivAbrirArriba}</span>
    </div>`;
}
