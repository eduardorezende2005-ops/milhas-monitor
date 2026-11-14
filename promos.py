"""Deduplicação, mesclagem de fontes, detecção de mudanças e cálculo de status."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from .config import TZ
from .db import rows
from .extract import Extracted, strip_accents

TRACKED_FIELDS = ("bonus_pct", "ppr", "discount_pct", "milheiro", "bonus_points", "end_at")
FIELD_LABEL = {"bonus_pct": "bônus (%)", "ppr": "pontos por real", "discount_pct": "desconto (%)",
               "milheiro": "milheiro (R$)", "bonus_points": "pontos bônus", "end_at": "encerramento"}

STATUS_LABEL = {
    "ativa_verificada": "Ativa e verificada", "ativa_pendente": "Ativa, pendente de confirmação",
    "futura": "Futura", "encerrada": "Encerrada", "indeterminada": "Indeterminada",
}


def _dt(s):
    return datetime.fromisoformat(s) if s else None


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", strip_accents((s or "").lower())).strip()


def _words(s: str) -> set[str]:
    return {w for w in _norm(s).split() if len(w) > 2}


def fingerprint(e: Extracted) -> str:
    end = e.end_at.date().isoformat() if e.end_at else "sem-data"
    return "|".join([e.category, e.program or "?", ",".join(sorted(e.origin)) or "-", _norm(e.partner) or "-", end])


def _dates_compatible(e: Extracted, p: dict, published: datetime) -> bool:
    pend = _dt(p["end_at"])
    if e.end_at and pend:
        return abs((e.end_at - pend).total_seconds()) <= 36 * 3600
    ppub = _dt(p["published_at"]) or _dt(p["first_seen"])
    return ppub is not None and abs((published - ppub).total_seconds()) <= 3 * 86400


def find_match(con, e: Extracted, published: datetime, title: str) -> dict | None:
    cutoff = (published - timedelta(days=30)).isoformat()
    cands = rows(con, "SELECT * FROM promos WHERE category=? AND last_seen>=?", e.category, cutoff)
    best = None
    for p in cands:
        if not _dates_compatible(e, p, published):
            continue
        if e.category == "transferencia":
            if not (e.destination and p["destination"] == e.destination):
                continue
            po = set(json.loads(p["origin"] or "[]"))
            eo = set(e.origin)
            if po and eo and not (po & eo) and "Todos os parceiros" not in (po | eo):
                continue
            if e.bonus_pct and p["bonus_pct"] and e.bonus_pct != p["bonus_pct"] and not (e.end_at and p["end_at"]):
                continue
        elif e.category == "compra_bonificada":
            if p["program"] != e.program or _norm(p["partner"]) != _norm(e.partner):
                continue
            if not e.partner and (e.ppr != p["ppr"]):
                continue
        else:
            if p["program"] != e.program:
                continue
            if e.category in ("outra", "resgate", "cartao"):
                a, b = _words(title), _words(p["title"])
                if not a or len(a & b) / len(a | b) < (0.8 if e.category == "resgate" else 0.5):
                    continue
        best = p
        break
    return best


def upsert(con, e: Extracted, item: dict, tier: int, now: datetime) -> tuple[int, str, list[dict]]:
    """Insere ou mescla. Retorna (promo_id, 'nova'|'atualizada'|'mesma', mudanças)."""
    published = item["published"]
    p = None
    if item.get("promo_id"):  # mesmo artigo atualizado → mesma campanha (ex.: prorrogação)
        r = con.execute("SELECT * FROM promos WHERE id=? AND category=?", (item["promo_id"], e.category)).fetchone()
        p = dict(r) if r else None
    if p is None:
        p = find_match(con, e, published, item["title"])
    values = {
        "category": e.category, "program": e.program, "origin": json.dumps(e.origin, ensure_ascii=False),
        "destination": e.destination, "partner": e.partner, "bonus_pct": e.bonus_pct, "ppr": e.ppr,
        "discount_pct": e.discount_pct, "milheiro": e.milheiro, "milheiro_calculado": int(e.milheiro_calculado),
        "bonus_points": e.bonus_points, "club_required": e.club_required,
        "registration_required": e.registration_required, "min_transfer": e.min_transfer,
        "limit_text": e.limit_text, "credit_deadline": e.credit_deadline, "validity": e.validity,
        "conditions": json.dumps(e.conditions, ensure_ascii=False),
        "start_at": e.start_at.isoformat() if e.start_at else None,
        "end_at": e.end_at.isoformat() if e.end_at else None, "end_source": e.end_source,
        "official_url": e.official_links[0] if e.official_links else None,
        "expired_flag": int(e.expired_flag) or None,
    }
    changes: list[dict] = []
    if p is None:
        values.update(fingerprint=fingerprint(e), title=item["title"], first_seen=now.isoformat(),
                      last_seen=now.isoformat(), best_tier=tier, published_at=published.isoformat(), status="")
        cols = ",".join(values)
        cur = con.execute(f"INSERT INTO promos({cols}) VALUES({','.join('?' * len(values))})", list(values.values()))
        pid, kind = cur.lastrowid, "nova"
    else:
        pid, kind = p["id"], "mesma"
        upd = {"last_seen": now.isoformat()}
        newer = published.isoformat() >= (p["published_at"] or "")
        authoritative = tier > (p["best_tier"] or 0) or (tier == p["best_tier"] and newer)
        for k, v in values.items():
            old = p.get(k)
            if v in (None, "", "[]", "não informado"):
                continue
            if old in (None, "", "[]", "não informado"):
                upd[k] = v
            elif v != old and authoritative:
                upd[k] = v
                if k in TRACKED_FIELDS:
                    changes.append({"field": k, "old": old, "new": v})
        if tier > (p["best_tier"] or 0):
            upd["best_tier"] = tier
            upd["title"] = item["title"]
        if len(upd) > 1:
            kind = "atualizada"
        con.execute(f"UPDATE promos SET {','.join(k + '=?' for k in upd)} WHERE id=?", [*upd.values(), pid])
        for c in changes:
            con.execute("INSERT INTO promo_changes(promo_id,field,old,new,at,source_id) VALUES(?,?,?,?,?,?)",
                        (pid, c["field"], str(c["old"]), str(c["new"]), now.isoformat(), item["source_id"]))
    con.execute("INSERT INTO promo_sources(promo_id,item_id,source_id,url,title,seen_at,tier,end_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(promo_id,url) DO UPDATE SET title=excluded.title, seen_at=excluded.seen_at, end_at=excluded.end_at",
                (pid, item.get("item_id"), item["source_id"], item["url"], item["title"], now.isoformat(), tier,
                 values["end_at"]))
    return pid, kind, changes


def compute_status(con, p: dict, now: datetime, undated_max_days: int) -> str:
    start, end = _dt(p["start_at"]), _dt(p["end_at"])
    if p.get("expired_flag"):
        return "encerrada"
    if start and now < start:
        return "futura"
    if end:
        if now > end:
            return "encerrada"
        srcs = rows(con, "SELECT source_id, tier, end_at FROM promo_sources WHERE promo_id=?", p["id"])
        official = any(s["tier"] == 3 for s in srcs) or bool(p["official_verified_at"])
        agreeing = {s["source_id"] for s in srcs if s["end_at"] and abs((_dt(s["end_at"]) - end).total_seconds()) <= 36 * 3600}
        return "ativa_verificada" if official or len(agreeing) >= 2 else "ativa_pendente"
    return "indeterminada"


def refresh_statuses(con, now: datetime, undated_max_days: int) -> list[tuple[int, str, str]]:
    transitions = []
    for p in rows(con, "SELECT * FROM promos WHERE status IS NULL OR status!='encerrada' OR end_at>=?",
                  (now - timedelta(days=2)).isoformat()):
        st = compute_status(con, p, now, undated_max_days)
        if st != p["status"]:
            transitions.append((p["id"], p["status"], st))
            con.execute("UPDATE promos SET status=?, last_verified=? WHERE id=?", (st, now.isoformat(), p["id"]))
        else:
            con.execute("UPDATE promos SET last_verified=? WHERE id=?", (now.isoformat(), p["id"]))
    return transitions
