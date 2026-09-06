// Cache del shell de la app para que "cero pérdida" (spec §8.1) también valga
// sin red: la UI y la cola de captura offline (js/api.js) siguen funcionando.
// Datos (API) siempre van a la red — nunca se cachean, se mostrarían obsoletos.
const CACHE = "mem-shell-v116";
const PRECACHE = [
  "/", "/manifest.webmanifest", "/assets/icon-192.png", "/assets/icon-512.png", "/assets/favicon.ico",
  "/css/ds.css", "/css/app.css",
  "/vendor/preact-htm.js", "/vendor/d3-force.js",
  "/vendor/leaflet/leaflet.js", "/vendor/leaflet/leaflet.css",
  "/fonts/archivo-variable.woff2", "/fonts/inter-variable.woff2", "/fonts/fraunces-variable.woff2",
  "/js/app.js", "/js/state.js", "/js/api.js", "/js/i18n.js", "/js/md.js", "/js/ui.js", "/js/version.js", "/js/privado.js", "/js/vistazo.js", "/js/clima.js", "/js/proyectos.js",
  "/js/vis/util.js", "/js/vis/grafo.js", "/js/vis/timeline.js", "/js/vis/scatter.js", "/js/vis/geomapa.js",
  "/js/screens/home.js", "/js/screens/inbox.js", "/js/screens/memory.js", "/js/screens/entry.js",
  "/js/screens/lint.js", "/js/screens/sessions.js", "/js/screens/chat.js", "/js/screens/settings.js",
];
// ponytail: la mascota (1,1MB) no se precachea; queda cacheada en runtime la
// primera vez que se ve, vía la misma regla cache-first de abajo.
// OJO: addAll es todo-o-nada — un solo 404 acá tumba la instalación entera del
// service worker y la app se queda SIN offline. Pasó con las Caprasimo/Figtree,
// que siguieron listadas después de que el reskin las borrara del disco.
const ESTATICO = ["/css/", "/js/", "/vendor/", "/fonts/", "/assets/"];

self.addEventListener("install", (e) => {
  // cache:"no-cache" revalida contra el server — sin esto el precache de una
  // versión nueva puede llenarse con archivos viejos del HTTP cache del navegador.
  e.waitUntil(caches.open(CACHE).then((c) =>
    c.addAll(PRECACHE.map((u) => new Request(u, { cache: "no-cache" })))));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  const esShell = url.pathname === "/" || ESTATICO.some((p) => url.pathname.startsWith(p)) || url.pathname === "/manifest.webmanifest";
  if (!esShell) return; // API y /attach/*: siempre red, sin cache

  // Network-first: la copia cacheada solo sale si no hay red. Cache-first dejaba
  // la app un reload atrás de los cambios, y cuando una edición tocaba dos
  // módulos (home.js importando algo nuevo de sessions.js) servía uno nuevo y
  // otro viejo: el import fallaba, Preact no montaba nada y quedaba la pantalla
  // de un color sólido, igual en PC que en el móvil.
  // Este fetch "de red" igual pasa por el HTTP cache del navegador, y StaticFiles
  // no mandaba Cache-Control: Chrome cacheaba el JS por heurística (Last-Modified)
  // sin revalidar, así que network-first devolvía código viejo igual. no-cache
  // fuerza la revalidación siempre — sigue habiendo 304, no se re-descarga lo que
  // no cambió. Va acá y no solo en el server porque el SW se actualiza sin
  // reiniciar nada, y el header del server recién existe tras reiniciar uvicorn.
  const pedido = e.request.mode === "navigate" ? e.request : new Request(e.request, { cache: "no-cache" });
  e.respondWith(
    fetch(pedido).then((r) => {
      // waitUntil: sin esto el SW puede morir antes de terminar el put y la
      // entrada vieja se queda para siempre.
      if (r.ok) e.waitUntil(caches.open(CACHE).then((c) => c.put(e.request, r.clone())));
      return r;
    }).catch(() => caches.match(e.request).then(
      (cached) => cached || (e.request.mode === "navigate" ? caches.match("/") : undefined)))
  );
});
