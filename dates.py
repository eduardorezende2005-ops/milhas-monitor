"""Interpretação de datas em português ("até 13 de novembro", "de 09 a 13/11/2026",
"até domingo (15)", "válida apenas hoje"…). Datas relativas usam a data de publicação."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import TZ

MONTHS = {
    "janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "março": 3, "marco": 3, "mar": 3, "abril": 4, "abr": 4,
    "maio": 5, "mai": 5, "junho": 6, "jun": 6, "julho": 7, "jul": 7, "agosto": 8, "ago": 8,
    "setembro": 9, "set": 9, "outubro": 10, "out": 10, "novembro": 11, "nov": 11, "dezembro": 12, "dez": 12,
}
WEEKDAYS = {"segunda": 0, "terça": 1, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sábado": 5, "sabado": 5, "domingo": 6}
_M = "|".join(sorted(MONTHS, key=len, reverse=True))
_TIME = r"(?:\s*,?\s*(?:às|as)\s*(\d{1,2})h(\d{2})?(?:min)?)?"

DATE_RE = re.compile(
    r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/])" + _TIME
    + r"|(?<!\d)(\d{1,2})º?\s+(?:de\s+)?(" + _M + r")\.?(?:\s+(?:de\s+)?(\d{4}))?\b" + _TIME,
    re.I,
)
BARE_RANGE_RE = re.compile(
    r"\b(?:de|entre(?:\s+os\s+dias)?|dias)\s+(\d{1,2})\s+(?:a|e|até)\s+(\d{1,2})\s+de\s+(" + _M + r")(?:\s+de\s+(\d{4}))?",
    re.I,
)
CREDIT_CTX = re.compile(r"credit|crédito|validade|válid[oa]s?\s+por\s+\d|expira|prazo de crédito|serão válidas", re.I)
ONE_DAY_CTX = re.compile(r"(?:durante o|(?:somente|apenas|só|exclusivamente)(?:\s+no)?|realizadas?\s+(?:no|em)|hoje,?)\s+(?:dia\s+)?$", re.I)
END_CTX = re.compile(r"(?:até|ate|termina|encerra|vence|prorrogad[oa]\s+(?:para|até)|fim)\s*(?:o\s+)?(?:às\s+\d{1,2}h\d{0,2}\s+)?(?:de\s+)?(?:hoje,?\s+)?(?:dia\s+)?(?:\w+-feira,?\s+)?$", re.I)
START_CTX = re.compile(r"(?:\bde|\bdesde|a partir de|início em|começa(?:ndo)? em|entre(?: os dias)?|período de|vigência de?)\s*(?:o\s+)?(?:dia\s+)?$", re.I)
RANGE_SEP = re.compile(r"^\s*(?:,?\s*(?:às|as)\s*\d{1,2}h\d{0,2}(?:min)?\s*,?)?\s*(?:a|até|e|-|–)\s*(?:as\s+\d{1,2}h\d{0,2}\s+de\s+)?$", re.I)


@dataclass
class Window:
    start: datetime | None = None
    end: datetime | None = None
    credit_deadline: datetime | None = None
    end_source: str = ""  # "título" | "texto" | ""


def _mk(day: int, month: int, year: int | None, ref: datetime, hour: int | None = None, minute: int | None = None,
        end: bool = True) -> datetime | None:
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    y = year if year else ref.year
    if y < 100:
        y += 2000
    try:
        dt = datetime(y, month, day, tzinfo=TZ)
    except ValueError:
        return None
    if not year:
        if dt < ref - timedelta(days=150):
            dt = dt.replace(year=dt.year + 1)
        elif dt > ref + timedelta(days=215):
            dt = dt.replace(year=dt.year - 1)
    if hour is not None:
        dt = dt.replace(hour=min(hour, 23), minute=minute or 0)
    elif end:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


def _token_dt(m: re.Match, ref: datetime, end: bool) -> datetime | None:
    g = m.groups()
    if g[0]:
        return _mk(int(g[0]), int(g[1]), int(g[2]) if g[2] else None, ref,
                   int(g[3]) if g[3] else None, int(g[4]) if g[4] else None, end)
    return _mk(int(g[5]), MONTHS[g[6].lower()], int(g[7]) if g[7] else None, ref,
               int(g[8]) if g[8] else None, int(g[9]) if g[9] else None, end)


def _next_day_number(day: int, ref: datetime, weekday: int | None = None) -> datetime | None:
    """Data com esse dia do mês mais próxima de ref (passado ou futuro). Se o dia da semana
    foi citado ("sexta-feira (13)"), ele precisa bater — evita jogar a data para o mês seguinte
    quando a página não informa a data de publicação."""
    best = None
    for add in range(-25, 40):
        d = ref + timedelta(days=add)
        if d.day == day and (weekday is None or d.weekday() == weekday):
            if best is None or abs(add) < abs(best[0]) or (abs(add) == abs(best[0]) and add > 0):
                best = (add, d)
    if best is None:
        return None
    return best[1].replace(hour=23, minute=59, second=59, microsecond=0)


def _relative_end(text: str, ref: datetime) -> datetime | None:
    t = text.lower()
    eod = ref.replace(hour=23, minute=59, second=59, microsecond=0)
    m = re.search(r"até\s+(?:a\s+|o\s+)?(segunda|terça|terca|quarta|quinta|sexta|sábado|sabado|domingo)(?:-feira)?,?\s*(?:\(|dia\s+)?(\d{1,2})?\)?", t)
    if m:
        if m.group(2):
            return _next_day_number(int(m.group(2)), ref, WEEKDAYS[m.group(1)])
        wd = WEEKDAYS[m.group(1)]
        return eod + timedelta(days=(wd - ref.weekday()) % 7)
    m = re.search(r"até\s+amanhã(?:\s*\((?:dia\s+)?(\d{1,2})\))?", t)
    if m:
        return eod + timedelta(days=1)
    m = re.search(r"até\s+(?:o\s+)?dia\s+(\d{1,2})\b(?!\s*(?:/|de\s+(?:" + _M + ")))", t)
    if m:
        return _next_day_number(int(m.group(1)), ref)
    if re.search(r"(?:apenas|somente|só|valid[ao]\s+(?:apenas\s+|somente\s+)?|vale\s+(?:apenas\s+|somente\s+)?)hoje|até\s+hoje|só até (?:a )?meia-noite|últimas horas|termina hoje|acaba hoje|encerra hoje", t):
        return eod
    return None


EXPLICIT_RE = re.compile(r"(?:válid[oa]|vigente|vigência|oferta|promoção|campanha|prazo)[^.\n]{0,40}?(?:até|:)\s*(?:o\s+dia\s+)?(?:(\d{4})-(\d{2})-(\d{2})|(\d{1,2})/(\d{1,2})/(\d{4})|(\d{1,2})\s+de\s+(" + _M + r")\.?(?:\s+de\s+(\d{4}))?)", re.I)


def _explicit_end(chunk: str, ref: datetime) -> datetime | None:
    m = EXPLICIT_RE.search(chunk)
    if not m:
        return None
    g = m.groups()
    if g[0]:
        return _mk(int(g[2]), int(g[1]), int(g[0]), ref)
    if g[3]:
        return _mk(int(g[3]), int(g[4]), int(g[5]), ref)
    return _mk(int(g[6]), MONTHS[g[7].lower()], int(g[8]) if g[8] else None, ref)


def extract_window(title: str, text: str, ref: datetime) -> Window:
    w = Window()
    exp = _explicit_end((text or "")[:3000], ref)
    for label, chunk in (("título", title or ""), ("texto", (text or "")[:4000])):
        start, end, credit = _scan(chunk, ref)
        if credit and not w.credit_deadline:
            w.credit_deadline = credit
        if start and not w.start:
            w.start = start
        if end and not w.end:
            w.end, w.end_source = end, label
        if not w.end:
            rel = _relative_end(chunk[:1500], ref)
            if rel:
                w.end, w.end_source = rel, label
    if exp and w.end_source != "título":
        if w.end and w.start and abs((w.end - exp).total_seconds()) > 36 * 3600:
            w.start = None  # o intervalo era de outra coisa (ex.: datas de viagem)
        w.end, w.end_source = exp, "texto"
    if w.start and w.end and w.start > w.end:
        w.start = None
    return w


def _scan(chunk: str, ref: datetime) -> tuple[datetime | None, datetime | None, datetime | None]:
    start = end = credit = None
    m = BARE_RANGE_RE.search(chunk)
    if m:
        mon = MONTHS[m.group(3).lower()]
        yr = int(m.group(4)) if m.group(4) else None
        s = _mk(int(m.group(1)), mon, yr, ref, end=False)
        e = _mk(int(m.group(2)), mon, yr, ref)
        if s and e and not CREDIT_CTX.search(chunk[max(0, m.start() - 50):m.start()]):
            start, end = s, e
    tokens = list(DATE_RE.finditer(chunk))
    for i, tok in enumerate(tokens):
        before = chunk[max(0, tok.start() - 60):tok.start()]
        if CREDIT_CTX.search(before[-45:]):
            if not credit:
                credit = _token_dt(tok, ref, True)
            continue
        prev = tokens[i - 1] if i else None
        if prev is not None and RANGE_SEP.match(chunk[prev.end():tok.start()]):
            s = _token_dt(prev, ref, False)
            e = _token_dt(tok, ref, True)
            if s and e and s.month == e.month and not prev.group(3) and not prev.group(8) and tok.group(3):
                s = s.replace(year=e.year)
            if s and e and s <= e and not end:
                start, end = s.replace(hour=s.hour if prev.group(4) or prev.group(9) else 0, minute=s.minute, second=0), e
            continue
        if END_CTX.search(before) and not end:
            end = _token_dt(tok, ref, True)
        elif ONE_DAY_CTX.search(before) and not end:
            end = _token_dt(tok, ref, True)
            start = start or (end.replace(hour=0, minute=0, second=0) if end else None)
        elif START_CTX.search(before) and not start:
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if nxt is None or not RANGE_SEP.match(chunk[tok.end():nxt.start()]):
                start = _token_dt(tok, ref, False)
    return start, end, credit
