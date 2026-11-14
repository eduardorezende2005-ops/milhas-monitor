"""Ciclo completo de monitoramento (executado a cada 30 min pelo agendador)."""
from __future__ import annotations

import hashlib
import json
import re
import traceback
from datetime import datetime, timedelta
from urllib.parse import urlparse

from . import discover, notify, reports
from .config import DB_PATH, load_settings, now as clock
from .db import connect, get_state, rows, set_state
from .extract import extract
from .http import Fetcher
from .promos import refresh_statuses, upsert
from .sources import OFFICIAL_DOMAINS, SOURCES


def sync_sources(con) -> None:
    for s in SOURCES:
        con.execute("""INSERT INTO sources(id,name,url,tier,access_notes,needs_login) VALUES(?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url, tier=excluded.tier,
                       access_notes=excluded.access_notes,
                       needs_login=COALESCE(sources.needs_login, excluded.needs_login)""",
                    (s["id"], s["name"], s["url"], s["tier"], s["access"],
                     None if s["needs_login"] is None else int(s["needs_login"])))


# ------------------------------------------------------------------ coleta por fonte
def discover_source(f: Fetcher, src: dict, row: dict, limit: int) -> tuple[list[discover.Entry], str, str, str | None]:
    """Tenta os métodos em ordem. Retorna (entradas, método, url, erro)."""
    errors = []
    tried = []
    if row.get("method") and row.get("method_url"):
        tried.append((row["method"], row["method_url"]))
    tried += [("rss", u) for u in src["feeds"]]
    if src.get("wp_api"):
        base = f"{urlparse(src['url']).scheme}://{urlparse(src['url']).netloc}"
        tried.append(("wp-json", base + "/wp-json/wp/v2/posts?per_page=20&_fields=date,link,title,excerpt,content"))
    tried += [("html", u) for u in src["pages"]]
    seen = set()
    for method, url in tried:
        if (method, url) in seen:
            continue
        seen.add((method, url))
        conditional = method == "rss" and row.get("method_url") == url
        r = f.get(url, row.get("etag") if conditional else None, row.get("last_modified") if conditional else None)
        if r.not_modified:
            return [], method, url, None
        if not r.ok:
            errors.append(f"{method}: {r.error}")
            if r.status == 429:
                break
            continue
        try:
            if method == "rss":
                entries = discover.parse_feed(r.text)
            elif method == "wp-json":
                entries = discover.parse_wp_json(r.text)
            else:
                art = discover.parse_article(r.text, r.url)
                # descoberta automática de feed declarado na página
                for feed in art.alternate_feeds[:2]:
                    if "comments" in feed:
                        continue
                    fr = f.get(feed)
                    if fr.ok and fr.text:
                        try:
                            fe = discover.parse_feed(fr.text)
                            if fe:
                                row["etag"], row["last_modified"] = fr.headers.get("etag"), fr.headers.get("last-modified")
                                return fe[:limit], "rss", feed, None
                        except Exception:
                            pass
                entries = discover.parse_listing(r.text, r.url, limit, strict=src["tier"] == 3)
                if not entries:
                    looks_js = len(discover.html_to_text(r.text)) < 500
                    errors.append("html: página sem publicações legíveis" + (" (conteúdo gerado por JavaScript)" if looks_js else ""))
                    continue
        except Exception as e:
            errors.append(f"{method}: conteúdo inválido ({type(e).__name__})")
            continue
        if entries:
            if method == "rss":
                row["etag"], row["last_modified"] = r.headers.get("etag"), r.headers.get("last-modified")
            return entries[:limit], method, url, None
        errors.append(f"{method}: vazio")
    return [], "", "", " | ".join(errors)[:500] or "nenhum método disponível"


