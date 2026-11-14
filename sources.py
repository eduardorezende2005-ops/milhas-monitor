"""As 21 fontes monitoradas.

Cada fonte tem uma cadeia de métodos tentados em ordem: feeds RSS/Atom → API REST do
WordPress → página HTML pública de listagem. O primeiro método que funcionar é
memorizado no banco (coluna `method`) e o painel de fontes mostra o resultado real.

Campos:
  tier        3 = programa oficial, 2 = portal especializado, 1 = agregador/ferramenta
  feeds       feeds candidatos (o coletor também descobre <link rel="alternate">)
  wp_api      tenta /wp-json/wp/v2/posts
  pages       páginas HTML públicas com listagem de promoções
  access      o que se sabe sobre o acesso, com a origem da informação
  needs_login None = desconhecido até a 1ª execução; True/False quando verificado
"""
from __future__ import annotations

SOURCES: list[dict] = [
    # ---------------- Portais especializados ----------------
    {
        "id": "passageirodeprimeira", "name": "Passageiro de Primeira", "tier": 2,
        "url": "https://passageirodeprimeira.com/",
        "feeds": ["https://passageirodeprimeira.com/categorias/promocoes/feed/",
                   "https://passageirodeprimeira.com/feed/"],
        "wp_api": True,
        "pages": ["https://passageirodeprimeira.com/categorias/promocoes/"],
        "access": "RSS da categoria Promoções com texto completo (verificado em 14/11/2026).",
        "needs_login": False,
    },
    {
        "id": "pontospravoar", "name": "Pontos pra Voar", "tier": 2,
        "url": "https://pontospravoar.com/",
        "feeds": ["https://pontospravoar.com/feed/"],
        "wp_api": True,
        "pages": ["https://pontospravoar.com/"],
        "access": "RSS com texto completo (verificado em 14/11/2026). Publica tabelas 'Radar PPV' de compras bonificadas.",
        "needs_login": False,
    },
    {
        "id": "melhorescartoes", "name": "Melhores Cartões", "tier": 2,
        "url": "https://www.melhorescartoes.com.br/",
        "feeds": ["https://www.melhorescartoes.com.br/feed"],
        "wp_api": True,
        "pages": ["https://www.melhorescartoes.com.br/cartoes-milhas",
                  "https://www.melhorescartoes.com.br/resumo-promocoes-cartoes-credito-milhas-pontos-cashback.html"],
        "access": "RSS /feed (verificado em 14/11/2026). Só 10 itens por feed, de todas as editorias.",
        "needs_login": False,
    },
    {
        "id": "melhoresdestinos", "name": "Melhores Destinos", "tier": 2,
        "url": "https://www.melhoresdestinos.com.br/",
        "feeds": ["https://www.melhoresdestinos.com.br/milhas/feed",
                  "https://www.melhoresdestinos.com.br/feed"],
        "wp_api": True,
        "pages": ["https://www.melhoresdestinos.com.br/milhas"],
        "access": "RSS da seção /milhas/ (verificado em 14/11/2026). Muitas ofertas de resgate de passagens.",
        "needs_login": False,
    },
    {
        "id": "cartoeseviagens", "name": "Cartões e Viagens", "tier": 2,
        "url": "https://cartoeseviagens.com.br/promocoes",
        "feeds": ["https://cartoeseviagens.com.br/feed/", "https://cartoeseviagens.com.br/rss"],
        "wp_api": False,
        "pages": ["https://cartoeseviagens.com.br/promocoes"],
        "access": "Sem RSS. Coleta pela página /promocoes + abertura de cada matéria (verificado em 14/11/2026).",
        "needs_login": False,
    },
    {
        "id": "viajanamilha", "name": "Viaja na Milha", "tier": 2,
        "url": "https://viajanamilha.com.br/promocoes",
        "feeds": ["https://viajanamilha.com.br/feed/"],
        "wp_api": True,
        "pages": ["https://viajanamilha.com.br/promocoes"],
        "access": "Sem RSS/API. Coleta pela página /promocoes; marca promoções vencidas com '⛔ EXPIRADA', usado como sinal de encerramento (14/11/2026).",
        "needs_login": False,
    },
    {
        "id": "lalafly", "name": "Lalafly", "tier": 2,
        "url": "https://lalafly.com.br/blog",
        "feeds": ["https://lalafly.com.br/blog/rss.xml"],
        "wp_api": True,
        "pages": ["https://lalafly.com.br/blog"],
        "access": "RSS em /blog/rss.xml (descoberto automaticamente). Páginas-resumo por programa, atualizadas continuamente, com bloco 'Período da campanha' (14/11/2026).",
        "needs_login": False,
    },
    # ---------------- Agregadores e ferramentas ----------------
    {
        "id": "dia23", "name": "DIA23", "tier": 1,
        "url": "https://dia23.com.br/milhas/promocoes/",
        "feeds": ["https://dia23.com.br/feed/"],
        "wp_api": True,
        "pages": ["https://dia23.com.br/milhas/promocoes/"],
        "access": "RSS /feed/ (verificado em 14/11/2026). Mistura guias e promoções; guias são descartados.",
        "needs_login": False,
    },
    {
        "id": "farejador", "name": "Farejador de Milhas", "tier": 1,
        "url": "https://farejadordemilhas.com.br/",
        "feeds": ["https://farejadordemilhas.com.br/feed/"],
        "wp_api": True,
        "pages": ["https://farejadordemilhas.com.br/"],
        "access": "RSS /feed/ (verificado em 14/11/2026).",
        "needs_login": False,
    },
    {
        "id": "milhashoje", "name": "Milhas Hoje", "tier": 1,
        "url": "https://milhashoje.com.br/",
        "feeds": ["https://milhashoje.com.br/feed/"],
        "wp_api": True,
        "pages": ["https://milhashoje.com.br/noticias", "https://milhashoje.com.br/"],
        "access": "Sem RSS. Coleta pela página /noticias (verificado em 14/11/2026).",
        "needs_login": False,
    },
    {
        "id": "radardomilheiro", "name": "Radar do Milheiro", "tier": 1,
        "url": "https://radardomilheiro.com.br/",
        "feeds": ["https://radardomilheiro.com.br/feed/"],
        "wp_api": True,
        "pages": ["https://radardomilheiro.com.br/"],
        "access": "RSS /feed/ (verificado em 14/11/2026). Foco em ofertas de emissão com milhas (resgate), com 'Oferta válida até'.",
        "needs_login": False,
    },
    {
        "id": "baratodeviajar", "name": "Barato de Viajar", "tier": 1,
        "url": "https://baratodeviajar.com.br/",
        "feeds": ["https://baratodeviajar.com.br/feed/"],
        "wp_api": True,
        "pages": ["https://baratodeviajar.com.br/"],
        "access": "Sem RSS. Agregador que republica outros portais em /oferta/…; não informa data de publicação (14/11/2026).",
        "needs_login": False,
    },
    {
        "id": "acelerapontos", "name": "AceleraPontos", "tier": 1,
        "url": "https://acelerapontos.app/",
        "feeds": [],
        "wp_api": False,
        "pages": ["https://acelerapontos.app/blog", "https://acelerapontos.app/"],
        "access": "Sem RSS. Coleta pelo /blog público; o app em si exige instalação/login (14/11/2026).",
        "needs_login": None,
    },
    {
        "id": "tripse", "name": "Tripse", "tier": 1,
        "url": "https://www.tripse.com.br/",
        "feeds": [],
        "wp_api": True,
        "pages": ["https://www.tripse.com.br/"],
        "access": "O feed /feed/ existe mas é do blog institucional do app, sem promoções de pontos (14/11/2026).",
        "needs_login": False,
    },
    {
        "id": "iurimilhas_aml", "name": "Alerta de Milhas Lucrativas (Iuri Milhas)", "tier": 1,
        "url": "https://www.iurimilhas.com.br/aml/",
        "feeds": [],
        "wp_api": False,
        "pages": ["https://www.iurimilhas.com.br/aml/"],
        "access": "Página pública sem promoções legíveis; os alertas são para assinantes (14/11/2026). Alternativa: encaminhar os alertas recebidos ao seu bot do Telegram.",
        "needs_login": True,
    },
    {
        "id": "comunidadevmm", "name": "Comunidade VMM", "tier": 1,
        "url": "https://www.comunidadevmm.com.br/",
        "feeds": [],
        "wp_api": False,
        "pages": ["https://www.comunidadevmm.com.br/"],
        "access": "Página pública sem promoções; conteúdo restrito a membros (14/11/2026).",
        "needs_login": True,
    },
    # ---------------- Programas oficiais ----------------
    {
        "id": "livelo", "name": "Livelo", "tier": 3,
        "url": "https://www.livelo.com.br/",
        "feeds": [],
        "wp_api": False,
        "pages": ["https://www.livelo.com.br/"],
        "access": "Site em JavaScript: a página inicial não expõe campanhas legíveis sem login (14/11/2026). Usado para confirmar regulamentos linkados pelos portais.",
        "needs_login": None,
    },
    {
        "id": "esfera", "name": "Esfera", "tier": 3,
        "url": "https://www.esfera.com.vc/",
        "feeds": [],
        "wp_api": False,
        "pages": ["https://www.esfera.com.vc/"],
        "access": "Site em JavaScript sem conteúdo legível sem navegador (14/11/2026). Usado para confirmar regulamentos linkados.",
        "needs_login": None,
    },
    {
        "id": "smiles", "name": "Smiles", "tier": 3,
        "url": "https://www.smiles.com.br/",
        "feeds": [],
        "wp_api": False,
        "pages": ["https://www.smiles.com.br/"],
        "access": "Página pública acessível, mas sem lista de campanhas legível (14/11/2026). Usado para confirmar regulamentos linkados.",
        "needs_login": None,
    },
    {
        "id": "latampass", "name": "LATAM Pass", "tier": 3,
        "url": "https://latampass.latam.com/br/pt",
        "feeds": [],
        "wp_api": False,
        "pages": ["https://latampass.latam.com/br/pt"],
        "access": "Bloqueia acessos automatizados (HTTP 403) — não contornado (14/11/2026). Campanhas chegam pelos portais.",
        "needs_login": False,
    },
    {
        "id": "azul", "name": "Azul Fidelidade", "tier": 3,
        "url": "https://www.voeazul.com.br/br/pt/programa-fidelidade/parceiros",
        "feeds": [],
        "wp_api": False,
        "pages": ["https://www.voeazul.com.br/br/pt/programa-fidelidade/parceiros"],
        "access": "Bloqueia acessos automatizados (HTTP 403) — não contornado (14/11/2026). Campanhas chegam pelos portais.",
        "needs_login": False,
    },
]

# Domínios oficiais: um link de regulamento para estes domínios, quando acessível,
# serve para confirmar datas ("Ativa e verificada").
OFFICIAL_DOMAINS = {
    "livelo.com.br": "Livelo", "esfera.com.vc": "Esfera", "smiles.com.br": "Smiles",
    "latampass.latam.com": "LATAM Pass", "latam.com": "LATAM Pass",
    "voeazul.com.br": "Azul Fidelidade", "tudoazul.com": "Azul Fidelidade",
    "all.accor.com": "ALL Accor", "iberia.com": "Iberia Plus", "qatarairways.com": "Qatar Privilege Club",
    "flytap.com": "TAP Miles&Go", "itau.com.br": "Itaú", "nubank.com.br": "Nubank",
    "c6bank.com.br": "C6 Átomos", "bancointer.com.br": "Inter Loop", "inter.co": "Inter Loop",
}


def by_id(source_id: str) -> dict | None:
    return next((s for s in SOURCES if s["id"] == source_id), None)
