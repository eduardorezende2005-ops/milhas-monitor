"""Indicadores históricos — calculados só com campanhas realmente registradas no banco."""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from .db import rows


def _bucket(b: int) -> str:
    return "<50%" if b < 50 else "50–79%" if b < 80 else "80–99%" if b < 100 else "≥100%"


def history(con, now: datetime) -> dict:
    allp = rows(con, "SELECT * FROM promos ORDER BY published_at")
    out: dict = {"total_campaigns": len(allp)}

    # transferências: frequência de faixas de bônus por rota
    routes: dict[str, list] = defaultdict(list)
    for p in allp:
        if p["category"] == "transferencia" and p["bonus_pct"] and p["destination"]:
            for o in json.loads(p["origin"] or "[]") or ["?"]:
                routes[f"{o} → {p['destination']}"].append({"bonus": p["bonus_pct"], "date": (p["published_at"] or "")[:10], "id": p["id"]})
    out["transfer_routes"] = sorted(
        [{"route": r, "count": len(v), "max": max(x["bonus"] for x in v), "median": statistics.median(x["bonus"] for x in v),
          "buckets": dict(Counter(_bucket(x["bonus"]) for x in v)), "events": v[-12:]} for r, v in routes.items()],
        key=lambda x: (-x["count"], -x["max"]))

    # compras bonificadas: pontuação atual vs mediana da loja
    stores: dict[str, list] = defaultdict(list)
    for p in allp:
        if p["category"] == "compra_bonificada" and p["ppr"] and p["partner"] and p["partner"] != "Várias lojas":
            stores[f"{p['program']} · {p['partner']}"].append({"ppr": p["ppr"], "date": (p["published_at"] or "")[:10], "id": p["id"]})
    out["stores"] = sorted(
        [{"store": s, "count": len(v), "median": statistics.median(x["ppr"] for x in v), "max": max(x["ppr"] for x in v),
          "last": v[-1]["ppr"], "events": v[-12:]} for s, v in stores.items()],
        key=lambda x: (-x["count"], -x["max"]))

    # milheiro por programa
    mil: dict[str, list] = defaultdict(list)
    for p in allp:
        if p["milheiro"] and p["category"] in ("compra_pontos", "clube"):
            mil[p["program"] or "?"].append({"value": p["milheiro"], "date": (p["published_at"] or "")[:10], "id": p["id"]})
    out["milheiro"] = [{"program": k, "count": len(v), "min": min(x["value"] for x in v),
                        "median": statistics.median(x["value"] for x in v), "events": v[-20:]} for k, v in mil.items()]

    # clubes
    clubs = Counter(p["program"] or "?" for p in allp if p["category"] == "clube")
    out["clubs"] = [{"program": k, "count": c} for k, c in clubs.most_common()]

    # programas com mais oportunidades (7 e 30 dias)
    def top(days):
        lim = (now - timedelta(days=days)).isoformat()
        c = Counter((p["destination"] or p["program"] or "?") for p in allp if (p["first_seen"] or "") >= lim)
        return [{"program": k, "count": n} for k, n in c.most_common(10)]
    out["top_programs_7d"], out["top_programs_30d"] = top(7), top(30)

    # dias distintos com dados — o dashboard só mostra gráficos quando há base real
    out["days_of_data"] = len({(p["first_seen"] or "")[:10] for p in allp})
    return out
