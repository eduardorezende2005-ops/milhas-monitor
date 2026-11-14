"""Regras de alerta e canais de notificação (Telegram, e-mail, WhatsApp oficial e dashboard).
Credenciais vêm só de variáveis de ambiente (GitHub Secrets em produção)."""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import statistics
import urllib.request
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from html import escape

from .db import rows
from .extract import CATEGORIES
from .promos import FIELD_LABEL, STATUS_LABEL


# ------------------------------------------------------------------ formatação
def fmt_dt(s: str | None) -> str:
    if not s:
        return "não informado"
    d = datetime.fromisoformat(s)
    return d.strftime("%d/%m/%Y %H:%M") if (d.hour, d.minute) not in ((23, 59), (0, 0)) else d.strftime("%d/%m/%Y")


def headline(p: dict) -> str:
    bits = []
    if p.get("bonus_pct"):
        bits.append(f"até {p['bonus_pct']}% de bônus")
    if p.get("ppr"):
        bits.append(f"até {str(p['ppr']).rstrip('0').rstrip('.')} pts/R$")
    if p.get("discount_pct"):
        bits.append(f"{p['discount_pct']}% de desconto")
    if p.get("milheiro"):
        bits.append(f"milheiro R$ {p['milheiro']:.2f}".replace(".", ","))
    if p.get("bonus_points"):
        bits.append(f"{p['bonus_points']:,} pontos bônus".replace(",", "."))
    return " · ".join(bits)


def promo_link(con, p: dict) -> str:
    if p.get("official_url"):
        return p["official_url"]
    r = con.execute("SELECT url FROM promo_sources WHERE promo_id=? ORDER BY tier DESC, seen_at LIMIT 1", (p["id"],)).fetchone()
    return r["url"] if r else ""


def promo_route(p: dict) -> str:
    if p["category"] == "transferencia":
        origin = ", ".join(json.loads(p.get("origin") or "[]")) or "?"
        return f"{origin} → {p.get('destination') or '?'}"
    if p["category"] == "compra_bonificada" and p.get("partner"):
        return f"{p.get('program') or '?'} · {p['partner']}"
    return p.get("program") or "Programa não identificado"


# ------------------------------------------------------------------ regras
def _add(con, p, kind, title, message, now):
    con.execute("INSERT OR IGNORE INTO alerts(promo_id,kind,title,message,url,created_at) VALUES(?,?,?,?,?,?)",
                (p["id"], kind, title, message, promo_link(con, p), now.isoformat()))


def evaluate(con, settings: dict, now: datetime, new_ids: set[int], changed: dict[int, list[dict]]) -> None:
    a = settings["alerts"]
    progs = set(a.get("programs") or [])
    active = rows(con, "SELECT * FROM promos WHERE status IN ('ativa_verificada','ativa_pendente','futura','indeterminada')")
    for p in active:
        if progs and p["program"] not in progs and p.get("destination") not in progs:
            continue
        cat = CATEGORIES.get(p["category"], p["category"])
        base = f"{promo_route(p)} — {headline(p) or cat}"
        if p["id"] in new_ids:
            if a.get("new_promo") and p["category"] in a.get("new_promo_categories", []):
                _add(con, p, "nova", f"Nova: {p['title']}", base, now)
            if p["category"] == "transferencia" and (p["bonus_pct"] or 0) >= a["transfer_min_bonus"]:
                _add(con, p, "bonus_alto", f"Transferência com {p['bonus_pct']}% de bônus", base, now)
            if p["category"] == "compra_bonificada" and (p["ppr"] or 0) >= a["ppr_min"]:
                _add(con, p, "ppr_alto", f"{p['ppr']:g} pontos por real", base, now)
            if p["category"] == "compra_bonificada" and p["ppr"] and p["partner"]:
                hist = [r["ppr"] for r in rows(con, "SELECT ppr FROM promos WHERE program=? AND partner=? AND id!=? AND ppr IS NOT NULL",
                                               p["program"], p["partner"], p["id"])]
                if len(hist) >= 2 and p["ppr"] >= statistics.median(hist) * a["ppr_above_history_factor"]:
                    _add(con, p, "acima_historico",
                         f"{p['partner']}: {p['ppr']:g} pts/R$ (mediana histórica {statistics.median(hist):g})", base, now)
            if p["category"] in ("compra_pontos", "clube") and p["milheiro"] and p["milheiro"] <= a["milheiro_max"]:
                _add(con, p, "milheiro_barato", f"Milheiro a R$ {p['milheiro']:.2f}".replace(".", ","), base, now)
            if p["category"] == "clube" and a.get("club_special"):
                _add(con, p, "clube", f"Promoção de clube: {p['program'] or ''}", base, now)
        if a.get("changes") and p["id"] in changed:
            for c in changed[p["id"]]:
                old = fmt_dt(c["old"]) if c["field"] == "end_at" else c["old"]
                new = fmt_dt(c["new"]) if c["field"] == "end_at" else c["new"]
                _add(con, p, "mudanca", f"Mudança em {promo_route(p)}: {FIELD_LABEL[c['field']]} {old} → {new}", base, now)
        if p["end_at"] and p["status"].startswith("ativa"):
            end = datetime.fromisoformat(p["end_at"])
            if now <= end <= now + timedelta(hours=a["ending_hours"]):
                _add(con, p, "encerrando", f"Encerra {fmt_dt(p['end_at'])}: {p['title']}", base, now)


