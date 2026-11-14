"""Testes com publicações reais (seed/real_items_2026-11-13.json). Rodar: python -m unittest -v"""
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed" / "real_items_2026-11-13.json"


def ex(title, text="", pub="2026-11-09T10:00:00-03:00"):
    from milhas.extract import extract
    return extract(title, text, datetime.fromisoformat(pub))


class Dates(unittest.TestCase):
    def w(self, title, text, ref="2026-11-09T10:00:00-03:00"):
        from milhas.dates import extract_window
        return extract_window(title, text, datetime.fromisoformat(ref))

    def test_range_numeric(self):
        w = self.w("x", "Período de 09/11/2026 a 13/11/2026")
        self.assertEqual((w.start.day, w.end.day, w.end.hour), (9, 13, 23))

    def test_bare_range(self):
        w = self.w("x", "válida de 09 a 13 de novembro de 2026, com")
        self.assertEqual((w.start.day, w.end.day), (9, 13))

    def test_credit_not_end(self):
        w = self.w("x", "realizada até o dia 13 de novembro de 2026; As milhas bônus serão creditadas até 30/11/2026")
        self.assertEqual(w.end.day, 13)
        self.assertEqual(w.credit_deadline.day, 30)

    def test_weekday_paren(self):
        self.assertEqual(self.w("até domingo, dia 15", "").end.day, 15)
        self.assertEqual(self.w("x", "válida até amanhã (10)").end.day, 10)

    def test_today(self):
        self.assertEqual(self.w("SÓ ATÉ MEIA-NOITE! oferta", "", "2026-11-13T20:00:00-03:00").end.day, 13)

    def test_single_day(self):
        w = self.w("x", "válida durante o dia 09 de novembro de 2026")
        self.assertEqual((w.start.day, w.end.day), (9, 9))

    def test_year_rollover(self):
        w = self.w("x", "válida até 05/01", "2026-12-28T10:00:00-03:00")
        self.assertEqual((w.end.year, w.end.month), (2027, 1))


    def test_weekday_must_match(self):  # página sem data de publicação (Barato de Viajar)
        self.assertEqual(self.w("x", "A promoção é válida até sexta-feira (13) e", "2026-11-14T01:00:00-03:00").end.day, 13)

    def test_explicit_end_beats_travel_range(self):  # Radar do Milheiro
        w = self.w("x", "datas de ida entre 16 de novembro de 2026 e 8 de dezembro de 2026. Oferta válida até 15/11/2026", "2026-11-13T08:00:00-03:00")
        self.assertEqual((w.start, w.end.day), (None, 15))


