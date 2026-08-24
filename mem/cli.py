"""CLI de MeM. El motor no necesita servidor: todo se puede operar desde acá."""
import argparse
import json
import sys
from pathlib import Path

from . import chat, config, lint, memoria, modos, procesar, reorganizar, sesiones, sintesis

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # consolas Windows cp1252


def _lista(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser(prog="mem", description="MeM — memoria persistente + sesiones sobre hamuQ")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ask", help="pregunta única, sin sesión")
    s.add_argument("pregunta"); s.add_argument("--modo", default="chat"); s.add_argument("--subjects", default="")
    s = sub.add_parser("chat", help="REPL de chat con sesión persistente")
    s.add_argument("--modo", default="chat"); s.add_argument("--sesion")
    s.add_argument("--titulo", default="sesion"); s.add_argument("--subjects", default="")
    s = sub.add_parser("sessions", help="listar sesiones")
    s.add_argument("--modo")
    s = sub.add_parser("search", help="buscar en los índices")
    s.add_argument("consulta")
    s = sub.add_parser("archive", help="destilar y archivar una sesión")
    s.add_argument("sesion")
    sub.add_parser("modes", help="listar modos de vista disponibles")
    sub.add_parser("index", help="reconstruir el índice de búsqueda (FTS5 + embeddings + geocache)")
    s = sub.add_parser("lint", help="chequeos de consistencia de la base")
    s.add_argument("--semantico", action="store_true",
                   help="además, un pase con el LLM: contradicciones, datos obsoletos, huecos")
    sub.add_parser("ids", help="dar id y versión a las entradas anteriores al versionado (one-shot)")
    sub.add_parser("inbox", help="listar el inbox (pendientes + procesadas recientes)")
    sub.add_parser("process", help="procesar el inbox: entender, categorizar, etiquetar, indexar")
    s = sub.add_parser("reorganizar", help="re-evaluar los temas de toda la Biblioteca con el LLM y reconstruir índices")
    s.add_argument("--aplicar", action="store_true", help="escribir los cambios (sin esto: imprime el plan y no toca nada)")
    s.add_argument("--plan", help="JSON de un plan ya calculado (evita re-llamar al LLM al aplicar)")
    s = sub.add_parser("tags", help="unificar tags duplicados de la Biblioteca (mayúsculas, guiones, tildes)")
    s.add_argument("--aplicar", action="store_true", help="escribir los cambios (sin esto: imprime el mapa y no toca nada)")
    s = sub.add_parser("sintesis", help="crear/regenerar la página de síntesis de un tema")
    s.add_argument("subject", help='tema, p.ej. "Tecnologia/VR"')
    s = sub.add_parser("capture", help="capturar una nota al inbox")
    s.add_argument("texto"); s.add_argument("--tipo", default="nota"); s.add_argument("--contexto", default="")
    s.add_argument("--tags", default=""); s.add_argument("--subjects", default="")
    s = sub.add_parser("serve", help="levantar API + UI web")
    s.add_argument("--port", type=int)

    a = ap.parse_args()
    cfg = config.cargar()
    root = cfg["hamuq"]

    if a.cmd == "search":
        print(memoria.buscar(root, a.consulta))
    elif a.cmd == "ask":
        modo = modos.cargar(root, a.modo)
        texto, paginas, tokens = chat.responder(
            cfg, modo, [{"role": "user", "content": a.pregunta}],
            on_event=lambda n, args: print(f"  · {n}({json.dumps(args, ensure_ascii=False)[:100]})"),
            subjects=_lista(a.subjects))
        print(f"\n{texto}\n\n[páginas: {', '.join(paginas) or '—'} | tokens entrada: {tokens}]")
    elif a.cmd == "chat":
        sid = a.sesion or sesiones.crear(root, _lista(a.subjects), a.titulo, a.modo)
        print(f"sesión: {sid}  (salir: 'salir' o Ctrl+C)")
        while True:
            try:
                linea = input("\ntú> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not linea or linea.lower() == "salir":
                break
            texto, paginas, tokens = chat.turno(cfg, sid, linea, on_event=lambda n, args: print(f"  · {n}"))
            print(f"\n{texto}\n\n[{', '.join(paginas) or 'sin páginas'} | {tokens} tokens entrada]")
    elif a.cmd == "sessions":
        for m in sesiones.listar(root, a.modo):
            subs = ", ".join(m.get("subjects") or []) or "—"
            print(f"{m.get('id')}  [{m.get('modo')}]  ({subs})  turnos={m.get('turnos', 0)}  {str(m.get('titulo', ''))[:60]}")
    elif a.cmd == "modes":
        for m in modos.listar(root):
            print(f"{m['nombre']}  —  {m['descripcion']}")
    elif a.cmd == "archive":
        r = chat.destilar(cfg, a.sesion)
        print(f"sincronizado: {r['inbox']} ({r['estado']})" if r["sincronizado"] else "(sesión vacía, nada que sincronizar)")
        print("→", sesiones.archivar(root, a.sesion))
    elif a.cmd == "ids":
        print(f"{memoria.asignar_ids(root)} entradas migradas")
    elif a.cmd == "index":
        from . import indice
        r = indice.reindexar(root)
        print(r.get("error") or (f"{r['entradas']} entradas, {r['con_embedding']} con embedding, "
                                 f"{r['geocodificados']} lugares geocodificados, {r['ms']} ms"))
    elif a.cmd == "lint":
        if a.semantico:
            print(f"{len(lint.semantico(cfg))} avisos semánticos nuevos")
        problemas = lint.correr(root)
        print("\n".join(problemas) or "sin problemas")
        memoria.log_evento(root, "lint", f"{len(problemas)} problemas detectados")
    elif a.cmd == "inbox":
        for it in memoria.inbox_listar(root):
            print(f"[{it.get('estado')}] {it['id']}  ({it.get('tipo')})  {it['texto'][:70]}")
    elif a.cmd == "sintesis":
        r = sintesis.sintetizar(cfg, a.subject)
        print(f"{r['resultado']} ({r['fuentes']} fuentes)")
    elif a.cmd == "capture":
        print("→", memoria.capturar(root, a.texto, a.tipo, a.contexto,
                                    _lista(a.tags), _lista(a.subjects)))
    elif a.cmd == "process":
        r = procesar.procesar_inbox(cfg)
        print(f"{r['procesadas']} procesadas, {r['errores']} con error")
        for e in r["detalle_errores"]:
            print(f"  ⚠ {e['id']}: {e['error']}")
    elif a.cmd == "reorganizar":
        plan = json.loads(Path(a.plan).read_text(encoding="utf-8")) if a.plan else reorganizar.planear(cfg)
        if a.aplicar:
            r = reorganizar.aplicar(cfg, plan)
            print(f"{r['cambiadas']} entradas recategorizadas · {r['indices']}")
        else:
            print(json.dumps(plan, ensure_ascii=False, indent=1))
    elif a.cmd == "tags":
        mapa = reorganizar.plan_tags(root)
        if a.aplicar:
            r = reorganizar.aplicar_tags(cfg, mapa)
            print(f"{r['cambiadas']} entradas normalizadas · {r['indices']}")
        else:
            for viejo, nuevo in sorted(mapa.items()):
                print(f"  {viejo!r} → {nuevo!r}" if nuevo else f"  {viejo!r} → (borrar)")
            print(f"{len(mapa)} tags a unificar (correr con --aplicar para escribirlo)")
    elif a.cmd == "serve":
        import uvicorn
        uvicorn.run("mem.api:app", host="0.0.0.0", port=a.port or int(cfg["puerto"]))


if __name__ == "__main__":
    main()
