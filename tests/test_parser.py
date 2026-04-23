"""
Testes unitários do parser (sem Playwright / sem rede).

Rodar:
    pytest -q
"""
from app.parser import (
    extrair_aliquotas,
    extrair_trecho_relevante,
    tem_pis_cofins,
)


# ---------------------------------------------------------------------------
# Caso 1 — NCM simples, uma única seção (exemplo 48219000 visto no vídeo)
# ---------------------------------------------------------------------------
TEXTO_SIMPLES_TRIBUTADO = """
Alíquotas do PIS e da COFINS:
1. Regime Cumulativo: 0,65% e 3,00%
2. Regime Não Cumulativo: 1,65% e 7,6%
3. Simples Nacional: Tributado Normalmente.
Natureza da Receita: Não há natureza da receita.
"""


def test_simples_tributado_possui_pis_cofins():
    trecho = extrair_trecho_relevante(TEXTO_SIMPLES_TRIBUTADO)
    assert tem_pis_cofins(trecho) is True
    aliq = extrair_aliquotas(trecho)
    assert aliq["aliquota_pis_cumulativo"] == "0,65%"
    assert aliq["aliquota_cofins_cumulativo"] == "3,00%"
    assert aliq["aliquota_pis_nao_cumulativo"] == "1,65%"
    assert aliq["aliquota_cofins_nao_cumulativo"] == "7,6%"


# ---------------------------------------------------------------------------
# Caso 2 — Alíquota Zero explícita
# ---------------------------------------------------------------------------
TEXTO_ALIQUOTA_ZERO = """
Alíquotas do PIS e da COFINS:
1. Regime Cumulativo: Alíquota Zero.
2. Regime Não Cumulativo: Alíquota Zero.
"""


def test_aliquota_zero_nao_possui():
    trecho = extrair_trecho_relevante(TEXTO_ALIQUOTA_ZERO)
    assert tem_pis_cofins(trecho) is False


# ---------------------------------------------------------------------------
# Caso 3 — Múltiplas seções: deve pegar "Não Contribuinte > Comerciante..."
# ---------------------------------------------------------------------------
TEXTO_MULTIPLAS_SECOES = """
Contribuinte
Importador ou Industrial:
    Regime Cumulativo: 2,00% e 9,50%
    Regime Não Cumulativo: 2,00% e 9,50%
Produtor:
    Alíquota Zero
Não Contribuinte
Comerciante atacadista ou varejista:
    Regime Cumulativo: Alíquota Zero
    Regime Não Cumulativo: Alíquota Zero
Natureza da Receita: monofásica
"""


def test_multiplas_secoes_escolhe_nao_contribuinte_comerciante():
    trecho = extrair_trecho_relevante(TEXTO_MULTIPLAS_SECOES)
    assert "Alíquota Zero" in trecho
    assert "2,00%" not in trecho  # não pode vazar da seção Contribuinte
    assert tem_pis_cofins(trecho) is False


# ---------------------------------------------------------------------------
# Caso 4 — Comerciante com alíquotas reais → deve retornar True
# ---------------------------------------------------------------------------
TEXTO_MULTIPLAS_TRIBUTADO_COMERCIANTE = """
Contribuinte
Industrial:
    Regime Cumulativo: Alíquota Zero
Não Contribuinte
Comerciante atacadista ou varejista:
    Regime Cumulativo: 0,65% e 3,00%
    Regime Não Cumulativo: 1,65% e 7,60%
"""


def test_multiplas_secoes_comerciante_tributado():
    trecho = extrair_trecho_relevante(TEXTO_MULTIPLAS_TRIBUTADO_COMERCIANTE)
    assert tem_pis_cofins(trecho) is True
    aliq = extrair_aliquotas(trecho)
    assert aliq["aliquota_pis_cumulativo"] == "0,65%"
    assert aliq["aliquota_cofins_nao_cumulativo"] == "7,60%"


# ---------------------------------------------------------------------------
# Caso 5 — Só ignorar caracteres não-dígito no texto
# ---------------------------------------------------------------------------
def test_texto_vazio():
    assert extrair_trecho_relevante("") == ""
    assert tem_pis_cofins("") is False
