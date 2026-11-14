"""Resumo diário (8h) e semanal (segunda, 8h), horário de Brasília."""
from __future__ import annotations

from datetime import datetime, timedelta
from html import escape

from .db import get_state, rows, set_state
from .extract import CATEGORIES
from .notify import broadcast, fmt_dt, headline, promo_link, promo_route

ACTIVE = "('ativa_verificada','ativa_pendente')"


def _line(con, p) -> tuple[str, str]:
    h = headline(p)
    end = f" · até {fmt_dt(p['end_at'])}" if p.get("end_at") else ""
    link = promo_link(con, p)
    html = f"• <b>{escape(promo_route(p))}</b> — {escape(h or p['title'])}{escape(end)}\n  <a href=\"{escape(link)}\">{escape(p['title'][:90])}</a>"
    txt = f"• {promo_route(p)} — {h or p['title']}{end}\n  {link}"
    return html, txt


def _section(con, title, plist, html, txt, empty="Nenhuma."):
    html.append(f"\n<b>{escape(title)}</b>")
    txt.append(f"\n{title}")
    if not plist:
        html.append(empty); txt.append(empty)
    for p in plist:
        h, t = _line(con, p)
        html.append(h); txt.append(t)


def _score(p) -> float:
    mil = (60 - p["milheiro"]) * 3 if p["milheiro"] and p["category"] in ("compra_pontos", "clube") else 0
    disc = (p["discount_pct"] or 0) * (1 if p["category"] == "compra_pontos" else 0.3)
    return (p["bonus_pct"] or 0) + (p["ppr"] or 0) * 8 + disc + mil - (40 if p["category"] == "resgate" else 0)


def build_daily(con, now: datetime) -> tuple[str, str, str]:
    since = (now - timedelta(hours=24)).isoformat()
    soon = (now + timedelta(hours=24)).isoformat()
    new = rows(con, "SELECT * FROM promos WHERE first_seen>=? AND status!='encerrada' ORDER BY first_seen DESC", since)
    active = rows(con, f"SELECT * FROM promos WHERE status IN {ACTIVE}")
    best = sorted(active, key=_score, reverse=True)[:8]
    ending = rows(con, f"SELECT * FROM promos WHERE status IN {ACTIVE} AND end_at BETWEEN ? AND ? ORDER BY end_at", now.isoformat(), soon)
    changes = rows(con, "SELECT c.*, p.title FROM promo_changes c JOIN promos p ON p.id=c.promo_id WHERE c.at>=? ORDER BY c.at DESC", since)
    subj = f"[Milhas] Resumo diário — {now:%d/%m/%Y}"
    html = [f"☀️ <b>Resumo diário de pontos e milhas — {now:%d/%m/%Y}</b>"]
    txt = [f"Resumo diário de pontos e milhas — {now:%d/%m/%Y}"]
    if not new:
        html.append("\nNenhuma nova campanha foi identificada nas últimas 24 horas.")
        txt.append("\nNenhuma nova campanha foi identificada nas últimas 24 horas.")
    else:
        _section(con, f"Novas nas últimas 24h ({len(new)})", new[:15], html, txt)
    _section(con, "Melhores oportunidades ativas", best, html, txt)
    _section(con, "Encerrando nas próximas 24h", ending, html, txt)
    html.append("\n<b>Alterações relevantes</b>"); txt.append("\nAlterações relevantes")
    if not changes:
        html.append("Nenhuma."); txt.append("Nenhuma.")
    for c in changes[:10]:
        s = f"• {c['title'][:70]}: {c['field']} {c['old']} → {c['new']}"
        html.append(escape(s)); txt.append(s)
    return subj, "\n".join(html), "\n".join(txt)


def build_weekly(con, now: datetime) -> tuple[str, str, str]:
    since = (now - timedelta(days=7)).isoformat()
    week = rows(con, "SELECT * FROM promos WHERE first_seen>=?", since)
    subj = f"[Milhas] Resumo semanal — semana até {now:%d/%m/%Y}"
    html = [f"📊 <b>Resumo semanal — {len(week)} campanhas registradas nos últimos 7 dias</b>"]
    txt = [f"Resumo semanal — {len(week)} campanhas registradas nos últimos 7 dias"]
    if not week:
        html.append("\nNenhuma nova campanha foi identificada nesta semana."); txt.append("\nNenhuma nova campanha foi identificada nesta semana.")
        return subj, "\n".join(html), "\n".join(txt)
    _section(con, "Principais promoções da semana", sorted(week, key=_score, reverse=True)[:8], html, txt)
    _section(con, "Maiores bônus de transferência", sorted([p for p in week if p["category"] == "transferencia" and p["bonus_pct"]],
                                                         key=lambda p: -p["bonus_pct"])[:6], html, txt)
    _section(con, "Melhores compras bonificadas", sorted([p for p in week if p["category"] == "compra_bonificada" and p["ppr"]],
                                                        key=lambda p: -p["ppr"])[:6], html, txt)
    _section(con, "Menores custos de milheiro", sorted([p for p in week if p["milheiro"]], key=lambda p: p["milheiro"])[:6], html, txt)
    # comparação com histórico
    html.append("\n<b>Comparação com o histórico</b>"); txt.append("\nComparação com o histórico")
    older = rows(con, "SELECT * FROM promos WHERE first_seen<?", since)
    if not older:
        msg = "Ainda não há histórico anterior suficiente para comparação."
        html.append(msg); txt.append(msg)
    else:
        for cat in ("transferencia", "compra_bonificada"):
            key = "bonus_pct" if cat == "transferencia" else "ppr"
            cur = [p[key] for p in week if p["category"] == cat and p[key]]
            old = [p[key] for p in older if p["category"] == cat and p[key]]
            if cur and old:
                s = f"• {CATEGORIES[cat]}: máximo da semana {max(cur):g} vs. máximo anterior {max(old):g}"
                html.append(escape(s)); txt.append(s)
    from collections import Counter
    top = Counter((p["destination"] or p["program"] or "?") for p in week).most_common(5)
    html.append("\n<b>Programas com mais oportunidades</b>"); txt.append("\nProgramas com mais oportunidades")
    for k, n in top:
        html.append(f"• {escape(k)}: {n}"); txt.append(f"• {k}: {n}")
    return subj, "\n".join(html), "\n".join(txt)


def maybe_send(con, settings: dict, now: datetime, log: list[str], force: str | None = None) -> list[dict]:
    r = settings["reports"]
    done = []
    today = now.date().isoformat()
    week_id = f"{now.isocalendar().year}-W{now.isocalendar().week}"
    jobs = []
    if force == "daily" or (force is None and now.hour >= r["daily_hour"] and get_state(con, "daily_sent") != today):
        jobs.append(("daily", build_daily, "daily_sent", today))
    if force == "weekly" or (force is None and now.weekday() == r["weekly_weekday"] and now.hour >= r["weekly_hour"]
                             and get_state(con, "weekly_sent") != week_id):
        jobs.append(("weekly", build_weekly, "weekly_sent", week_id))
    for kind, fn, key, val in jobs:
        subj, html, txt = fn(con, now)
        sent = broadcast(subj, html, txt, log)
        set_state(con, key, val)
        reps = get_state(con, "reports", [])
        reps.insert(0, {"kind": kind, "at": now.isoformat(), "subject": subj, "text": txt, "sent": sent})
        set_state(con, "reports", reps[:30])
        done.append({"kind": kind, "sent": sent})
    return done