# ------------------------------------------------------------------ processamento
def process_entry(con, src: dict, entry: discover.Entry, f: Fetcher | None, now: datetime, fetch_budget: list[int],
                  stats: dict) -> None:
    url = entry.url.split("#")[0]
    if not url:
        return
    existing = con.execute("SELECT id, content_hash, promo_id FROM items WHERE url=?", (url,)).fetchone()
    if existing is not None and not entry.full:
        # Já conhecida e a listagem só traz o título: não reprocessa com menos informação.
        # Exceção útil: a listagem passou a marcar a promoção como expirada/encerrada.
        if existing["promo_id"] and re.search(r"⛔|expirad|encerrad", entry.title or "", re.I):
            con.execute("UPDATE promos SET expired_flag=1 WHERE id=?", (existing["promo_id"],))
        return
    text, links, published, title = entry.text, entry.links, entry.published, entry.title
    if not entry.full and f is not None and fetch_budget[0] > 0 and existing is None:
        fetch_budget[0] -= 1
        r = f.get(url)
        if r.ok and r.text:
            art = discover.parse_article(r.text, r.url)
            listing_label = title
            text = art.text[:20000] or text
            if listing_label and listing_label != art.title and re.search(r"⛔|expirad|encerrad", listing_label, re.I):
                text = listing_label + "\n" + text   # selos da listagem (ex.: "⛔ EXPIRADA") viram contexto
            links = art.links or links
            published = published or art.published
            title = art.title or title
    if len(text) < 200:  # pouco texto: o slug da URL ajuda a identificar programas/lojas
        text = (text + "\n" + urlparse(url).path.strip("/").split("/")[-1].replace("-", " ")).strip()
    published = published or now
    h = hashlib.sha1(f"{title}\n{text}".encode()).hexdigest()
    if existing and existing["content_hash"] == h:
        return
    e = extract(title, text, published, links, OFFICIAL_DOMAINS)
    if existing:
        con.execute("UPDATE items SET title=?, text=?, content_hash=?, fetched_at=?, is_promo=?, reason=?, extracted=? WHERE id=?",
                    (title, text[:20000], h, now.isoformat(), int(e.is_promo), e.reason, json.dumps(e.to_dict(), ensure_ascii=False), existing["id"]))
        item_id = existing["id"]
    else:
        cur = con.execute("INSERT INTO items(source_id,url,title,published_at,fetched_at,content_hash,text,is_promo,reason,extracted) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (src["id"], url, title, published.isoformat(), now.isoformat(), h, text[:20000], int(e.is_promo), e.reason,
                           json.dumps(e.to_dict(), ensure_ascii=False)))
        item_id = cur.lastrowid
        stats["items_new"] += 1
    if not e.is_promo:
        return
    pid, kind, changes = upsert(con, e, {"title": title, "url": url, "published": published, "source_id": src["id"],
                                         "item_id": item_id, "promo_id": existing["promo_id"] if existing else None},
                                src["tier"], now)
    con.execute("UPDATE items SET promo_id=? WHERE id=?", (pid, item_id))
    con.execute("UPDATE sources SET promos_found=promos_found+1 WHERE id=?", (src["id"],))
    if kind == "nova":
        stats["new_ids"].add(pid)
    elif kind == "atualizada":
        stats["updated"] += 1
    if changes:
        stats["changes"].setdefault(pid, []).extend(changes)


def verify_official(con, f: Fetcher, now: datetime, budget: int, log: list[str]) -> int:
    """Abre o link oficial (regulamento) de promoções ativas e confirma a data de encerramento."""
    from .dates import extract_window
    done = 0
    cands = rows(con, """SELECT * FROM promos WHERE official_url IS NOT NULL AND status IN ('ativa_pendente','indeterminada')
                        AND (official_verified_at IS NULL OR official_verified_at < ?) ORDER BY end_at""",
                 (now - timedelta(hours=6)).isoformat())
    for p in cands[:budget]:
        r = f.get(p["official_url"])
        done += 1
        if not r.ok:
            log.append(f"regulamento inacessível (promo {p['id']}): {r.error}")
            continue
        art = discover.parse_article(r.text, r.url)
        w = extract_window(art.title, art.text, datetime.fromisoformat(p["published_at"]) if p["published_at"] else now)
        if w.end and p["end_at"] and abs((w.end - datetime.fromisoformat(p["end_at"])).total_seconds()) <= 36 * 3600:
            con.execute("UPDATE promos SET official_verified_at=? WHERE id=?", (now.isoformat(), p["id"]))
        elif w.end and not p["end_at"]:
            con.execute("UPDATE promos SET end_at=?, end_source='regulamento oficial', official_verified_at=? WHERE id=?",
                        (w.end.isoformat(), now.isoformat(), p["id"]))
    return done


