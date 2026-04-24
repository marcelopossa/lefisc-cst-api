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

# Exige que "Comerciante atacadista ou varejista" seja SUJEITO de frase
# (início de linha, opcionalmente precedido por "N)") e terminado por ":".
# Evita match quando a expressão aparece como objeto — ex: "...vendas efetuadas
# para comerciante atacadista ou varejista ou para consumidores..." em autopeças.
SUBSECAO_COMERCIANTE = re.compile(
    r"(?:^|\n)\s*(?:\d+\)\s*)?comerciante\s+atacadista\s+ou\s+varejista\s*:",
    re.IGNORECASE,
)

# Possíveis cabeçalhos que marcam fim/início de seção — exige ":" após o rótulo
# para evitar match em palavras adjetivas soltas (ex: "estabelecimento industrial").
SECAO_HEADERS = re.compile(
    r"(contribuinte|n[ãa]o\s+contribuinte|importador|industrial|produtor|"
    r"comerciante\s+atacadista\s+ou\s+varejista|natureza\s+da\s+receita)\s*:",
    re.IGNORECASE,
)

# Formato alternativo do Lefisc em produtos monofásicos: seções rotuladas
# "A) B) C) D)" (cerveja) ou "A. B. C. D." (máquinas/implementos). Aceita ambos.
HEADER_ABCD = re.compile(r"^[A-G][\)\.]\s+\S", re.MULTILINE)


def tem_formato_abcd(texto: str) -> bool:
    """Detecta o formato alternativo A)/B)/C)/D) com ao menos 2 seções."""
    if not texto:
        return False
    return len(HEADER_ABCD.findall(texto)) >= 2


def extrair_secao_abcd_varejista(texto: str) -> str | None:
    """
    Isola a seção A/B/C/D cujo header indica "VENDA EFETUADA POR … VAREJISTA"
    (case-sensitive — o Lefisc usa caixa alta nesse rótulo). Retorna o bloco
    completo dessa seção, do header até o próximo header A-G ou fim do texto.
    Retorna None se o formato ou a seção não forem encontrados.
    """
    if not tem_formato_abcd(texto):
        return None

    headers = list(re.finditer(r"^([A-G])[\)\.]\s+([^\n]+)", texto, re.MULTILINE))
    if not headers:
        return None

    alvo = None
    for h in headers:
        titulo = h.group(2)
        # Header em caixa alta com VAREJISTA
        if re.search(r"\bVAREJISTA\b", titulo):
            alvo = h
            break
        # Fallback: "efetuada por ... varejista" sem negação "não"
        if re.search(r"efetuada\s+por.*varejista", titulo, re.IGNORECASE) and \
           not re.search(r"n[ãa]o\s+varejista", titulo, re.IGNORECASE):
            alvo = h
            break

    if alvo is None:
        return None

    inicio = alvo.start()
    proximo = next(
        (h for h in headers if h.start() > alvo.start()), None
    )
    fim = proximo.start() if proximo else len(texto)
    return texto[inicio:fim].strip()


def extrair_trecho_relevante(texto: str) -> str:
    """
    Isola o trecho que deve ser usado na decisão CST.

    Regra:
      1. Se o texto usa formato A/B/C/D com seção "VAREJISTA" → usa essa seção.
         (produtos monofásicos tipo cerveja onde não há "Não Contribuinte")
      2. Se houver seção "Não Contribuinte" → usa o trecho dentro dela.
      3. Se houver sub-rótulo "Comerciante atacadista ou varejista:" como
         sujeito de frase (início de linha, eventualmente após "N)") → usa
         só esse sub-trecho até o próximo cabeçalho.
      4. Se nada disso existir → usa o texto inteiro.
    """
    if not texto:
        return ""

    # 1) Formato A/B/C/D com header "VAREJISTA"
    secao_abcd = extrair_secao_abcd_varejista(texto)
    if secao_abcd:
        return secao_abcd

    # 2) "Não Contribuinte" tradicional
    m = SECAO_NAO_CONTRIBUINTE.search(texto)
    if m:
        trecho = texto[m.end():]
        trecho = _cortar_ate_proximo_header(trecho, permitir_filhos=True)
    else:
        trecho = texto

    # 3) Sub-seção "Comerciante atacadista ou varejista:" como sujeito
    m2 = SUBSECAO_COMERCIANTE.search(trecho)
    if m2:
        sub = trecho[m2.end():]
        sub = _cortar_ate_proximo_header(sub, permitir_filhos=False)
        return sub.strip()

    return trecho.strip()


def _cortar_ate_proximo_header(texto: str, permitir_filhos: bool) -> str:
    """Corta o texto no próximo cabeçalho de seção encontrado (inclui A/B/C/D)."""
    # Próximo header textual
    candidatos = []
    for m in SECAO_HEADERS.finditer(texto):
        header = m.group(0).lower()
        if permitir_filhos and "comerciante" in header:
            continue
        candidatos.append(m.start())
    # Próximo header A/B/C/D (ex: "B) DEMAIS SITUAÇÕES")
    for m in HEADER_ABCD.finditer(texto):
        candidatos.append(m.start())
    if candidatos:
        return texto[: min(candidatos)]
    return texto


def percentuais_positivos(texto: str) -> list[float]:
    """Retorna lista de percentuais > 0 encontrados no texto (sem filtro de contexto)."""
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


def percentuais_tributacao(texto: str) -> list[float]:
    """
    Retorna apenas alíquotas declaradas em linhas "Regime Cumulativo:" ou
    "Regime Não Cumulativo:". Ignora percentuais que aparecem em notas
    explicativas (ex: "Conceito de varejista - ... 75% ...").
    """
    valores: list[float] = []
    for m in re.finditer(
        r"regime\s+(?:n[ãa]o\s+)?cumulativo[^:]*:\s*([^\n]+)",
        texto,
        re.IGNORECASE,
    ):
        linha = m.group(1)
        for pct in re.findall(r"(\d+[.,]?\d*)\s*%", linha):
            try:
                v = float(pct.replace(",", "."))
                if v > 0:
                    valores.append(v)
            except ValueError:
                continue
    return valores


def tem_pis_cofins(texto: str) -> bool:
    """
    Decide se o trecho relevante indica tributação PIS/COFINS > 0.

    Recebe JÁ o trecho isolado por `extrair_trecho_relevante`. Usa apenas
    percentuais das linhas de Regime (Cumulativo/Não Cumulativo) para evitar
    que percentuais de notas explicativas causem falso positivo.
    """
    if not texto:
        return False

    texto_low = texto.lower()
    percentuais_pos = percentuais_tributacao(texto)

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

    # Formato A/B/C/D é sempre complexo — mesmo quando achamos a seção
    # VAREJISTA corretamente, é prudente marcar como baixa para revisão.
    if tem_formato_abcd(texto_bruto):
        return (
            "baixa",
            "Formato alternativo A/B/C/D — seção VAREJISTA usada, revisar manualmente",
        )

    if detectar_multiplas_secoes(texto_bruto):
        if not SECAO_NAO_CONTRIBUINTE.search(texto_bruto):
            return "baixa", "Múltiplas seções sem 'Não Contribuinte' identificada"
        if not SUBSECAO_COMERCIANTE.search(texto_bruto):
            return (
                "baixa",
                "Seção 'Não Contribuinte' presente mas sem subseção "
                "'Comerciante atacadista ou varejista'",
            )

    percentuais = percentuais_tributacao(trecho_relevante)
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