# ------------------------------------------------------------------ canais
def channels_status() -> dict:
    return {
        "telegram": bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
        "email": bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("EMAIL_TO")),
        "whatsapp": bool(os.environ.get("WHATSAPP_TOKEN") and os.environ.get("WHATSAPP_PHONE_ID") and os.environ.get("WHATSAPP_TO")),
        "dashboard": True,
    }


def _post_json(url: str, payload: dict, headers: dict | None = None) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def send_telegram(html_text: str) -> None:
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    for i in range(0, len(html_text), 3900):
        _post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                   {"chat_id": chat, "text": html_text[i:i + 3900], "parse_mode": "HTML", "disable_web_page_preview": True})


def send_email(subject: str, html_body: str) -> None:
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))
    user, pwd = os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", "")
    to = [x.strip() for x in os.environ["EMAIL_TO"].split(",")]
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("EMAIL_FROM", user), ", ".join(to)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, pwd); s.sendmail(msg["From"], to, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx); s.login(user, pwd); s.sendmail(msg["From"], to, msg.as_string())


def send_whatsapp(text: str) -> None:
    """WhatsApp Business Cloud API (Meta). Mensagens iniciadas pela empresa fora da janela de
    24h exigem template aprovado (WHATSAPP_TEMPLATE); senão envia texto simples."""
    url = f"https://graph.facebook.com/v21.0/{os.environ['WHATSAPP_PHONE_ID']}/messages"
    hdr = {"Authorization": f"Bearer {os.environ['WHATSAPP_TOKEN']}"}
    tpl = os.environ.get("WHATSAPP_TEMPLATE")
    if tpl:
        payload = {"messaging_product": "whatsapp", "to": os.environ["WHATSAPP_TO"], "type": "template",
                   "template": {"name": tpl, "language": {"code": "pt_BR"},
                                "components": [{"type": "body", "parameters": [{"type": "text", "text": text[:1000]}]}]}}
    else:
        payload = {"messaging_product": "whatsapp", "to": os.environ["WHATSAPP_TO"], "type": "text",
                   "text": {"body": text[:4000], "preview_url": False}}
    _post_json(url, payload, hdr)


def broadcast(subject: str, html_text: str, plain: str, log: list[str]) -> list[str]:
    sent = []
    st = channels_status()
    for name, fn in (("telegram", lambda: send_telegram(html_text)),
                     ("email", lambda: send_email(subject, html_text.replace("\n", "<br>"))),
                     ("whatsapp", lambda: send_whatsapp(plain))):
        if not st[name]:
            continue
        try:
            fn()
            sent.append(name)
        except Exception as e:
            log.append(f"falha ao enviar por {name}: {type(e).__name__}: {e}"[:300])
    return sent


def dispatch(con, log: list[str], suppress: bool = False) -> int:
    pending = rows(con, "SELECT * FROM alerts WHERE sent='' ORDER BY id")
    if not pending:
        return 0
    if suppress:
        con.execute("UPDATE alerts SET sent='carga inicial (não enviado)' WHERE sent=''")
        return 0
    lines_html, lines_txt = [], []
    for al in pending[:25]:
        lines_html.append(f"• <b>{escape(al['title'])}</b>\n  {escape(al['message'])}\n  {escape(al['url'] or '')}")
        lines_txt.append(f"• {al['title']}\n  {al['message']}\n  {al['url'] or ''}")
    extra = f"\n… e mais {len(pending) - 25} alertas no dashboard." if len(pending) > 25 else ""
    html_text = "🔔 <b>Pontos e Milhas — novos alertas</b>\n\n" + "\n\n".join(lines_html) + extra
    plain = "Pontos e Milhas — novos alertas\n\n" + "\n\n".join(lines_txt) + extra
    sent = broadcast(f"[Milhas] {len(pending)} novo(s) alerta(s)", html_text, plain, log)
    label = ",".join(sent) if sent else "dashboard"
    con.execute(f"UPDATE alerts SET sent=? WHERE id IN ({','.join(str(a['id']) for a in pending)})", (label,))
    return len(pending)
