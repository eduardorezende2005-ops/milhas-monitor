"""Gera o dashboard estático: site/data.json + site/index.html (dados embutidos)."""
from __future__ import annotations

import json
import os
from datetime import timedelta

from . import analytics, notify
from .config import SITE_DIR, WEB_TEMPLATE, now as clock
from .db import get_state, rows
from .extract import CATEGORIES
from .promos import STATUS_LABEL


def build_data(con, settings: dict) -> dict:
    now = clock()
    vis = (now - timedelta(days=settings["site"]["ended_days_visible"])).isoformat()
    promos = rows(con, "SELECT * FROM promos WHERE status!='encerrada' OR end_at>=? OR last_seen>=? ORDER BY first_seen DESC", vis, vis)
    for p in promos:
        p["origin"] = json.loads(p["origin"] or "[]")
        p["conditions"] = json.loads(p["conditions"] or "[]")
        p["sources"] = rows(con, """SELECT ps.source_id, s.name, ps.url, ps.title, ps.tier, ps.seen_at FROM promo_sources ps
                                    JOIN sources s ON s.id=ps.source_id WHERE promo_id=? ORDER BY ps.tier DESC, ps.seen_at""", p["id"])
        p["changes"] = rows(con, "SELECT field, old, new, at, source_id FROM promo_changes WHERE promo_id=? ORDER BY at", p["id"])
        p["route"] = notify.promo_route(p | {"origin": json.dumps(p["origin"])})
        p["headline"] = notify.headline(p)
        p["link"] = notify.promo_link(con, p)
    sources = rows(con, "SELECT id,name,url,tier,method,method_url,status,last_check,last_ok,consecutive_errors,total_errors,total_checks,items_found,promos_found,last_error,access_notes,needs_login,cooldown_until FROM sources ORDER BY tier DESC, name")
    for s in sources:
        s["reliability"] = ("alta (fonte oficial)" if s["tier"] == 3 else "alta (portal especializado)" if s["tier"] == 2 else "média (agregador)")
        if s["total_checks"]:
            s["success_rate"] = round(100 * (s["total_checks"] - s["total_errors"]) / s["total_checks"])
    active = [p for p in promos if p["status"] in ("ativa_verificada", "ativa_pendente")]
    soon = (now + timedelta(hours=24)).isoformat()
    day_ago = (now - timedelta(hours=24)).isoformat()
    stats = {
        "active": len(active),
        "new_24h": sum(1 for p in promos if (p["first_seen"] or "") >= day_ago and p["status"] != "encerrada"),
        "ending_24h": sum(1 for p in active if p["end_at"] and p["end_at"] <= soon),
        "transfer": sum(1 for p in active if p["category"] == "transferencia"),
        "bonified": sum(1 for p in active if p["category"] == "compra_bonificada"),
        "buy_points": sum(1 for p in active if p["category"] == "compra_pontos"),
        "clubs": sum(1 for p in active if p["category"] == "clube"),
        "indeterminate": sum(1 for p in promos if p["status"] == "indeterminada"),
        "verified": sum(1 for p in active if p["status"] == "ativa_verificada"),
    }
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return {
        "generated_at": now.isoformat(),
        "last_run": get_state(con, "last_run"),
        "title": settings["site"]["title"],
        "stats": stats,
        "categories": CATEGORIES,
        "status_labels": STATUS_LABEL,
        "promos": promos,
        "sources": sources,
        "alerts": rows(con, "SELECT * FROM alerts ORDER BY id DESC LIMIT 100"),
        "runs": rows(con, "SELECT id,started_at,finished_at,trigger,status,sources_ok,sources_error,sources_skipped,items_new,promos_new,promos_updated,requests,log FROM runs ORDER BY id DESC LIMIT 30"),
        "history": analytics.history(con, now),
        "reports": get_state(con, "reports", []),
        "channels": notify.channels_status(),
        "alert_settings": settings["alerts"],
        "manual_update_url": f"https://github.com/{repo}/actions/workflows/monitor.yml" if repo else "",
    }


def export_site(con, settings: dict) -> None:
    data = build_data(con, settings)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, default=str)
    (SITE_DIR / "data.json").write_text(payload, encoding="utf-8")
    html = WEB_TEMPLATE.read_text(encoding="utf-8")
    safe = payload.replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", safe)
    (SITE_DIR / "index.html").write_text(html, encoding="utf-8")
