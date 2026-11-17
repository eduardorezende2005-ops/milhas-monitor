# CLAUDE.md — Central de Pontos e Milhas (milhas-monitor)

Documento de passagem: resume tudo o que foi construído e decidido nas conversas no Claude (13 a 17/11/2026),
para continuar o trabalho no Claude Code. Coloque este arquivo na raiz do repositório.

## 1. Quem é o usuário e o que ele quer

- **Eduardo Rezende** (Curitiba), consultor financeiro. Fala português; responda sempre em português.
- **Objetivo:** uma central que monitora continuamente promoções de pontos e milhas no Brasil e reúne tudo
  num dashboard. Promoções: transferências bonificadas, compras bonificadas, compra de pontos, clubes,
  cartões e resgates.
- **Princípios inegociáveis que ele pediu:**
  1. **Nunca inventar** preços, prazos, percentuais ou históricos. Campo sem dado fica vazio ou "não informado".
  2. Promoção antiga **não** pode aparecer como ativa só porque o artigo continua no ar.
  3. Usar só coleta autorizada (RSS, API, HTML público), respeitar robots.txt e **nunca contornar**
     login, paywall ou anti-bot.
  4. Deduplicar: a mesma campanha em vários sites vira 1 card, com todas as fontes.
  5. Ser transparente sobre qualquer fonte ou função que não esteja operacional.
- Ele não tem experiência com GitHub. Explique os passos de forma simples quando ele precisar agir.

## 2. Onde está tudo

| Item | Endereço |
|---|---|
| Dashboard (público) | https://eduardorezende2005-ops.github.io/milhas-monitor/ |
| Repositório (público) | https://github.com/eduardorezende2005-ops/milhas-monitor |
| Dados do dashboard | https://eduardorezende2005-ops.github.io/milhas-monitor/data.json |
| Banco persistente | branch `data` do repositório (arquivo único `milhas.db`, commit reescrito a cada ciclo) |
| Agendamento | GitHub Actions, `.github/workflows/monitor.yml` (cron `*/30`; na prática roda a cada 40–70 min) |

**Estado em 17/11/2026 às 17:46:** 86 execuções, todas as recentes com sucesso, 95 promoções ativas
(10 verificadas), resumos diários e o primeiro semanal sendo gerados.

## 3. Arquitetura (custo zero, sem servidor)

- **Python 3.11+ só com a biblioteca padrão.** Nenhum `pip install`, de propósito. SQLite como banco, `urllib`
  para HTTP, `html.parser` e `xml.etree` para parsing, `tomllib` para a configuração.
- **Ciclo** (`python -m milhas run`):
  coleta → extração/classificação → dedup/mesclagem → status → alertas → relatórios → exporta o site.
- **Dashboard estático:** `web/index.html` é o template (JS puro). Na exportação, `site/index.html` recebe os
  dados embutidos no lugar de `/*__DATA__*/null` e `site/data.json` é gerado. O workflow publica no Pages.
- **Por que não FastAPI/PostgreSQL:** pediriam servidor pago. O caminho de evolução fica documentado no README
  (Supabase/Neon gratuitos), caso ele queira API, login ou vários usuários.

### Estrutura de arquivos

```
milhas/
  config.py     caminhos, fuso America/Sao_Paulo, settings.toml; MILHAS_NOW simula o relógio nos testes
  sources.py    as 21 fontes (feeds, wp_api, pages, tier, notas de acesso verificadas) + OFFICIAL_DOMAINS
  http.py       Fetcher: robots.txt, 3 s entre requisições por host, ETag/If-Modified-Since, retentativa, 429
  discover.py   RSS/Atom, wp-json, listagem HTML, parse_article (prioriza blocos entry-content/post-content)
  dates.py      datas em pt-BR: "até 13 de novembro", "de 09 a 13/11", "até sexta-feira (13)", "válida até…"
  extract.py    classificação (7 categorias) e extração de campos; filtros de não promoção
  promos.py     dedup (find_match), upsert com mesclagem por confiabilidade, mudanças, status
  pipeline.py   run(), process_entry(), verify_official(), reprocess(), ingest_seed()
  notify.py     regras de alerta + Telegram / e-mail SMTP / WhatsApp (API oficial da Meta) / dashboard
  reports.py    resumo diário (8h) e semanal (segunda 8h)
  analytics.py  históricos só com dados reais (rotas de transferência, pts/R$ por loja, milheiro, clubes)
  site.py       gera data.json + index.html
  __main__.py   CLI
config/settings.toml   preferências de alerta e limites de coleta
web/index.html         template do dashboard
tests/                 28 testes (test_core.py e test_collector.py) — rodam antes de cada ciclo no CI
seed/real_items_2026-11-13.json   publicações reais (título, URL e trecho; textos não reais foram zerados)
```

