"""
Testes unitários do parser (sem Playwright / sem rede).

Rodar:
    pytest -q
"""
import json
from pathlib import Path

import pytest

from app.parser import (
    calcular_confianca,
    extrair_aliquotas,
    extrair_secao_abcd_varejista,
    extrair_trecho_relevante,
    tem_formato_abcd,
    tem_pis_cofins,
)

FIXTURES_PATH = Path(__file__).parent / "fixtures.json"


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


# ---------------------------------------------------------------------------
# Caso 6 — Autopeças monofásicas (NCM 8414.80.21, 8414.90.39)
# Estrutura real do Lefisc: A) ... 1) Fabricantes... 2) Fabricantes vendendo
# para comerciante... 3) Comerciante atacadista ou varejista: alíquota zero.
# O parser antigo capturava o item 2 (como objeto da frase). Agora deve achar
# o item 3 onde "Comerciante..." é SUJEITO (após "\n3) ", terminado por ":").
# ---------------------------------------------------------------------------
TEXTO_AUTOPECA_MONOFASICA = """A) ENQUADRADO NOS ANEXOS I OU II DA LEI Nº 10.485/2002 (NOVOS)

1) Fabricantes e Importadores: vendas efetuadas para fabricante de veículos...
Regime Cumulativo: 1,65% e 7,6%
Regime Não Cumulativo: 1,65% e 7,6%

2) Fabricantes e Importadores: vendas efetuadas para comerciante atacadista ou varejista ou para consumidores...
Regime Cumulativo: 2,3% e 10,8%
Regime Não Cumulativo: 2,3% e 10,8%

3) Comerciante atacadista ou varejista: Ficam reduzidas a 0% as alíquotas...
Regime Cumulativo: Alíquota Zero.
Regime Não Cumulativo: Alíquota Zero.

B) DEMAIS SITUAÇÕES (inclusive usados)

Regime Cumulativo: 0,65% e 3,00%
Regime Não Cumulativo: 1,65% e 7,6%
"""


def test_autopeca_pega_item_3_comerciante_como_sujeito():
    trecho = extrair_trecho_relevante(TEXTO_AUTOPECA_MONOFASICA)
    # Deve pegar o item 3 (alíquota zero), NÃO o item 2 (2,3%/10,8%)
    assert "Alíquota Zero" in trecho
    assert "2,3%" not in trecho
    assert "10,8%" not in trecho


def test_autopeca_cst_4():
    trecho = extrair_trecho_relevante(TEXTO_AUTOPECA_MONOFASICA)
    assert tem_pis_cofins(trecho) is False


# ---------------------------------------------------------------------------
# Caso 7 — Formato A/B/C/D onde a seção-alvo tem header "VAREJISTA"
# (típico de cerveja — NCM 22030000)
# ---------------------------------------------------------------------------
TEXTO_CERVEJA_ABCD = """A) REGRA GERAL (VENDA PARA DEMAIS PESSOAS JURÍDICAS)

Regime Cumulativo: 2,32% e 10,68%
Regime Não Cumulativo: 2,32% e 10,68%

B) Venda para pessoa jurídica varejista ou Consumidor Final efetuada por não varejista

Regime Cumulativo: 1,86% e 8,54%
Regime Não Cumulativo: 1,86% e 8,54%

C) VENDA EFETUADA POR PESSOA JURÍDICA VAREJISTA

Regime Cumulativo: Alíquota Zero.
Regime Não Cumulativo: Alíquota Zero.
Simples Nacional: Alíquota Zero.

D) INDUSTRIALIZAÇÃO POR ENCOMENDA

Regime Cumulativo: 1,65% e 7,6%
Regime Não Cumulativo: 1,65% e 7,6%
"""


def test_formato_abcd_detecta():
    assert tem_formato_abcd(TEXTO_CERVEJA_ABCD) is True
    assert tem_formato_abcd(TEXTO_SIMPLES_TRIBUTADO) is False