# ------------------------------------------------------------------ ciclo
def run(trigger: str = "agendado", only: list[str] | None = None, fetcher: Fetcher | None = None,
        export: bool = True) -> dict:
    settings = load_settings()
    m = settings["monitor"]
    now = clock()
    con = connect(DB_PATH)
    sync_sources(con)
    first_run = con.execute("SELECT COUNT(*) FROM runs WHERE status!='falhou'").fetchone()[0] == 0
    run_id = con.execute("INSERT INTO runs(started_at,trigger,status) VALUES(?,?,?)", (now.isoformat(), trigger, "rodando")).lastrowid
    con.commit()
    f = fetcher or Fetcher(m["per_host_interval_seconds"], m["timeout_seconds"])
    log: list[str] = []
    stats = {"items_new": 0, "updated": 0, "new_ids": set(), "changes": {}}
    ok = err = skipped = 0
    for src in SOURCES:
        if only and src["id"] not in only:
            continue
        row = dict(con.execute("SELECT * FROM sources WHERE id=?", (src["id"],)).fetchone())
        if row.get("cooldown_until") and row["cooldown_until"] > now.isoformat() and trigger != "manual":
            skipped += 1
            continue
        try:
            entries, method, murl, error = discover_source(f, src, row, m["max_items_per_source"])
        except Exception as e:
            entries, method, murl, error = [], "", "", f"erro interno: {type(e).__name__}: {e}"
        ts = clock().isoformat()
        if error:
            err += 1
            ce = (row["consecutive_errors"] or 0) + 1
            cooldown = (now + timedelta(minutes=m["cooldown_minutes"])).isoformat() if ce >= m["cooldown_after_errors"] else None
            status = "indisponível" if ce >= 3 else "com falhas"
            if src.get("needs_login"):
                status, error = "indisponível", "conteúdo exclusivo para assinantes/membros — não coletado (" + error + ")"
            con.execute("""UPDATE sources SET last_check=?, status=?, consecutive_errors=?, total_errors=total_errors+1,
                           total_checks=total_checks+1, last_error=?, cooldown_until=? WHERE id=?""",
                        (ts, status, ce, error, cooldown, src["id"]))
            con.execute("INSERT INTO errors(run_id,source_id,at,message) VALUES(?,?,?,?)", (run_id, src["id"], ts, error))
            log.append(f"{src['id']}: {error}")
            con.commit()
            continue
        ok += 1
        budget = [m["max_article_fetches_per_source"]]
        for entry in entries:
            try:
                process_entry(con, src, entry, f, now, budget, stats)
            except Exception as e:
                log.append(f"{src['id']}: falha ao processar {entry.url}: {type(e).__name__}: {e}")
        status = "automático" if method in ("rss", "wp-json") else "parcial"
        con.execute("""UPDATE sources SET last_check=?, last_ok=?, status=?, method=?, method_url=?, consecutive_errors=0,
                       total_checks=total_checks+1, items_found=items_found+?, last_error=NULL, cooldown_until=NULL,
                       etag=?, last_modified=? WHERE id=?""",
                    (ts, ts, status, method, murl, len(entries), row.get("etag"), row.get("last_modified"), src["id"]))
        con.commit()
    try:
        verify_official(con, f, now, m["official_verify_per_run"], log)
    except Exception as e:
        log.append(f"verificação oficial: {e}")
    return finish(con, settings, now, run_id, stats, log, ok, err, skipped, f.requests_made, first_run, export, trigger)


