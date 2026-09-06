// Autocheck del candado de lo privado (mem/static/js/privado.js): quién queda
// tapado en el celular sin verificar, y cuándo se avisa que lo privado igual
// sale de la máquina. Es la única lógica de la app que decide si algo se ve o
// no, así que se prueba sola:
//
//     node tests/privado.check.mjs
//
// ponytail: sin framework y sin package.json — tres stubs y el módulo REAL. El
// resto del front no tiene checks; este los tiene porque es el que, si se rompe,
// muestra en un teléfono prestado lo que nadie tenía que ver.
globalThis.matchMedia = () => ({ matches: false });
globalThis.document = { addEventListener() {} };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.addEventListener = () => {};
globalThis.setInterval = () => 0;   // el vencimiento del candado no debe dejar vivo a node

const { privadosDe, esMemoriaPrivada, esSesionPrivada, esLocal, AvisoNube } =
  await import(new URL("../mem/static/js/privado.js", import.meta.url));

// Lo privado es del PROYECTO y no de la memoria (2026-09-05): la lista sale del
// switch `privado` de cada proyecto, no de un ámbito ni de un campo por memoria.
const privs = privadosDe([{ nombre: "Salud", privado: true },
                          { nombre: "Yachay", privado: false }]);
const eq = (a, b, msg) => { if (a !== b) throw new Error(`${msg}: ${a} !== ${b}`); };

eq(privs.has("Salud"), true, "Salud es privado");
eq(privs.has("Yachay"), false, "Yachay no lo es");
eq(esMemoriaPrivada({ subjects: ["Tecnologia/IA"] }, privs), false, "memoria sin proyecto");
// el proyecto de HOY la tapa: togglearlo, o mudar una memoria, cambia la
// visibilidad al instante y sin migrar ningún campo
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

// -- aviso de nube (pedido 2026-09-05) --------------------------------------
// Privado tapa lo que se VE en la app; no cambia a dónde va el texto. El aviso
// aparece solo si el agente que atiende no corre en esta máquina.
const local = { nombre: "Cheap", modelo: "qwen/qwen3-vl-8b", proveedor: "openai" };
const claude = { nombre: "Smart", modelo: "sonnet", proveedor: "claude_code" };
eq(esLocal(local), true, "openai (LM Studio) es el proveedor local");
eq(esLocal(claude), false, "claude_code sale de la máquina");
eq(esLocal({ proveedor: "anthropic" }), false, "la API de Claude también sale");
eq(AvisoNube({ agentes: [local] }), null, "todo local: sin aviso");
eq(AvisoNube({ agentes: [] }), null, "sin agentes: sin aviso");
eq(AvisoNube({ agentes: [null, undefined] }), null, "agentes a medio cargar: sin aviso");
eq(AvisoNube({ agentes: [local, claude] }) === null, false, "uno solo en la nube ya avisa");

console.log("privado.js ok · 18 casos");
