"""Descoberta de publicações: RSS/Atom, API REST do WordPress e páginas HTML.
Também converte HTML de artigo em texto limpo + metadados."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .config import TZ


@dataclass
class Entry:
    url: str
    title: str
    published: datetime | None = None
    text: str = ""              # texto completo quando o feed traz content:encoded
    links: list[str] = field(default_factory=list)
    full: bool = False          # True se `text` já é o artigo completo


# ---------------------------------------------------------------- HTML → texto
class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "iframe"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "ul", "ol", "td", "th"}

    VOID = {"br", "img", "meta", "link", "input", "hr", "source", "area", "base", "col", "embed", "wbr", "track", "param"}
    CONTENT_RX = re.compile(r"entry-content|post-content|article-content|article-body|post-body|single-content|td-post-content|content-single|materia|post__content|article__content|prose", re.I)

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.main_depth: int | None = None
        self.main_parts: list[str] = []
        self.main_done = False
        self.parts: list[str] = []
        self.article_parts: list[str] = []
        self.skip_depth = 0
        self.article_depth = 0
        self.links: list[str] = []
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self.alternate_feeds: list[str] = []
        self.anchors: list[tuple[str, str]] = []
        self._a_href: str | None = None
        self._a_text: list[str] = []
        self.times: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in self.VOID:
            self.depth += 1
            if (self.main_depth is None and not self.main_done and tag in ("div", "section", "main", "article")
                    and self.CONTENT_RX.search(a.get("class") or "")):
                self.main_depth = self.depth
        if tag in self.SKIP:
            self.skip_depth += 1
        if tag == "article":
            self.article_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop")
            if key and a.get("content"):
                self.meta[key.lower()] = a["content"]
        if tag == "link" and (a.get("rel") or "").lower() == "alternate" and "xml" in (a.get("type") or ""):
            if a.get("href"):
                self.alternate_feeds.append(a["href"])
        if tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
            self._a_href = a["href"]
            self._a_text = []
        if tag in self.BLOCK:
            self._push("\n")

    def handle_endtag(self, tag):
        if tag not in self.VOID:
            if self.main_depth is not None and self.depth == self.main_depth:
                self.main_depth = None
                if len("".join(self.main_parts)) > 300:
                    self.main_done = True
                else:
                    self.main_parts = []
            self.depth = max(0, self.depth - 1)
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        if tag == "article" and self.article_depth:
            self.article_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._a_href is not None:
            self.anchors.append((self._a_href, " ".join("".join(self._a_text).split())))
            self._a_href = None
        if tag in self.BLOCK:
            self._push("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._a_href is not None:
            self._a_text.append(data)
        if not self.skip_depth:
            self._push(data)

    def _push(self, s):
        self.parts.append(s)
        if self.main_depth is not None and not self.main_done:
            self.main_parts.append(s)
        if self.article_depth:
            self.article_parts.append(s)


def _clean(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    return _clean("".join(p.parts))


@dataclass
class Article:
    url: str
    title: str
    text: str
    published: datetime | None
    links: list[str]
    alternate_feeds: list[str]
    anchors: list[tuple[str, str]]


def parse_article(html: str, url: str) -> Article:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    main = "".join(p.main_parts)
    art = "".join(p.article_parts)
    body = main if len(main) > 300 else art if len(art) > 400 else "".join(p.parts)
    title = p.meta.get("og:title") or p.title
    title = re.sub(r"\s*[|\-–·]\s*[^|\-–·]{2,40}$", "", unescape(title).strip())  # tira " | Nome do site"
    pub = None
    for key in ("article:published_time", "datepublished", "og:updated_time"):
        if p.meta.get(key):
            pub = parse_iso(p.meta[key])
            if pub:
                break
    if not pub and p.times:
        pub = parse_iso(p.times[0])
    links = [urljoin(url, h) for h in p.links if not h.startswith(("#", "mailto:", "javascript:"))]
    anchors = [(urljoin(url, h), t) for h, t in p.anchors if not h.startswith(("#", "mailto:", "javascript:"))]
    return Article(url, title.strip(), _clean(body), pub, links,
                   [urljoin(url, f) for f in p.alternate_feeds], anchors)


def parse_iso(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=TZ)).astimezone(TZ)
    except Exception:
        return None


# ---------------------------------------------------------------- RSS / Atom
_NS = {"content": "http://purl.org/rss/1.0/modules/content/", "atom": "http://www.w3.org/2005/Atom",
       "dc": "http://purl.org/dc/elements/1.1/"}


def parse_feed(xml_text: str) -> list[Entry]:
    xml_text = xml_text.lstrip("﻿ \n\r\t")
    if not xml_text.startswith("<"):
        raise ValueError("resposta não é XML")
    root = ET.fromstring(xml_text.encode("utf-8"))
    entries: list[Entry] = []
    items = root.findall(".//item")
    if items:  # RSS 2.0
        for it in items:
            title = unescape((it.findtext("title") or "").strip())
            link = (it.findtext("link") or "").strip()
            pub = None
            raw_date = it.findtext("pubDate") or it.findtext("dc:date", namespaces=_NS)
            if raw_date:
                try:
                    pub = parsedate_to_datetime(raw_date).astimezone(TZ)
                except Exception:
                    pub = parse_iso(raw_date)
            content = it.findtext("content:encoded", namespaces=_NS) or ""
            desc = it.findtext("description") or ""
            html = content or desc
            entries.append(Entry(link, title, pub, html_to_text(html), _links(html, link), full=len(content) > 800))
        return entries
    for it in root.findall("atom:entry", _NS):  # Atom
        title = unescape((it.findtext("atom:title", namespaces=_NS) or "").strip())
        link_el = it.find("atom:link[@rel='alternate']", _NS)
        if link_el is None:
            link_el = it.find("atom:link", _NS)
        link = link_el.get("href") if link_el is not None else ""
        pub = parse_iso(it.findtext("atom:published", namespaces=_NS) or it.findtext("atom:updated", namespaces=_NS) or "")
        html = it.findtext("atom:content", namespaces=_NS) or it.findtext("atom:summary", namespaces=_NS) or ""
        entries.append(Entry(link, title, pub, html_to_text(html), _links(html, link), full=len(html) > 800))
    return entries


def _links(html: str, base: str) -> list[str]:
    return [urljoin(base, unescape(h)) for h in re.findall(r'href=["\']([^"\']+)["\']', html or "")]


# ---------------------------------------------------------------- WordPress REST
def parse_wp_json(text: str) -> list[Entry]:
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("wp-json não retornou lista")
    out = []
    for p in data:
        html = (p.get("content") or {}).get("rendered", "") or (p.get("excerpt") or {}).get("rendered", "")
        out.append(Entry(
            p.get("link", ""), unescape(html_to_text((p.get("title") or {}).get("rendered", ""))),
            parse_iso(p.get("date", "") or ""), html_to_text(html), _links(html, p.get("link", "")),
            full=len(html) > 800,
        ))
    return out


# ---------------------------------------------------------------- HTML listing
_RELEVANT = re.compile(r"pont|milha|bonus|bônus|transfer|livelo|esfera|smiles|latam|azul|clube|promo|cartao|cartão|avios|accor|compra", re.I)
_SKIP_PATH = re.compile(r"/(tag|categoria|categorias|category|author|autor|page|wp-|login|conta|cadastro|busca|search|sobre|contato|politica|privacidade|termos)(/|$)", re.I)


_GENERIC = re.compile(r"^(destaque|leia mais|ler mais|saiba mais|ver mais|confira|📊.*|ler nossa análise.*|continue lendo|veja)$", re.I)


def parse_listing(html: str, base_url: str, limit: int = 30, strict: bool = False) -> list[Entry]:
    """strict=True (sites oficiais): só caminhos com cara de campanha, nunca links de menu."""
    art = parse_article(html, base_url)
    host = urlparse(base_url).netloc.replace("www.", "")
    best: dict[str, str] = {}
    order: list[str] = []
    for href, text in art.anchors:
        u = urlparse(href)
        if u.netloc.replace("www.", "") != host or _SKIP_PATH.search(u.path):
            continue
        slug = u.path.strip("/").split("/")[-1] if u.path.strip("/") else ""
        if len(slug) < 20 or "-" not in slug:
            continue
        if strict and not re.search(r"promo|campanha|oferta|bonus|bônus|black|friday|regulamento-(?!do-programa)", u.path, re.I):
            continue
        clean = href.split("#")[0]
        text = "" if _GENERIC.match(text.strip()) else text
        if clean not in best:
            order.append(clean)
            best[clean] = text
        elif len(text) > len(best[clean]):
            best[clean] = text
    out = []
    for url in order:
        text = best[url]
        slug = urlparse(url).path.strip("/").split("/")[-1]
        if not (_RELEVANT.search(slug) or _RELEVANT.search(text)):
            continue
        out.append(Entry(url, text if len(text) > 15 else slug.replace("-", " ")))
        if len(out) >= limit:
            break
    return out
