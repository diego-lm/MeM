// Autocheck del candado de lo privado (mem/static/js/privado.js): quién queda
// tapado en el celular sin verificar. Es la única lógica de la app que decide si
// algo se ve o no, así que se prueba sola:
//
//     node tests/privado.check.mjs
//
// ponytail: sin framework y sin package.json — tres stubs y el módulo REAL. El
// resto del front no tiene checks; este los tiene porque es el que, si se rompe,
// muestra en un teléfono prestado lo que nadie tenía que ver.
globalThis.matchMedia = () => ({ matches: false });
globalThis.document = { addEventListener() {} };
globalThis.localStorage = { getItem: () => null, setItem() {} };

const { privadosDe, esMemoriaPrivada, esSesionPrivada } =
  await import(new URL("../mem/static/js/privado.js", import.meta.url));

const privs = privadosDe([{ nombre: "Salud", ambito: "privado" },
                          { nombre: "Yachay", ambito: "trabajo" }]);
const eq = (a, b, msg) => { if (a !== b) throw new Error(`${msg}: ${a} !== ${b}`); };

eq(privs.has("Salud"), true, "Salud es privado");
// el campo propio de la memoria manda, con lista de proyectos o sin ella
eq(esMemoriaPrivada({ privada: true }, privs), true, "campo privada");
eq(esMemoriaPrivada({ privada: true }, undefined), true, "campo privada sin lista");
eq(esMemoriaPrivada({ privada: false, subjects: ["Tecnologia/IA"] }, privs), false, "memoria común");
// pedido 2026-08-23: el proyecto de HOY la tapa aunque el campo diga que no —
// un proyecto que pasa a privado, o una memoria que se muda a uno
eq(esMemoriaPrivada({ subjects: ["Proyectos/Salud"] }, privs), true, "memoria en proyecto privado");
eq(esMemoriaPrivada({ subjects: ["Proyectos/Salud/Estudios"] }, privs), true, "subject colgando del proyecto");
eq(esMemoriaPrivada({ subjects: ["Proyectos/Yachay"] }, privs), false, "proyecto no privado");
// un tema que se llama igual que el proyecto NO es el proyecto
eq(esMemoriaPrivada({ subjects: ["Salud/Analisis"] }, privs), false, "subject homónimo");
// un item del inbox y un medio suelto traen el proyecto en `proyecto`/subjects
eq(esMemoriaPrivada({ proyecto: "Salud" }, privs), true, "item de inbox por campo proyecto");
eq(esMemoriaPrivada({ proyecto: "Yachay", subjects: [] }, privs), false, "inbox de proyecto abierto");
// sin /projects no hay lista y no puede decidir: por eso las pantallas ocultan
// TODO mientras proyectosListos() sea false, en vez de fiarse de esto
eq(esMemoriaPrivada({ subjects: ["Proyectos/Salud"] }, privadosDe([])), false, "sin lista no decide");
// una sesión del mismo proyecto se mide igual: mismo criterio, misma respuesta
eq(esSesionPrivada({ proyecto: "Salud" }, privs), true, "sesión privada");

console.log("privado.js ok · 13 casos");
