"""CLI.

  python -m milhas run [--manual] [--only id1,id2]   ciclo completo (coleta → dashboard → alertas)
  python -m milhas seed seed/arquivo.json            carrega publicações reais salvas (teste)
  python -m milhas reprocess                         reextrai tudo o que já foi coletado (sem rede)
  python -m milhas export                            só regenera o dashboard
  python -m milhas report daily|weekly               força o envio de um resumo
  python -m milhas test-notify                       envia mensagem de teste pelos canais configurados
  python -m milhas sources                           mostra o status das fontes
"""
from __future__ import annotations

import json
import sys

from .config import DB_PATH, load_settings, now
from .db import connect, rows


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "run":
        from .pipeline import run
        only = None
        if "--only" in argv:
            only = argv[argv.index("--only") + 1].split(",")
        print(json.dumps(run("manual" if "--manual" in argv else "agendado", only), ensure_ascii=False, indent=2, default=str))
    elif cmd == "seed":
        from .pipeline import ingest_seed
        print(json.dumps(ingest_seed(argv[1]), ensure_ascii=False, indent=2, default=str))
    elif cmd == "reprocess":
        from .pipeline import reprocess
        print(json.dumps(reprocess(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "export":
        from .site import export_site
        con = connect(DB_PATH)
        export_site(con, load_settings())
        print("dashboard gerado em site/index.html")
    elif cmd == "report":
        from .reports import maybe_send
        con = connect(DB_PATH)
        log: list[str] = []
        print(maybe_send(con, load_settings(), now(), log, force=argv[1]), log)
        con.commit()
    elif cmd == "test-notify":
        from .notify import broadcast, channels_status
        log: list[str] = []
        print("canais:", channels_status())
        print("enviado por:", broadcast("[Milhas] teste", "✅ <b>Teste</b> do monitor de pontos e milhas.", "Teste do monitor de pontos e milhas.", log), log)
    elif cmd == "sources":
        con = connect(DB_PATH)
        for s in rows(con, "SELECT id,status,method,last_check,consecutive_errors,last_error FROM sources ORDER BY tier DESC"):
            print(f"{s['id']:22} {s['status'] or '':14} {s['method'] or '-':8} {s['last_check'] or '-':32} {s['last_error'] or ''}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
