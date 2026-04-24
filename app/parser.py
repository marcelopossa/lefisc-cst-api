"""
Parser da coluna PIS/COFINS do Lefisc (sem dependência de Playwright).

Isolado do scraper pra facilitar testes unitários.

Regra de negócio (informada pelo usuário):
- Em alguns NCMs a coluna PIS/COFINS tem MAIS DE UMA seção (ex: "Contribuinte",
  "Não Contribuinte", "Importador", "Industrial"...).
- A seção relevante é SEMPRE "Não Contribuinte".
- Dentro de "Não Contribuinte", o subtrecho que vale é o de
  "Comerciante atacadista ou varejista".
- Se essa subseção trouxer alíquotas > 0 → CST 1.
- Se trouxer "Alíquota Zero" / "0,00%" / "Monofásico" → CST 4.
"""
from __future__ import annotations

import re
from typing import Literal


ALIQUOTA_ZERO_PATTERNS = [
    r"al[ií]quota\s+zero",
    r"isent[ao]",
    r"n[ãa]o\s+tribut",
    r"monof[áa]sic",
    r"0,00\s*%.*0,00\s*%",
]

# Marcadores de seção na coluna PIS/COFINS
SECAO_NAO_CONTRIBUINTE = re.compile(r"n[ãa]o\s+contribuinte", re.IGNORECASE)
SUBSECAO_COMERCIANTE = re.compile(
    r"comerciante\s+atacadista\s+ou\s+varejista", re.IGNORECASE
)
# Possíveis cabeçalhos que marcam fim/início de seção
SECAO_HEADERS = re.compile(
    r"(contribuinte|n[ãa]o\s+contribuinte|importador|industrial|produtor|"
    r"comerciante\s+atacadista\s+ou\s+varejista|natureza\s+da\s+receita)",
    re.IGNORECASE,
)


def extrair_trecho_relevante(texto: str) -> str:
    """
    Isola o trecho que deve ser usado na decisão CST.

    Regra:
      1. Se houver seção "Não Contribuinte" → usa o trecho dentro dela.
      2. Dentro desse trecho, se houver sub-rótulo "Comerciante atacadista
         ou varejista" → usa só esse sub-trecho até o próximo cabeçalho.
      3. Se nada disso existir → usa o texto inteiro (NCMs sem seccionamento).
    """
    if not texto:
        return ""

    m = SECAO_NAO_CONTRIBUINTE.search(texto)
    if m:
        trecho = texto[m.end():]
        trecho = _cortar_ate_proximo_header(trecho, permitir_filhos=True)
    else:
        trecho = texto

    m2 = SUBSECAO_COMERCIANTE.search(trecho)
    if m2:
        sub = trecho[m2.end():]
        sub = _cortar_ate_proximo_header(sub, permitir_filhos=False)
        return sub.strip()

    return trecho.strip()


def _cortar_ate_proximo_header(texto: str, permitir_filhos: bool) -> str:
    """Corta o texto no próximo cabeçalho de seção encontrado."""
    for m in SECAO_HEADERS.finditer(texto):
        header = m.group(0).lower()
        if permitir_filhos and "comerciante" in header:
            continue
        return texto[: m.start()]
    return texto


def percentuais_positivos(texto: str) -> list[float]:
    """Retorna lista de percentuais > 0 encontrados no texto."""
    matches = re.findall(r"(\d+[.,]?\d*)\s*%", texto)
    valores = []
    for m in matches:
        try:
            v = float(m.replace(",", "."))
            if v > 0:
                valores.append(v)
        except ValueError:
            continue
    return valores


def tem_pis_cofins(texto: str) -> bool:
    """
    Decide se o trecho relevante indica tributação PIS/COFINS > 0.

    Recebe JÁ o trecho isolado por `extrair_trecho_relevante`.
    """
    if not texto:
        return False

    texto_low = texto.lower()
    percentuais_pos = percentuais_positivos(texto)

    for pat in ALIQUOTA_ZERO_PATTERNS:
        if re.search(pat, texto_low) and not percentuais_pos:
            return False

    return len(percentuais_pos) > 0


def detectar_multiplas_secoes(texto: str) -> bool:
    """Detecta se a célula tem múltiplas seções por tipo de contribuinte."""
    marcadores = [
        r"\bcontribuinte\b",
        r"n[ãa]o\s+contribuinte",
        r"\bimportador\b",
        r"\bindustrial\b",
        r"\bprodutor\b",
    ]
    encontradas = sum(1 for p in marcadores if re.search(p, texto, re.IGNORECASE))
    return encontradas >= 2


def calcular_confianca(
    texto_bruto: str, trecho_relevante: str
) -> tuple[Literal["alta", "baixa"], str | None]:
    """
    Retorna (confianca, motivo_revisao).

    "alta" → decisão baseada em regra clara e texto bem estruturado.
    "baixa" → algum passo de parsing foi ambíguo; humano deve revisar.
    """
    if not texto_bruto:
        return "baixa", "Texto PIS/COFINS vazio"

    if detectar_multiplas_secoes(texto_bruto):
        if not SECAO_NAO_CONTRIBUINTE.search(texto_bruto):
            return "baixa", "Múltiplas seções sem 'Não Contribuinte' identificada"
        if not SUBSECAO_COMERCIANTE.search(texto_bruto):
            return (
                "baixa",
                "Seção 'Não Contribuinte' presente mas sem subseção "
                "'Comerciante atacadista ou varejista'",
            )

    percentuais = percentuais_positivos(trecho_relevante)
    tem_zero = any(
        re.search(pat, trecho_relevante.lower()) for pat in ALIQUOTA_ZERO_PATTERNS
    )
    if not percentuais and not tem_zero:
        return "baixa", "Sem alíquotas ou padrão zero/isento no trecho relevante"

    return "alta", None


def extrair_aliquotas(texto: str) -> dict:
    """
    Extrai alíquotas de PIS e COFINS dos regimes Cumulativo e Não Cumulativo.

    Formato esperado do Lefisc:
      "Regime Cumulativo: 0,65% e 3,00%"
      "Regime Não Cumulativo: 1,65% e 7,6%"
    """
    out: dict[str, str | None] = {
        "aliquota_pis_cumulativo": None,
        "aliquota_cofins_cumulativo": None,
        "aliquota_pis_nao_cumulativo": None,
        "aliquota_cofins_nao_cumulativo": None,
    }

    cum = re.search(
        r"regime\s+cumulativo[^:]*:\s*([\d.,]+\s*%)\s*e\s*([\d.,]+\s*%)",
        texto,
        re.IGNORECASE,
    )
    if cum:
        out["aliquota_pis_cumulativo"] = cum.group(1).strip()
        out["aliquota_cofins_cumulativo"] = cum.group(2).strip()

    nao_cum = re.search(
        r"regime\s+n[ãa]o\s+cumulativo[^:]*:\s*([\d.,]+\s*%)\s*e\s*([\d.,]+\s*%)",
        texto,
        re.IGNORECASE,
    )
    if nao_cum:
        out["aliquota_pis_nao_cumulativo"] = nao_cum.group(1).strip()
        out["aliquota_cofins_nao_cumulativo"] = nao_cum.group(2).strip()

    return out
