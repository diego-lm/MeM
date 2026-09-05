// Candado de lo privado (pedido 2026-08-05): las memorias y sesiones de un
// proyecto con ámbito "privado" se ven siempre en desktop (marcadas en rojo),
// pero en el celular van ocultas — solo un botón "Ver privado" — hasta
// verificar al usuario con el propio teléfono (huella/cara/PIN vía WebAuthn).
// Apagar la pantalla o irse a otra app vuelve a cerrar el candado.
import { html, useState, useEffect } from "../vendor/preact-htm.js";
import { dict } from "./i18n.js";

const CRED_KEY = "mem.privado.cred";
// el candado es del celular: puntero primario táctil = dispositivo que se presta
const esMovil = matchMedia("(pointer: coarse)").matches;

let desbloqueado = false;
const subs = new Set();
const emitir = () => subs.forEach((fn) => fn(desbloqueado));

// pantalla apagada o app en segundo plano → el candado se cierra solo
document.addEventListener("visibilitychange", () => {
  if (document.hidden && desbloqueado) { desbloqueado = false; emitir(); }
});

const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));
const desB64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

/** Verificación con el propio dispositivo. La primera vez registra una
 *  credencial de plataforma (el sistema pide huella/cara/PIN); las siguientes
 *  solo piden verificar. No hay firma que validar en el server: el gate es el
 *  prompt del sistema operativo — la API ya tiene su propia auth.
 *  ponytail: sin WebAuthn (PWA por http:// en la LAN) el tap de "Ver" es el
 *  único gate; servir por https activa la verificación real solo. */
async function verificarDispositivo() {
  if (!window.isSecureContext || !window.PublicKeyCredential) return true;
  const challenge = crypto.getRandomValues(new Uint8Array(16));
  try {
    const guardada = localStorage.getItem(CRED_KEY);
    if (guardada) {
      await navigator.credentials.get({ publicKey: {
        challenge, timeout: 60000, userVerification: "required",
        allowCredentials: [{ type: "public-key", id: desB64(guardada) }] } });
    } else {
      const cred = await navigator.credentials.create({ publicKey: {
        challenge, timeout: 60000, rp: { name: "MeM" },
        user: { id: crypto.getRandomValues(new Uint8Array(16)), name: "diego", displayName: "Diego" },
        pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
        authenticatorSelection: { authenticatorAttachment: "platform", userVerification: "required" } } });
      localStorage.setItem(CRED_KEY, b64(cred.rawId));
    }
    return true;
  } catch { return false; }
}

export async function desbloquear() {
  if (await verificarDispositivo()) { desbloqueado = true; emitir(); }
}

/** Gate de UNA acción, no del candado global: en el celular exige la huella,
 *  en la compu pasa derecho. Lo usa el backup de Ajustes — sacar toda la
 *  memoria a una carpeta es lo más sensible que hace la app, y el celular es
 *  el aparato que se presta. */
export const verificarSiMovil = () => (esMovil ? verificarDispositivo() : Promise.resolve(true));

/** oculto=true → lo privado no se muestra (celular sin verificar). */
export function usePrivado() {
  const [, force] = useState(0);
  useEffect(() => {
    const fn = () => force((n) => n + 1);
    subs.add(fn);
    return () => subs.delete(fn);
  }, []);
  return { oculto: esMovil && !desbloqueado, desbloquear };
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

/** Botón "Ver": lo único que delata que hay contenido privado oculto. */
export function BotonVerPrivado({ lang }) {
  const L = dict(lang);
  return html`
    <div role="button" tabindex="0" onClick=${desbloquear} class="mem-priv-ver">⚿ ${L.tPrivVer}</div>`;
}

/** Pantalla completa bloqueada (sesión o memoria abierta directo). */
export function PantallaPrivada({ lang }) {
  const L = dict(lang);
  return html`
    <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:40px 20px">
      <span style="font-size:34px;opacity:.5">⚿</span>
      <span style="font-family:var(--font-mono);font-size:11px;letter-spacing:.12em;text-transform:uppercase;opacity:.6">${L.tPriv}</span>
      <${BotonVerPrivado} lang=${lang} />
    </div>`;
}