def finish(con, settings, now, run_id, stats, log, ok, err, skipped, requests, first_run, export, trigger="agendado"):
    m = settings["monitor"]
    refresh_statuses(con, now, m["undated_promo_max_age_days"])
    notify.evaluate(con, settings, now, stats["new_ids"], stats["changes"])
    sent = notify.dispatch(con, log, suppress=first_run)
    reps = reports.maybe_send(con, settings, now, log) if not first_run else []
    status = "ok" if err == 0 else ("parcial" if ok else "falhou")
    con.execute("""UPDATE runs SET finished_at=?, status=?, sources_ok=?, sources_error=?, sources_skipped=?, items_new=?,
                   promos_new=?, promos_updated=?, requests=?, log=? WHERE id=?""",
                (clock().isoformat(), status, ok, err, skipped, stats["items_new"], len(stats["new_ids"]), stats["updated"],
                 requests, "\n".join(log)[-8000:], run_id))
    set_state(con, "last_run", now.isoformat())
    con.commit()
    if export:
        from .site import export_site
        export_site(con, settings)
    summary = {"run_id": run_id, "status": status, "sources_ok": ok, "sources_error": err, "skipped": skipped,
               "items_new": stats["items_new"], "promos_new": len(stats["new_ids"]), "promos_updated": stats["updated"],
               "alerts_dispatched": sent, "reports": reps, "first_run": first_run}
    con.close()
    return summary


def ingest_seed(path: str) -> dict:
    """Carrega publicações reais salvas em JSON (teste/bootstrap) passando pelo mesmo pipeline."""
    settings = load_settings()
    now = clock()
    con = connect(DB_PATH)
    sync_sources(con)
    first_run = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    run_id = con.execute("INSERT INTO runs(started_at,trigger,status) VALUES(?,?,?)", (now.isoformat(), "carga de teste", "rodando")).lastrowid
    stats = {"items_new": 0, "updated": 0, "new_ids": set(), "changes": {}}
    from .sources import by_id
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for it in data:
        src = by_id(it["source"])
        e = discover.Entry(it["url"], it["title"], datetime.fromisoformat(it["published"]), it.get("text", ""), it.get("links", []), full=True)
        process_entry(con, src, e, None, now, [0], stats)
    return finish(con, settings, now, run_id, stats, [f"carga de {len(data)} publicações reais de {path}"], 0, 0, 0, 0, first_run, True, "carga de teste")


def reprocess() -> dict:
    """Reextrai todas as publicações já guardadas (sem acessar a rede). Útil após ajustar regras."""
    settings = load_settings()
    now = clock()
    con = connect(DB_PATH)
    for t in ("promos", "promo_sources", "promo_changes", "alerts"):
        con.execute(f"DELETE FROM {t}")
    con.execute("UPDATE sources SET promos_found=0")
    con.execute("UPDATE items SET content_hash=NULL, promo_id=NULL")
    run_id = con.execute("INSERT INTO runs(started_at,trigger,status) VALUES(?,?,?)", (now.isoformat(), "reprocessamento", "rodando")).lastrowid
    stats = {"items_new": 0, "updated": 0, "new_ids": set(), "changes": {}}
    from .sources import by_id
    items = rows(con, "SELECT * FROM items ORDER BY published_at")
    for it in items:
        src = by_id(it["source_id"])
        e = discover.Entry(it["url"], it["title"], datetime.fromisoformat(it["published_at"]), it["text"] or "", [], full=True)
        process_entry(con, src, e, None, now, [0], stats)
    stats["new_ids"] = set()  # reprocessamento não gera alertas de "nova"
    return finish(con, settings, now, run_id, stats, [f"reprocessadas {len(items)} publicações"], 0, 0, 0, 0, True, True, "reprocessamento")