class Extraction(unittest.TestCase):
    def test_real_world_filters(self):
        self.assertFalse(ex("Viagem marcada no fim de ano? Reserve sua vaga na Estapar com 10% OFF", "pontos Azul").is_promo)
        self.assertFalse(ex("LATAM Pass 2026: Como Funciona? O Guia Completo (Clube, Gold, Black e Tabela Fixa)").is_promo)
        self.assertEqual(ex("Bônus TudoAzul: até 90% na Nomad").bonus_pct, 90)
        self.assertEqual(ex("APROVEITE: Montreal a partir de R$ 1.488 ou 93.000 milhas Smiles só ida").category, "resgate")
        self.assertEqual(ex("Receba até 388.000 pontos ao assinar o Clube Livelo Premium").bonus_points, 388000)
        self.assertTrue(ex("Clube Azul 2.000 por R$ 27 no 1º mês ⛔ EXPIRADA 50% off nos demais").expired_flag)

    def test_transfer(self):
        e = ex("Livelo para Qatar Airways: 35% de bônus na transferência até 13 de novembro", "envio mínimo de 3.000 pontos")
        self.assertEqual((e.category, e.origin, e.destination, e.bonus_pct, e.min_transfer), ("transferencia", ["Livelo"], "Qatar Privilege Club", 35, 3000))

    def test_transfer_verb(self):
        e = ex("Transfira pontos BRB para Azul e ganhe até 120% de bônus")
        self.assertEqual((e.category, e.origin, e.destination), ("transferencia", ["BRB"], "Azul Fidelidade"))

    def test_bonified(self):
        e = ex("Ganhe até 7 Pontos Esfera por Real em Compras na Centauro")
        self.assertEqual((e.category, e.program, e.partner, e.ppr), ("compra_bonificada", "Esfera", "Centauro", 7.0))

    def test_bonified_many(self):
        self.assertEqual(ex("Turbina saldo! Esfera oferece até 14 pontos por real gasto na Insider, Polishop e mais").partner, "Várias lojas")

    def test_buy_points(self):
        e = ex("Livelo oferece até 57% de desconto na compra de pontos para assinantes do Clube até domingo, dia 15")
        self.assertEqual((e.category, e.discount_pct, e.end_at.day), ("compra_pontos", 57, 15))
        self.assertTrue(e.club_required.startswith("sim"))

    def test_milheiro(self):
        self.assertEqual(ex("Compre pontos a partir de R$ 29,40 o milheiro", "livelo").milheiro, 29.4)

    def test_club(self):
        e = ex("Ganhe até 25.000 pontos ao assinar o Clube Livelo com nosso cupom exclusivo – milheiro a partir de R$ 21,56")
        self.assertEqual((e.category, e.bonus_points, e.milheiro), ("clube", 25000, 21.56))

    def test_not_promo(self):
        self.assertFalse(ex("Aprenda a usar Avios para fazer upgrade de cabine").is_promo)
        self.assertFalse(ex("Aproveite os próximos feriados estacionando com 10% de desconto com a Estapar").is_promo)

    def test_never_invents(self):
        e = ex("Ganhe até 120% de bônus na transferência de pontos")
        self.assertIsNone(e.end_at)
        self.assertIsNone(e.milheiro)


class Pipeline(unittest.TestCase):
    def test_seed_dedup_and_status(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ.update(MILHAS_DB=f"{d}/t.db", MILHAS_SITE_DIR=f"{d}/site", MILHAS_NOW="2026-11-13T23:40:00-03:00")
            import importlib, milhas.config, milhas.pipeline, milhas.site
            importlib.reload(milhas.config); importlib.reload(milhas.site); importlib.reload(milhas.pipeline)
            r = milhas.pipeline.ingest_seed(str(SEED))
            self.assertTrue(r["first_run"])
            from milhas.db import connect, rows
            con = connect(Path(f"{d}/t.db"))
            smiles = rows(con, "SELECT * FROM promos WHERE category='transferencia' AND destination='Smiles'")
            self.assertEqual(len(smiles), 1, "a mesma campanha da Smiles (2 sites) deve virar 1 promoção")
            n = con.execute("SELECT COUNT(*) FROM promo_sources WHERE promo_id=?", (smiles[0]["id"],)).fetchone()[0]
            self.assertEqual(n, 2)
            self.assertEqual(smiles[0]["status"], "ativa_verificada")  # 2 fontes, mesma data
            itau = rows(con, "SELECT * FROM promos WHERE destination='Azul Fidelidade' AND origin LIKE '%Itaú%'")[0]
            self.assertEqual(itau["status"], "encerrada")
            latam = rows(con, "SELECT * FROM promos WHERE destination='LATAM Pass'")[0]
            self.assertEqual(latam["status"], "ativa_pendente")
            data = json.loads(Path(f"{d}/site/data.json").read_text())
            self.assertGreater(len(data["promos"]), 10)
            self.assertIn("/*__DATA__*/null", (ROOT / "web" / "index.html").read_text())
            self.assertNotIn("/*__DATA__*/null", Path(f"{d}/site/index.html").read_text())


if __name__ == "__main__":
    unittest.main()