def test_formato_abcd_isola_secao_c_varejista():
    secao = extrair_secao_abcd_varejista(TEXTO_CERVEJA_ABCD)
    assert secao is not None
    assert "VAREJISTA" in secao
    assert "Alíquota Zero" in secao
    # Não deve vazar a seção D (industrialização)
    assert "INDUSTRIALIZAÇÃO" not in secao
    # Nem as seções A ou B
    assert "2,32%" not in secao
    assert "1,86%" not in secao


def test_formato_abcd_extrair_trecho_usa_secao_varejista():
    trecho = extrair_trecho_relevante(TEXTO_CERVEJA_ABCD)
    assert "Alíquota Zero" in trecho
    # Nunca deve ter as alíquotas das outras seções
    assert "2,32%" not in trecho
    assert "1,86%" not in trecho
    assert "1,65%" not in trecho


def test_formato_abcd_cst_4():
    trecho = extrair_trecho_relevante(TEXTO_CERVEJA_ABCD)
    assert tem_pis_cofins(trecho) is False


def test_formato_abcd_sempre_flaga_revisao():
    trecho = extrair_trecho_relevante(TEXTO_CERVEJA_ABCD)
    confianca, motivo = calcular_confianca(TEXTO_CERVEJA_ABCD, trecho)
    assert confianca == "baixa"
    assert motivo is not None
    assert "A/B/C/D" in motivo


# ---------------------------------------------------------------------------
# Caso 8 — Texto genérico curto (NCM 84148029) = tributação normal
# O parser NÃO deve flagar review. Texto genérico = CST 1 com alta confiança.
# ---------------------------------------------------------------------------
TEXTO_GENERICO_CURTO = """Alíquotas do PIS e da COFINS:

Regime Cumulativo: 0,65% e 3,00%
Regime Não Cumulativo: 1,65% e 7,6%
Simples Nacional: Tributado Normalmente.
Natureza da Receita: Não há natureza da receita para fins de escrituração da EFD-Contribuições nas operações de saída tributável."""


def test_texto_generico_curto_cst_1():
    trecho = extrair_trecho_relevante(TEXTO_GENERICO_CURTO)
    assert tem_pis_cofins(trecho) is True


def test_texto_generico_curto_mantem_alta_confianca():
    trecho = extrair_trecho_relevante(TEXTO_GENERICO_CURTO)
    confianca, motivo = calcular_confianca(TEXTO_GENERICO_CURTO, trecho)
    assert confianca == "alta"
    assert motivo is None


# ---------------------------------------------------------------------------
# Caso 9 — Regressão contra fixtures reais capturados do Lefisc
# Cada entrada em tests/fixtures.json valida o comportamento end-to-end do
# parser em texto real. Roda sem rede (raw_text já está salvo).
# ---------------------------------------------------------------------------
def _carregar_fixtures():
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    return [(f["ncm"], f) for f in data["fixtures"]]


@pytest.mark.parametrize("ncm,fixture", _carregar_fixtures())
def test_fixtures_reais_cst_bate(ncm, fixture):
    trecho = extrair_trecho_relevante(fixture["raw_text"])
    possui = tem_pis_cofins(trecho)
    cst = 1 if possui else 4
    assert cst == fixture["cst_esperado"], (
        f"NCM {ncm}: CST={cst} mas esperado={fixture['cst_esperado']}. "
        f"Tipo: {fixture['tipo']}. Obs: {fixture['observacoes']}"
    )
    assert possui == fixture["possui_pis_cofins_esperado"]


@pytest.mark.parametrize("ncm,fixture", _carregar_fixtures())
def test_fixtures_reais_confianca_bate(ncm, fixture):
    trecho = extrair_trecho_relevante(fixture["raw_text"])
    confianca, motivo = calcular_confianca(fixture["raw_text"], trecho)
    assert confianca == fixture["confianca_esperada"], (
        f"NCM {ncm}: confianca={confianca} mas esperado={fixture['confianca_esperada']}. "
        f"Motivo do parser: {motivo!r}"
    )
    revisao_atual = confianca == "baixa"
    assert revisao_atual == fixture["revisao_esperada"]
