import tempfile
import unittest
from pathlib import Path

from analises import analise_gastos
from faturas import (
    categorizar_lancamento, conectar, importar, inicializar, lancamentos, salvar_orcamento,
)


CSV_NOVEMBRO = b"""Data;Descricao;Estabelecimento;Valor;Tipo;Parcela
05/11/2026;Mercado;Mercado Azul;10,00;Compra;1/2
06/11/2026;Restaurante;Bistro;20,00;Compra;-
07/11/2026;Pagamento da fatura;Banco;30,00;Pagamento;-
08/11/2026;Estorno;Bistro;-5,00;Estorno;-
"""
CSV_DEZEMBRO = b"""Data;Descricao;Estabelecimento;Valor;Tipo;Parcela
05/12/2026;Mercado;Mercado Azul;10,00;Compra;2/2
"""


class AnaliseGastosTest(unittest.TestCase):
    def setUp(self):
        self.temporario = tempfile.TemporaryDirectory()
        self.conn = conectar(Path(self.temporario.name) / "gastos.sqlite3")
        inicializar(self.conn)
        self.novembro = importar(self.conn, CSV_NOVEMBRO, "Cartão teste", "2026-11", "positivo")
        mercado = next(item for item in lancamentos(self.conn, self.novembro["id"], "", "", "", 50, 0)["itens"]
                       if item["estabelecimento"] == "Mercado Azul")
        categorizar_lancamento(self.conn, mercado["id"], "Supermercado", True)

    def tearDown(self):
        self.conn.close()
        self.temporario.cleanup()

    def test_reconcilia_despesas_e_exclui_pagamento(self):
        dados = analise_gastos(self.conn, "Cartão teste", self.novembro["id"])
        self.assertEqual(3000, dados["despesas_elegiveis_centavos"])
        self.assertEqual(3000, sum(item["total_centavos"] for item in dados["por_categoria"]))
        self.assertEqual(500, dados["creditos_estornos_centavos"])
        self.assertEqual(1, dados["sem_categoria"]["quantidade"])

    def test_orcamento_e_compromisso_futuro_sao_separados(self):
        salvar_orcamento(self.conn, "Cartão teste", "Supermercado", "12,00")
        dados = analise_gastos(self.conn, "Cartão teste", self.novembro["id"])
        mercado = next(item for item in dados["orcamentos"] if item["categoria"] == "Supermercado")
        self.assertEqual(1000, mercado["gasto_centavos"])
        self.assertEqual(1200, mercado["limite_centavos"])
        self.assertEqual(8333, mercado["percentual_base_pontos"])
        self.assertEqual(1000, mercado["compromissos_futuros_centavos"])
        self.assertEqual(3000, dados["despesas_elegiveis_centavos"])

    def test_regra_categoria_aplica_na_fatura_seguinte(self):
        dezembro = importar(self.conn, CSV_DEZEMBRO, "Cartão teste", "2026-12", "positivo")
        item = lancamentos(self.conn, dezembro["id"], "", "", "", 50, 0)["itens"][0]
        self.assertEqual("Supermercado", item["categoria"])

    def test_substituicao_preserva_categoria_manual_inequivoca(self):
        substituta = CSV_NOVEMBRO + b"09/11/2026;Outra;Outro;1,00;Compra;-\n"
        importar(self.conn, substituta, "Cartão teste", "2026-11", "positivo", substituir=True)
        categorias = lancamentos(self.conn, self.novembro["id"], "", "", "", 50, 0)["itens"]
        mercado = next(item for item in categorias if item["estabelecimento"] == "Mercado Azul")
        self.assertEqual("Supermercado", mercado["categoria"])

    def test_reimportacao_nao_remove_orcamento(self):
        salvar_orcamento(self.conn, "Cartão teste", "Lazer", "50,00")
        resultado = importar(self.conn, CSV_NOVEMBRO, "Cartão teste", "2026-11", "positivo")
        self.assertEqual("ja_importada", resultado["status"])
        dados = analise_gastos(self.conn, "Cartão teste", self.novembro["id"])
        lazer = next(item for item in dados["orcamentos"] if item["categoria"] == "Lazer")
        self.assertEqual(5000, lazer["limite_centavos"])

    def test_troca_de_categoria_atualiza_a_analise(self):
        antes = analise_gastos(self.conn, "Cartão teste", self.novembro["id"])
        restaurante = next(item for item in lancamentos(self.conn, self.novembro["id"], "", "", "", 50, 0)["itens"]
                           if item["estabelecimento"] == "Bistro" and item["natureza"] == "debito")
        categorizar_lancamento(self.conn, restaurante["id"], "Restaurantes e delivery", False)
        depois = analise_gastos(self.conn, "Cartão teste", self.novembro["id"])
        self.assertGreater(antes["sem_categoria"]["total_centavos"], depois["sem_categoria"]["total_centavos"])
        categoria = next(item for item in depois["por_categoria"] if item["categoria"] == "Restaurantes e delivery")
        self.assertEqual(2000, categoria["total_centavos"])


if __name__ == "__main__":
    unittest.main()