### CLI

```bash
python -m milhas run [--manual] [--only id1,id2]   # ciclo completo
python -m milhas reprocess                          # reaplica as regras a tudo o que já foi coletado, sem rede
python -m milhas seed seed/real_items_2026-11-13.json
python -m milhas export | report daily|weekly | test-notify | sources
python -m unittest discover tests -v                # rode também com python3.12 (é a versão do CI)
```

### Workflow (`monitor.yml`)

Passos: checkout → Python 3.12 → restaura `milhas.db` do branch `data` → **testes automáticos** (se falharem,
o ciclo não roda e o site mantém os últimos dados bons) → ciclo → salva o banco no branch `data` (force-push,
um commit só) → backup semanal como artefato (segunda 11h UTC) → upload do Pages → deploy, **com segunda
tentativa após 30 s** (falha intermitente conhecida: "No artifacts named github-pages").
Entradas do *Run workflow*: `report` (daily/weekly) e `reprocess` (`sim` reaplica as regras sem acessar sites).
`concurrency: monitor` evita execuções simultâneas. Timeout de 25 min.

## 4. Regras de negócio importantes

- **Status:** `ativa_verificada` (regulamento oficial confirmou a data **ou** 2+ fontes concordam com margem de
  ±36 h) · `ativa_pendente` (data de uma fonte só) · `futura` · `encerrada` · `indeterminada` (sem data; não
  conta como ativa). O selo "⛔ EXPIRADA"/"encerrada" na listagem marca a promoção como encerrada (`expired_flag`).
- **Confiabilidade (tier):** 3 = oficial, 2 = portal, 1 = agregador. Na mesclagem vale o maior tier; no mesmo
  tier, a publicação mais nova. Mudanças em bônus, pts/R$, desconto, milheiro e encerramento ficam em
  `promo_changes` e geram alerta.
- **Primeira execução:** os alertas ficam só no dashboard (`sent='carga inicial'`), para não disparar dezenas de mensagens.
- **Datas relativas** usam a data de publicação. "Dia N + dia da semana" precisa bater os dois (Barato de Viajar
  não informa a publicação). Prazo explícito ("Oferta válida até…") vence intervalos de datas de viagem.
- **Resgate/outra:** prazo mais de 120 dias após a publicação é tratado como data de viagem e descartado.
- **Milheiro** só é aceito entre R$ 8 e R$ 80 (fora disso costuma ser outro número da página).
- **Links oficiais:** só páginas de campanha ou regulamento. Nunca buscas de voo (`/mfe/`, `emissao`), imagens
  ou URLs com mais de 200 caracteres.
- **Reabertura de matérias:** quem já foi coletado não é reprocessado só com o título da listagem. Texto
  guardado com menos de 400 caracteres é reaberto no máximo a cada 12 h. O ciclo para de abrir matérias após
  15 min (`FETCH_DEADLINE`).

## 5. Fontes (situação real em produção)

| Situação | Fontes |
|---|---|
| Automáticas (RSS) | Passageiro de Primeira (feed da categoria promoções, texto completo), Pontos pra Voar, Melhores Cartões, Melhores Destinos (/milhas/feed), Lalafly (`/blog/rss.xml`), DIA23, Farejador, Radar do Milheiro, Tripse (feed institucional, sem promoções) |
| HTML público + abertura das matérias | Cartões e Viagens (/promocoes), Viaja na Milha (/promocoes, selo ⛔ EXPIRADA), Milhas Hoje (/noticias), Barato de Viajar (agregador, sem data de publicação), AceleraPontos (/blog), Smiles |
| Indisponíveis (aceito; não contornar) | LATAM Pass e Azul (HTTP 403 anti-bot), Livelo (403 a partir do GitHub), Esfera (só JavaScript), Alerta de Milhas Lucrativas e Comunidade VMM (conteúdo pago ou para membros) |

