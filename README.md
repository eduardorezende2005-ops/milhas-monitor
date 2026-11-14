# Central de Pontos e Milhas

Monitor automático de promoções de pontos e milhas no Brasil. A cada 30 minutos ele consulta 21 fontes,
extrai e classifica as campanhas, junta as duplicadas, confere a validade, guarda o histórico, envia alertas
e publica um dashboard.

**Custo: zero.** Roda no GitHub Actions (agendador) + GitHub Pages (dashboard), com SQLite como banco.
Nada depende de uma conversa aberta com o Claude.

```
GitHub Actions (cron */30) ──► python -m milhas run
   │  1. coleta: RSS → API WordPress → HTML público (respeita robots.txt, 1 req/3 s por site)
   │  2. extração e classificação (7 categorias) a partir de título + texto
   │  3. deduplicação (mesma campanha em vários sites = 1 card, com todas as fontes)
   │  4. validade: datas, regulamento oficial, concordância entre fontes
   │  5. histórico + detecção de mudanças (bônus, prazo, pontuação)
   │  6. alertas (Telegram / e-mail / WhatsApp oficial / dashboard)
   │  7. resumo diário (8h) e semanal (segunda, 8h), horário de Brasília
   ├──► data/milhas.db  ──► branch "data" (estado persistente)
   └──► site/index.html + data.json ──► GitHub Pages (dashboard)
```

## Implantação (cerca de 15 minutos)

1. **Crie um repositório no GitHub** e envie esta pasta:
   ```bash
   git init && git add . && git commit -m "monitor de milhas"
   git branch -M main
   git remote add origin https://github.com/SEU_USUARIO/milhas-monitor.git
   git push -u origin main
   ```
   *Público ou privado?* Em repositório **público** o Actions é ilimitado e o Pages é grátis, mas o dashboard fica
   acessível a quem tiver o link (só há dados públicos de promoções; os segredos nunca aparecem).
   Em **privado**, 48 execuções/dia × ~1–2 min podem passar dos 2.000 min/mês gratuitos, e o Pages privado exige
   plano pago. Recomendação: público.
2. **Ative o Pages:** *Settings → Pages → Source: GitHub Actions*.
3. **Permissões:** *Settings → Actions → General → Workflow permissions: Read and write*.
4. **(Opcional) Notificações** em *Settings → Secrets and variables → Actions*:

   | Canal | Secrets |
   |---|---|
   | Telegram | `TELEGRAM_BOT_TOKEN` (crie com o @BotFather), `TELEGRAM_CHAT_ID` (mande uma mensagem ao bot e abra `https://api.telegram.org/bot<TOKEN>/getUpdates`) |
   | E-mail | `SMTP_HOST` (ex.: `smtp.gmail.com`), `SMTP_PORT` (`587`), `SMTP_USER`, `SMTP_PASSWORD` (no Gmail, uma *senha de app*), `EMAIL_TO`, `EMAIL_FROM` opcional |
   | WhatsApp | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_TO` e `WHATSAPP_TEMPLATE` — exige conta no **WhatsApp Business Platform (Meta)**. Mensagens iniciadas pelo sistema fora da janela de 24h precisam de template aprovado, e a Meta cobra por conversa. Fica desligado até você configurar. |

5. **Primeira execução:** *Actions → monitor → Run workflow*. A primeira carga **não** dispara alertas nos
   canais (evita dezenas de mensagens de uma vez); os seguintes, sim. O link do dashboard aparece no resumo do job
   (`https://SEU_USUARIO.github.io/milhas-monitor/`).
6. Teste os canais localmente, se quiser: `TELEGRAM_BOT_TOKEN=… TELEGRAM_CHAT_ID=… python -m milhas test-notify`.

O botão **⟳ Atualizar agora** do dashboard abre o *Run workflow* do Actions (atualização manual).
O GitHub pode atrasar execuções agendadas em alguns minutos nos horários de pico. Também desativa agendamentos de
repositórios públicos depois de 60 dias sem atividade; o commit automático no branch `data` deve manter o repositório ativo,
mas se o GitHub avisar por e-mail, basta reativar em *Actions*.

## Uso local

Python 3.11+ e só a biblioteca padrão (nenhum `pip install`).

```bash
python -m milhas run --manual          # ciclo completo
python -m milhas run --only livelo,smiles
python -m milhas sources               # status das fontes
python -m milhas report daily          # força o resumo diário
python -m milhas seed seed/real_items_2026-11-13.json   # carga de teste com publicações reais
python -m unittest discover tests -v   # 22 testes
python -m milhas reprocess             # reextrai tudo o que já foi coletado, sem acessar os sites
```

