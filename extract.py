"""Classificação e extração estruturada de promoções a partir de título + texto.

Regra de ouro: só preenche um campo quando o valor aparece no texto. Nada é estimado,
exceto o custo do milheiro calculado a partir de preço e desconto publicados, que fica
marcado como "calculado"."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .dates import extract_window

CATEGORIES = {
    "transferencia": "Transferência bonificada",
    "compra_bonificada": "Compra bonificada",
    "compra_pontos": "Compra de pontos/milhas",
    "clube": "Clube de fidelidade",
    "cartao": "Cartão de crédito",
    "resgate": "Resgate com milhas",
    "outra": "Outras promoções",
}

# programa canônico → (regex, tipo). tipo: "aereo"/"hotel" = destino típico; "banco"/"pontos" = origem típica
PROGRAMS: dict[str, tuple[str, str]] = {
    "Smiles": (r"\bsmiles\b", "aereo"),
    "LATAM Pass": (r"\blatam(?:\s*pass)?\b", "aereo"),
    "Azul Fidelidade": (r"\bazul(?:\s+fidelidade)?\b|\btudoazul\b", "aereo"),
    "TAP Miles&Go": (r"\btap\b|miles\s*&\s*go", "aereo"),
    "Iberia Plus": (r"\biberia\b", "aereo"),
    "Qatar Privilege Club": (r"\bqatar\b|privilege club", "aereo"),
    "British Airways": (r"\bbritish\b", "aereo"),
    "Mundo Avios": (r"mundo avios|\bavios\b", "aereo"),
    "ConnectMiles": (r"connectmiles|\bcopa\b", "aereo"),
    "Turkish Miles&Smiles": (r"\bturkish\b", "aereo"),
    "ALL Accor": (r"\ball accor\b|\ball\b(?= accor)|\baccor\b", "hotel"),
    "Livelo": (r"\blivelo\b", "pontos"),
    "Esfera": (r"\besfera\b", "pontos"),
    "Itaú": (r"\bitaú\b|\bitau\b|sempre presente", "banco"),
    "Credicard": (r"\bcredicard\b", "banco"),
    "Nubank": (r"\bnubank\b|ultravioleta|\bcroma\b", "banco"),
    "C6 Átomos": (r"\bc6\b|átomos", "banco"),
    "Inter Loop": (r"\binter\b(?! ?nacion)|\bloop\b", "banco"),
    "Bradesco": (r"\bbradesco\b", "banco"),
    "Banco do Brasil": (r"banco do brasil|\bbb\b", "banco"),
    "Caixa": (r"\bcaixa\b", "banco"),
    "BRB": (r"\bbrb\b|curtaí", "banco"),
    "Sicoob": (r"\bsicoob\b", "banco"),
    "Santander": (r"\bsantander\b", "banco"),
    "PicPay": (r"\bpicpay\b", "banco"),
    "Porto Bank": (r"porto bank|\bporto\b(?= seguro| bank)", "banco"),
    "Sem Parar": (r"sem parar", "banco"),
    "Nomad": (r"\bnomad\b", "banco"),
    "XP": (r"\bxp\b", "banco"),
    "BTG": (r"\bbtg\b", "banco"),
    "Mercado Pago": (r"mercado pago", "banco"),
    "Coopera": (r"\bcoopera\b", "banco"),
}
_PROG_RE = {k: re.compile(v[0], re.I) for k, v in PROGRAMS.items()}

STORE_STOP = set("""
a o as os de da do das dos e em na no nas nos para por com sem até ate mais compras compra pontos ponto milhas milha
real reais cada gasto gastos site app loja lojas parceiro parceiros parceiras produto produtos selecionados selecionadas
oferece oferta ofertas ganhe ganha acumule turbina saldo hoje amanhã black friday semana dia novembro clube assinantes
categoria masculina infantil cupom pontua pontue esfera livelo smiles latam azul pass shopping fidelidade
""".split())


@dataclass
class Extracted:
    is_promo: bool = False
    category: str = "outra"
    program: str | None = None
    origin: list[str] = field(default_factory=list)
    destination: str | None = None
    partner: str | None = None
    bonus_pct: int | None = None
    ppr: float | None = None
    discount_pct: int | None = None
    milheiro: float | None = None
    milheiro_calculado: bool = False
    bonus_points: int | None = None
    club_required: str = "não informado"
    registration_required: str = "não informado"
    min_transfer: int | None = None
    limit_text: str | None = None
    credit_deadline: str | None = None
    validity: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    end_source: str = ""
    conditions: list[str] = field(default_factory=list)
    official_links: list[str] = field(default_factory=list)
    expired_flag: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start_at"] = self.start_at.isoformat() if self.start_at else None
        d["end_at"] = self.end_at.isoformat() if self.end_at else None
        return d


def _num(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def _int(s: str) -> int:
    return int(s.replace(".", "").replace(" ", ""))


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


NON_PROMO = re.compile(r"^(como|aprenda|guia|o que é|o que e|entenda|vale a pena|quanto vale|tudo sobre|review|análise|dicas?|saiba como|veja como|conheça|melhores cartões para|ranking|quando)\b|guia completo|como funciona|vale a pena\?|passo a passo|o que é o|tudo o que você precisa", re.I)
PROMO_SIGNAL = re.compile(r"\d+\s?%|(?:pontos?|milhas?)(?:\s+\w+){0,2}\s+(?:por|a cada)\s+(?:real|r\$)|milheiro|r\$\s?\d|\d{1,3}(?:\.\d{3})+\s+(?:pontos|milhas)|bônus|bonus|desconto|off\b|promo", re.I)
RELEVANT = re.compile(r"pontos?|milhas?|livelo|esfera|smiles|latam pass|azul fidelidade|avios|clube|accor|tudoazul", re.I)


TRANSFER_RX = r"transf(?:er|ir|ira|ere)|envio de pontos|envie (?:pontos|milhas)|enviar pontos"


def classify(title: str, text: str) -> str:
    t = title.lower()
    head = (title + " " + text[:600]).lower()
    if (re.search(TRANSFER_RX, t) and re.search(r"bônus|bonus|bonificad", t) and not re.match(r"\s*compr", t)) \
            or re.match(r"\s*b[ôo]nus\b[^%]{0,40}\d{1,3}\s?%", t):
        return "transferencia"
    if re.search(r"compr(?:a|e|ar)\w*\s+(?:de\s+)?(?:pontos|milhas|avios)|(?:pontos|milhas|avios)\s+com\s+(?:até\s+)?\d+\s?%\s+(?:de\s+)?(?:desconto|off)|desconto na compra de (?:pontos|milhas)|\bpagol\b", t):
        return "compra_pontos"
    if re.search(r"\bclube\b", t) and re.search(r"assin|adesão|adesao|upgrade|ganhe até [\d.]+ pontos|bônus em 12 meses|cupom", t):
        return "clube"
    if re.search(r"(?:pontos?|milhas?|pts)(?:\s+\w+){0,2}\s+(?:por|a cada)\s+(?:real|r\$\s?1)|compre e pontue|turbina saldo|pontuação turbinada|pontos por real", t):
        return "compra_bonificada"
    if (re.search(r"resgate|passage(?:m|ns)|trechos?|voos?\b|emiss|classe executiva|executiva|ida e volta|só ida", t) and re.search(r"milhas|pontos|avios", t)) \
            or re.search(r"(?:por|a partir de|ou)\s+[\d.,]+\s*(?:mil\s+)?(?:milhas|pontos|avios)\b", t):
        return "resgate"
    if re.search(r"cartão|cartao|anuidade|meta de gastos|bater meta|pontos extras", t):
        return "cartao"
    if re.search(r"bônus|bonus", t) and re.search(TRANSFER_RX, head):
        return "transferencia"
    if re.search(r"pontos|milhas|pts", t) and not re.search(r"desconto|passage", t) and \
            re.search(r"(?:pontos?|milhas?)(?:\s+\w+){0,2}\s+(?:por|a cada)\s+(?:real|r\$\s?1)", head):
        return "compra_bonificada"
    return "outra"


def programs_in(s: str) -> list[str]:
    found = []
    for name, rx in _PROG_RE.items():
        m = rx.search(s)
        if m:
            found.append((m.start(), name))
    return [n for _, n in sorted(found)]


def _transfer_parties(title: str, text: str) -> tuple[list[str], str | None]:
    t = title
    for m in re.finditer(r"\s+para\s+(?:o\s+|a\s+)?", t):
        left, right = t[max(0, m.start() - 45):m.start()], t[m.end():m.end() + 45]
        o, d = programs_in(left), programs_in(right)
        if o and d:
            return [o[-1]], d[0]
    progs = programs_in(t) or programs_in(text[:500])
    dest = next((p for p in progs if PROGRAMS[p][1] in ("aereo", "hotel")), None)
    origins = [p for p in progs if p != dest and PROGRAMS[p][1] in ("banco", "pontos")]
    if not origins and re.search(r"todos os parceiros|cart(ões|oes) de crédito|bancos|programas parceiros|pontos do cartão|pontos dos cartões", (t + " " + text[:600]).lower()):
        origins = ["Todos os parceiros"]
    if dest is None and len(progs) >= 2:
        dest = progs[-1]
        origins = [p for p in progs if p != dest]
    return origins, dest


def _partner(title: str, text: str) -> str | None:
    if re.search(r"lojas parceiras|ofertas em lojas|\d+\s+(?:ofertas|lojas|parceiros)|várias lojas|diversas lojas", title, re.I):
        return "Várias lojas"
    m0 = re.search(r"(?:pontos?|milhas?)\s+([A-Z][\w&.'’\-]+(?:\s+[A-Z][\w&.'’\-]+)?)\s+(?:por|a cada)\s+(?:real|r\$)", title)
    if m0 and not programs_in(m0.group(1)) and m0.group(1).lower() not in STORE_STOP:
        return m0.group(1)
    m = re.search(r"(?:por|a cada)\s+(?:real|r\$\s?1(?:,00)?)(?:\s+gasto)?(?:\s+em compras)?\s+(?:na|no|nas|nos|em)\s+(.+?)(?:\s+(?:com|até|para|e mais|e outras)\b|[!?.,:;(]|$)", title, re.I)
    if not m:
        m = re.search(r"\b(?:na|no|nas|nos)\s+([A-Z0-9][\w&.'’\-]*(?:\s+[A-Z0-9][\w&.'’\-]*){0,2})", title)
    if not m:
        m = re.search(r"\b(?:esfera|livelo|smiles)\s+e\s+([A-Z][\w&.'’\-]*(?:\s+[A-Z][\w&.'’\-]*){0,2})", title, re.I)
    if not m:
        return None
    cand = m.group(1).strip(" -–")
    if re.search(r",|\be mais\b|\be outras\b|\blojas\b", cand + title[m.end(1):m.end(1) + 12]):
        return "Várias lojas"
    words = [w for w in cand.split() if w.lower() not in STORE_STOP]
    if not words or programs_in(" ".join(words)):
        return None
    return " ".join(words)[:40]


def extract(title: str, text: str, published: datetime, links: list[str] | None = None,
            official_domains: dict | None = None) -> Extracted:
    e = Extracted()
    full = f"{title}\n{text}"
    low = full.lower()
    if not RELEVANT.search(full):
        e.reason = "sem relação com pontos/milhas"
        return e
    if not RELEVANT.search(title) and not programs_in(title):
        e.reason = "título sem relação com pontos/milhas"
        return e
    if (NON_PROMO.search(title.strip()) or title.strip().endswith("?")) and not re.search(r"\d+\s?%\s*(?:de\s+)?(?:bônus|bonus|desconto|off)|pontos por real|até \d{1,2}/", title.lower()):
        e.reason = "conteúdo explicativo, não promoção"
        return e
    if not PROMO_SIGNAL.search(title) and not PROMO_SIGNAL.search(text[:800]):
        e.reason = "sem sinais de promoção"
        return e

    e.category = classify(title, text)
    progs_title = programs_in(title)
    progs_all = progs_title or programs_in(text[:800])

    # ---------- percentuais e números ----------
    def best(rx, src, conv=int):
        vals = [conv(m.group(1)) for m in re.finditer(rx, src, re.I)]
        return max(vals) if vals else None

    bonus_rx = r"(\d{1,3})\s?%\s*(?:de\s+)?(?:bônus|bonus|milhas bônus|pontos bônus|de milhas bônus)|b[ôo]nus(?: de transferência)?[^%\d\n]{0,40}?(?:até\s+)?(\d{1,3})\s?%"
    def bonus_best(src):
        vals = [int(m.group(1) or m.group(2)) for m in re.finditer(bonus_rx, src, re.I)]
        return max(vals) if vals else None
    e.bonus_pct = bonus_best(title) or (bonus_best(text[:1500]) if e.category == "transferencia" else None)
    e.discount_pct = best(r"(\d{1,2})\s?%\s*(?:de\s+)?(?:desconto|off)\b", title) or (
        best(r"(\d{1,2})\s?%\s*(?:de\s+)?(?:desconto|off)\b", text[:1200]) if e.category in ("compra_pontos", "clube") else None)
    ppr_rx = r"(\d{1,2}(?:,\d)?)\s+(?:pontos?|milhas?|pts)(?:\s+[A-Za-zÀ-ú]+){0,2}?\s+(?:por|a cada)\s+(?:real|r\$\s?1(?:,00)?)"
    e.ppr = best(ppr_rx, title, _num) or (best(ppr_rx, text[:2000], _num) if e.category == "compra_bonificada" else None)
    low_head = (title + "\n" + text[:1500]).lower()
    mil = re.search(r"milheiro[^.\n]{0,40}?r\$\s?(\d{1,3},\d{2})|r\$\s?(\d{1,3},\d{2})\s+o milheiro|cpm[^.\n]{0,40}?r\$\s?(\d{1,3},\d{2})|custo para (?:cada )?mil pontos[^.\n]{0,40}?r\$\s?(\d{1,3},\d{2})|compre pontos a partir de r\$\s?(\d{1,3},\d{2})|lote de 1\.000 (?:pontos|milhas)[^.\n]{0,40}?r\$\s?(\d{1,3},\d{2})", low_head)
    if mil:
        e.milheiro = _num(next(g for g in mil.groups() if g))
    elif e.category == "compra_pontos":
        m = re.search(r"de r\$\s?(\d{2,3},\d{2}) por r\$\s?(\d{2,3},\d{2})", low_head)
        if m:
            e.milheiro, e.milheiro_calculado = _num(m.group(2)), False
    if e.milheiro is not None and not (8 <= e.milheiro <= 80):
        e.milheiro = None  # fora da faixa plausível de custo do milheiro: provavelmente outro número
    bp = re.search(r"(\d{1,3}(?:\.\d{3})+)\s+(?:pontos|milhas)(?:\s+\w+)?\s+(?:bônus|bonus|extras|adicionais)", title, re.I) or \
        re.search(r"(?:ganhe|receba|acumule|ganha)(?: até)? (\d{1,3}(?:\.\d{3})+)\s+(?:pontos|milhas)", title, re.I)
    if bp:
        e.bonus_points = _int(bp.group(1))

    # ---------- programas ----------
    if e.category == "transferencia":
        e.origin, e.destination = _transfer_parties(title, text)
        e.program = e.destination or (progs_all[0] if progs_all else None)
    else:
        e.program = progs_all[0] if progs_all else None
        if e.category == "compra_bonificada":
            e.partner = _partner(title, text)
            if e.partner and e.partner.lower() in [p.lower() for p in PROGRAMS]:
                e.partner = None

    # ---------- condições ----------
    if re.search(r"para assinantes d[oa] clube", title.lower()) or re.search(r"exclusiv\w+ (?:para|aos?) assinantes|somente (?:para )?assinantes|apenas (?:para )?assinantes|clientes do clube [\w ]{0,20}(?:podem|é exclusiv)|exclusiva para assinantes", low):
        e.club_required = "sim (exclusivo para assinantes)"
    elif re.search(r"assinantes (?:do |dos planos )?clube[^.\n]{0,80}(?:recebem|têm|ganham|garantem)|demais clientes", low):
        e.club_required = "não, mas assinantes recebem mais"
    elif "clube" in low and "assinante" in low:
        e.club_required = "verificar (menciona assinantes do clube)"
    if re.search(r"não precisa (?:se )?cadastrar|sem necessidade de cadastro|não é necessário (?:se )?cadastrar|sem cadastro", low):
        e.registration_required = "não"
    elif re.search(r"cadastr(?:e|e-se|o|ar|amento)[^.\n]{0,60}(?:promoção|campanha|cpf|página|site)|precisa ativar\?? sim|ative a oferta|ativar a oferta|registre seu cpf", low):
        e.registration_required = "sim"
    m = re.search(r"(?:transferência|envio)\s+mínim[oa][^.\n\d]{0,25}(\d{1,3}(?:\.\d{3})+|\d{3,6})", low)
    if m:
        e.min_transfer = _int(m.group(1))
    m = re.search(r"(?:limitad[oa]s?|limite(?: de)?(?: bônus| acúmulo| compra)?(?: é)?(?: de)?|até)\s+(?:a\s+)?(\d{1,3}(?:\.\d{3})+)\s+(pontos|milhas)(?: bônus)?(?: livelo| esfera| smiles)?(?: por cpf| por ano| por conta)?[^.\n]{0,25}", low)
    if m and ("limit" in m.group(0) or "por cpf" in m.group(0)):
        e.limit_text = m.group(0).strip().rstrip(";,")[:120]
    m = re.search(r"(?:creditad[oa]s?|crédito)[^.\n]{0,40}?(?:em até|até)\s+(\d{1,3}\s+dias(?:\s+(?:úteis|corridos))?|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2} de \w+(?: de \d{4})?)", low)
    if m:
        e.credit_deadline = m.group(1)
    m = re.search(r"(?:serão |terão |com )?válid[oa]s?\s+por\s+(\d{1,2}\s+(?:meses|anos))|validade de (\d{1,2}\s+(?:meses|anos))", low)
    if m:
        e.validity = m.group(1) or m.group(2)
    for rx, label in [
        (r"cupom\s+[\"“']?([A-Z0-9]{4,20})[\"”']?", "Cupom: {0}"),
        (r"pagamento(?:s)? via pix[^.\n]{0,40}?(\d{1,2})\s?% de desconto|(\d{1,2})\s?% (?:de desconto )?(?:adicional )?no pix", "Pix: {0}% de desconto adicional"),
        (r"(?:parcelad[oa]|parcelamento)\s+em até (\d{1,2})\s?x", "Parcelamento em até {0}x"),
        (r"mesma titularidade", "Contas da mesma titularidade"),
        (r"não (?:é )?cumulativ", "Não cumulativa com outras campanhas"),
        (r"primeira transferência[^.\n]{0,60}?(\d{1,3}(?:\.\d{3})*)\s+milhas", "1ª transferência: +{0} milhas"),
    ]:
        mm = re.search(rx, full, 0) if "cupom" in rx else re.search(rx, low, re.I)
        if mm:
            val = next((g for g in mm.groups() if g), "")
            e.conditions.append(label.format(val))

    # ---------- datas ----------
    w = extract_window(title, text, published)
    e.start_at, e.end_at, e.end_source = w.start, w.end, w.end_source
    if e.category == "resgate":
        e.start_at = None  # intervalos em ofertas de passagem costumam ser datas de viagem
    if w.credit_deadline and not e.credit_deadline:
        e.credit_deadline = w.credit_deadline.strftime("%d/%m/%Y")

    # ---------- links oficiais ----------
    if links and official_domains:
        for l in links:
            host = re.sub(r"^https?://(www\.)?", "", l).split("/")[0]
            if any(host == d or host.endswith("." + d) for d in official_domains):
                if l not in e.official_links and not re.search(r"\.(png|jpe?g|webp|gif|svg)(\?|$)", l):
                    e.official_links.append(l)
        e.official_links = e.official_links[:6]

    if re.search(r"⛔|\bexpirad[ao]\b|\bencerrad[ao]\b", full[:250], re.I):
        e.expired_flag = True

    # ---------- decisão final ----------
    has_value = any(v is not None for v in (e.bonus_pct, e.ppr, e.discount_pct, e.milheiro, e.bonus_points))
    if e.category == "outra" and not has_value:
        e.reason = "sem categoria nem valor identificável"
        return e
    if e.category in ("resgate",) and not (has_value or re.search(r"\d+\s?(?:mil)?\s*(?:milhas|pontos)", title.lower())):
        e.reason = "resgate sem valor"
        return e
    e.is_promo = True
    return e
