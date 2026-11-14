"""Testa a coleta (RSS, wp-json, HTML, falhas, pausa de fonte, mudança de promoção) com respostas simuladas."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from milhas.http import Response

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>Smiles oferece até 70% de bônus na transferência de pontos</title><link>https://passageirodeprimeira.com/smiles-70</link>
<pubDate>Mon, 09 Nov 2026 15:00:00 -0300</pubDate><content:encoded><![CDATA[<p>{body}</p>
<p><a href="https://www.smiles.com.br/promocao/regulamento-70">regulamento</a></p>]]></content:encoded></item>
<item><title>Aprenda a usar Avios para fazer upgrade de cabine</title><link>https://passageirodeprimeira.com/aprenda</link>
<pubDate>Mon, 09 Nov 2026 16:00:00 -0300</pubDate><description>Guia completo.</description></item>
</channel></rss>"""
BODY = ("A Smiles liberou uma nova oportunidade de transferência bonificada de todos os parceiros. " * 12 +
        "A transferência deve ser realizada até o dia 13 de novembro de 2026; As milhas bônus serão creditadas até 30/11/2026.")
WP = json.dumps([{"date": "2026-11-09T09:00:00", "link": "https://www.melhorescartoes.com.br/centauro.html",
                  "title": {"rendered": "Ganhe até 7 Pontos Esfera por Real em Compras na Centauro"},
                  "content": {"rendered": "<p>" + "Promoção Esfera e Centauro. " * 40 + "Válida de 09 a 13 de novembro de 2026.</p>"}}])
LISTING = """<html><body><main><a href="https://cartoeseviagens.com.br/transferencia-de-pontos-bonus-70porcento">Ganhe até 70% de bônus na transferência de pontos</a>
<a href="https://cartoeseviagens.com.br/sobre">Sobre</a></main></body></html>"""
ARTICLE = """<html><head><meta property="og:title" content="Ganhe até 70% de bônus na transferência de pontos | Cartões e Viagens">
<meta property="article:published_time" content="2026-11-09T10:00:00-03:00"></head><body><article><p>""" + \
    "Transferência de pontos de crédito para a Smiles. " * 15 + "Período de 09/11/2026 a 13/11/2026. Limite Até 300.000 milhas bônus por CPF.</p></article></body></html>"


class FakeFetcher:
    def __init__(self, routes):
        self.routes, self.requests_made, self.calls = routes, 0, []

    def get(self, url, etag=None, last_modified=None):
        self.requests_made += 1
        self.calls.append(url)
        r = self.routes.get(url)
        if r is None:
            return Response(url, 404, error="não encontrado (404)")
        if isinstance(r, int):
            return Response(url, r, error=f"acesso negado ({r})")
        return Response(url, 200, r, {"etag": '"x"'})


class Collector(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        os.environ.update(MILHAS_DB=f"{self.d.name}/t.db", MILHAS_SITE_DIR=f"{self.d.name}/site", MILHAS_NOW="2026-11-10T12:00:00-03:00")
        import importlib, milhas.config, milhas.site, milhas.pipeline
        importlib.reload(milhas.config); importlib.reload(milhas.site); importlib.reload(milhas.pipeline)
        self.p = milhas.pipeline

    def tearDown(self):
        self.d.cleanup()

    def routes(self, body=BODY):
        return {
            "https://passageirodeprimeira.com/categorias/promocoes/feed/": RSS.replace("{body}", body),
            "https://www.melhorescartoes.com.br/feed": 403,
            "https://www.melhorescartoes.com.br/wp-json/wp/v2/posts?per_page=20&_fields=date,link,title,excerpt,content": WP,
            "https://cartoeseviagens.com.br/promocoes": LISTING,
            "https://cartoeseviagens.com.br/transferencia-de-pontos-bonus-70porcento": ARTICLE,
            "https://www.livelo.com.br/": "<html><body><div id='root'></div><script>app()</script></body></html>",
            "https://www.smiles.com.br/promocao/regulamento-70": 403,
        }

    def test_full_cycle(self):
        only = ["passageirodeprimeira", "melhorescartoes", "cartoeseviagens", "livelo"]
        r = self.p.run("manual", only, FakeFetcher(self.routes()))
        self.assertEqual((r["sources_ok"], r["sources_error"]), (3, 1))
        from milhas.db import connect, rows
        con = connect(Path(os.environ["MILHAS_DB"]))
        src = {s["id"]: s for s in rows(con, "SELECT * FROM sources")}
        self.assertEqual(src["passageirodeprimeira"]["method"], "rss")
        self.assertEqual(src["passageirodeprimeira"]["status"], "automático")
        self.assertEqual(src["melhorescartoes"]["method"], "wp-json")
        self.assertEqual(src["cartoeseviagens"]["status"], "parcial")
        self.assertIn("JavaScript", src["livelo"]["last_error"])
        smiles = rows(con, "SELECT * FROM promos WHERE destination='Smiles'")
        self.assertEqual(len(smiles), 1)                       # PP (RSS) + C&V (HTML) = 1 campanha
        self.assertEqual(smiles[0]["status"], "ativa_verificada")
        self.assertTrue(smiles[0]["official_url"].startswith("https://www.smiles.com.br"))
        self.assertEqual(rows(con, "SELECT COUNT(*) n FROM items WHERE is_promo=0")[0]["n"], 1)  # o guia foi descartado
        # alertas da carga inicial não são disparados nos canais
        self.assertTrue(all(a["sent"].startswith("carga inicial") for a in rows(con, "SELECT * FROM alerts")))
        con.close()

        # 2º ciclo: a campanha é prorrogada → mudança registrada + alerta
        os.environ["MILHAS_NOW"] = "2026-11-12T12:00:00-03:00"
        body2 = BODY.replace("13 de novembro", "16 de novembro")
        self.p.run("manual", only, FakeFetcher(self.routes(body2)))
        con = connect(Path(os.environ["MILHAS_DB"]))
        ch = rows(con, "SELECT * FROM promo_changes")
        self.assertEqual(ch[0]["field"], "end_at")
        self.assertIn("2026-11-16", ch[0]["new"])
        self.assertTrue(rows(con, "SELECT * FROM alerts WHERE kind='mudanca'"))

    def test_cooldown(self):
        f = FakeFetcher({})
        for i in range(5):
            os.environ["MILHAS_NOW"] = f"2026-11-10T1{i}:00:00-03:00"
            self.p.run("agendado", ["tripse"], f)
        from milhas.db import connect
        con = connect(Path(os.environ["MILHAS_DB"]))
        s = dict(con.execute("SELECT * FROM sources WHERE id='tripse'").fetchone())
        self.assertEqual(s["status"], "indisponível")
        self.assertIsNotNone(s["cooldown_until"])
        n = f.requests_made
        os.environ["MILHAS_NOW"] = "2026-11-10T14:30:00-03:00"
        self.p.run("agendado", ["tripse"], f)
        self.assertEqual(f.requests_made, n)  # pausada: nenhuma requisição


if __name__ == "__main__":
    unittest.main()