Preferências de alerta, limites de coleta e horários: `config/settings.toml`
(ex.: `transfer_min_bonus = 80`, `ppr_min = 10`, `milheiro_max = 30.0`, `programs = ["Smiles"]`).

## Como cada requisito é atendido

| Requisito | Implementação |
|---|---|
| Coleta autorizada | `milhas/http.py`: robots.txt, User-Agent identificado, intervalo por site, GET condicional (ETag), backoff, respeito a 429. Login, paywall e anti-bot **nunca** são contornados |
| Falhas | Cada fonte isolada; 5 falhas seguidas → pausa de 3h; erros registrados por execução e por fonte |
| Classificação | `milhas/extract.py`: transferência, compra bonificada, compra de pontos, clube, cartão, resgate, outras; programa, origem → destino, loja, %, pts/R$, milheiro, limite, mínimo, clube, cadastro, crédito, validade, cupom |
| Duplicatas | `milhas/promos.py`: mesma categoria + programas/loja + janela de datas. Fonte oficial > portal > agregador; dentro do mesmo nível, a mais recente |
| Mudanças | Bônus, pontos/R$, desconto, milheiro e encerramento são versionados em `promo_changes` e geram alerta |
| Status | **Ativa e verificada** (regulamento oficial confirmou a data ou 2+ fontes concordam) · **Ativa, pendente** (data vinda de uma fonte só) · **Futura** · **Encerrada** · **Indeterminada** (sem data identificada; não entra na contagem de ativas) |
| Nada inventado | Campo sem valor no texto fica vazio/"não informado". Históricos e gráficos só aparecem com dados reais |
| Histórico | `milhas/analytics.py`: faixas de bônus por rota, mediana de pts/R$ por loja (alerta "acima do padrão" com 2+ registros), milheiro por programa, campanhas de clubes, programas mais ativos |

## Fontes: resultado real da coleta (14/11/2026)

Primeira coleta completa contra os 21 sites: **273 publicações lidas, 16 fontes funcionando**.

| Situação | Fontes | Método |
|---|---|---|
| **Automáticas (RSS)** — 9 | Passageiro de Primeira, Pontos pra Voar, Melhores Cartões, Melhores Destinos, Lalafly, DIA23, Farejador de Milhas, Radar do Milheiro, Tripse* | Feed RSS, geralmente com texto completo |
| **Parciais (HTML público)** — 7 | Cartões e Viagens, Viaja na Milha, Milhas Hoje, Barato de Viajar, AceleraPontos (blog), Livelo, Smiles | Página de listagem + abertura de cada matéria |
| **Bloqueiam robôs (HTTP 403)** — 2 | LATAM Pass, Azul Fidelidade | Não contornado; as campanhas chegam pelos portais |
| **Sem conteúdo legível sem navegador** — 1 | Esfera | Site em JavaScript; campanhas chegam pelos portais |
| **Conteúdo para assinantes/membros** — 2 | Alerta de Milhas Lucrativas, Comunidade VMM | Só a página pública. Alternativa: encaminhar os alertas que você recebe para o seu bot do Telegram |

\* O feed da Tripse é do blog institucional do app e não traz promoções de pontos.

Os sites oficiais (Livelo, Esfera, Smiles, LATAM, Azul) não publicam listas de campanhas legíveis sem login.
Eles servem para **conferir regulamentos** linkados pelos portais, o que gera o status "verificada".
As campanhas deles aparecem no dashboard porque os portais as divulgam, quase sempre no mesmo dia.

## Manutenção

- **Ajustar uma fonte:** `milhas/sources.py` (feeds, páginas, notas de acesso).
- **Novo padrão de texto não reconhecido:** adicione o caso em `tests/test_core.py` e ajuste a regex em `milhas/extract.py` ou `milhas/dates.py`.
- **Banco:** branch `data` (arquivo `milhas.db`). Backup semanal como artefato do Actions (30 dias). Para inspecionar: `sqlite3 milhas.db "select title,status,end_at from promos order by id desc limit 20"`.
- **Evolução possível:** FastAPI + PostgreSQL (Supabase/Neon gratuitos) se quiser API própria, login ou várias pessoas usando. A arquitetura atual foi escolhida por custo zero e zero servidor.

## Dependências externas

| Recurso | Depende de | Situação |
|---|---|---|
| Agendamento a cada 30 min | GitHub Actions | Grátis em repositório público |
| Dashboard | GitHub Pages | Grátis em repositório público |
| Telegram / e-mail | Seus tokens/SMTP | Desligado até configurar |
| WhatsApp | Meta WhatsApp Business Platform | Pago por conversa; desligado até configurar |