As campanhas dos programas oficiais chegam pelos portais. Fonte com 5 falhas seguidas entra em pausa de 3 h
(por isso algumas execuções aparecem como "parcial").

## 6. Histórico de problemas resolvidos (para não repetir)

1. Upload pela web subiu os arquivos sem as pastas; reorganizei renomeando pelo editor do GitHub.
2. Ao mover `__init__.py`, "ilhas/" foi digitado dentro do código → `NameError`. Corrigido.
3. **Bug de reprocessamento:** na 2ª execução, matérias de fontes HTML eram reprocessadas só com o título da
   listagem, criando duplicatas e **sobrescrevendo o texto guardado**. Corrigido, com reabertura automática de
   textos curtos e testes `test_second_run_is_idempotent` e `test_poor_text_is_refetched`.
4. Deploy do Pages falhando de forma intermitente → segunda tentativa automática no workflow.
5. Qualidade de extração com dados reais:
   - guias e matérias fora do tema eram tratados como promoção;
   - números da barra lateral e de matérias relacionadas contaminavam os campos;
   - datas de viagem eram lidas como prazo da campanha;
   - "sexta-feira (13)" era interpretada como o dia 13 do mês seguinte;
   - rotas com "→" ou "via X" não eram interpretadas, e Emirates Skywards não era reconhecida;
   - "Bônus <destino> <origem>" não era tratado como rota;
   - buscas de voo apareciam como link oficial;
   - o resumo diário não priorizava as melhores oportunidades.

## 7. Pendências e próximos passos

- [ ] **Notificações:** nenhum canal está configurado, então os alertas e resumos só aparecem no dashboard.
  Ele precisa criar o bot no @BotFather e salvar `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` em
  *Settings → Secrets and variables → Actions*. E-mail: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
  `SMTP_PASSWORD` (senha de app do Gmail) e `EMAIL_TO`. WhatsApp exige conta paga na Meta.
  Depois: `python -m milhas test-notify` ou um ciclo manual.
- [ ] Muitas promoções ficam "indeterminadas" (93), sobretudo ofertas de passagem sem prazo. Avaliar se o
  dashboard deve escondê-las por padrão ou separá-las numa aba.
- [ ] Duplicatas residuais: o mesmo anúncio republicado com outro prazo (ex.: Qatar 25%, Barato de Viajar).
- [ ] Os programas oficiais seguem fora da coleta direta. Não contornar os bloqueios.
- [ ] Ideias que ele pode querer: filtros por "melhor custo de milheiro hoje", página por programa,
  alerta de compra bonificada acima do histórico (já existe a regra, falta histórico acumulado).

## 8. Como trabalhar neste projeto (no Claude Code)

- Antes de qualquer commit: `python -m unittest discover tests` com **python3.12** e com a versão local.
  Todo bug corrigido ganha um teste com o caso real.
- Depois de mudar regras de extração ou datas: commit e push, e então *Run workflow* com `reprocess=sim`
  (ou `gh workflow run monitor.yml -f reprocess=sim`).
- Para auditar produção sem acessar os sites: baixe `data.json` e confira duplicatas, datas e valores
  absurdos, ou `git fetch origin data` e abra o `milhas.db` com `sqlite3`.
- Envie juntos o código e os testes que dependem dele (os testes rodam no CI antes de cada ciclo).
- Atenção: o branch `data` é reescrito a cada ciclo. Não trabalhe nele.
- No Claude, as edições eram feitas pelo editor web do GitHub via Chrome, porque não havia acesso git.
  No Claude Code, use git normalmente (`gh auth login` uma vez).

## 9. Primeiros comandos sugeridos no Claude Code

```bash
gh repo clone eduardorezende2005-ops/milhas-monitor && cd milhas-monitor
# copie este CLAUDE.md para a raiz e faça commit
python3 -m unittest discover tests -v
gh run list --workflow monitor.yml --limit 10      # saúde das execuções
curl -s https://eduardorezende2005-ops.github.io/milhas-monitor/data.json | python3 -m json.tool | head -40
```
