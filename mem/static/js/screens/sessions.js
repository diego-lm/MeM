// Las sesiones viven en el buscador del Main (pedido 2026-08-01): la ruta
// #sessions ya no tiene pantalla propia. Redirige por los deep links viejos.
import { html, useEffect } from "../../vendor/preact-htm.js";
import { reemplazar } from "../state.js";

export function Sessions() {
  useEffect(() => reemplazar("home"), []);
  return html``;
}
