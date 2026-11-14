"""Cliente HTTP educado: robots.txt, intervalo mínimo por site, GET condicional,
retentativas com backoff e respeito a 429/Retry-After. Só biblioteca padrão."""
from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.request
import urllib.robotparser
import zlib
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .config import USER_AGENT


@dataclass
class Response:
    url: str
    status: int
    text: str = ""
    headers: dict = field(default_factory=dict)
    not_modified: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and (200 <= self.status < 300 or self.not_modified)


class Fetcher:
    def __init__(self, interval: float = 3.0, timeout: int = 20, retries: int = 2):
        self.interval = interval
        self.timeout = timeout
        self.retries = retries
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests_made = 0

    # ---------- robots.txt ----------
    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            r = self._raw_get(base + "/robots.txt", {}, respect_interval=True)
            if r.status == 200 and r.text:
                rp.parse(r.text.splitlines())
                self._robots[base] = rp
            else:
                self._robots[base] = None  # sem robots.txt legível → permitido
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    # ---------- GET ----------
    def get(self, url: str, etag: str | None = None, last_modified: str | None = None) -> Response:
        if not self.allowed(url):
            return Response(url, 0, error="bloqueado pelo robots.txt (não coletado)")
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        attempt = 0
        while True:
            r = self._raw_get(url, headers)
            transient = r.status in (0, 500, 502, 503, 504) and r.error and "robots" not in r.error
            if r.status == 429:
                wait = r.headers.get("retry-after", "")
                r.error = f"HTTP 429 (limite de requisições){' — retry-after ' + wait if wait else ''}"
                return r
            if transient and attempt < self.retries:
                attempt += 1
                time.sleep(2 ** attempt)
                continue
            return r

    def _wait_turn(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            delta = time.monotonic() - last
            if delta < self.interval:
                time.sleep(self.interval - delta)
        self._last_hit[host] = time.monotonic()

    def _raw_get(self, url: str, extra: dict, respect_interval: bool = True) -> Response:
        host = urlparse(url).netloc
        if respect_interval:
            self._wait_turn(host)
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml,application/rss+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            **extra,
        })
        self.requests_made += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(5_000_000)
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip":
                    raw = gzip.decompress(raw)
                elif enc == "deflate":
                    raw = zlib.decompress(raw)
                charset = resp.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return Response(resp.geturl(), resp.status, text, hdrs)
        except urllib.error.HTTPError as e:
            hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
            if e.code == 304:
                return Response(url, 304, headers=hdrs, not_modified=True)
            msg = {401: "exige autenticação", 403: "acesso negado (403) — possível proteção anti-bot",
                   404: "não encontrado (404)"}.get(e.code, f"HTTP {e.code}")
            return Response(url, e.code, headers=hdrs, error=msg)
        except Exception as e:  # timeout, DNS, TLS…
            return Response(url, 0, error=f"{type(e).__name__}: {e}"[:200])
