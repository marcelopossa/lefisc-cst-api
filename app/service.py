"""
Serviço de consulta CST.

Encapsula:
- normalização do NCM
- cache em memória (TTL)
- orquestração da chamada ao scraper
- regra de negócio final: possui PIS/COFINS → 1, caso contrário → 4
"""
from __future__ import annotations

import asyncio
import logging
import re

from app.cache import SQLiteCache
from app.config import settings
from app.models import CSTResponse
from app.parser import calcular_confianca
from app.scraper import NCMResult, scraper

logger = logging.getLogger(__name__)

# Cache persistente em SQLite, chave = NCM normalizado
_cache = SQLiteCache(
    db_path=settings.cache_db_path, ttl_seconds=settings.cache_ttl_seconds
)
_cache_lock = asyncio.Lock()


def normalizar_ncm(ncm: str) -> str:
    """Remove tudo que não é dígito. Lefisc aceita NCM incompleto, então
    não validamos tamanho aqui — o próprio site trata."""
    return re.sub(r"\D", "", ncm or "")


def _resultado_para_response(ncm_normalizado: str, r: NCMResult) -> CSTResponse:
    cst = 1 if r.possui_pis_cofins else 4
    confianca, motivo = calcular_confianca(r.pis_cofins_texto, r.trecho_relevante)
    return CSTResponse(
        ncm=r.ncm or ncm_normalizado,
        cst=cst,
        possui_pis_cofins=r.possui_pis_cofins,
        confianca=confianca,
        revisao_necessaria=(confianca == "baixa"),
        motivo_revisao=motivo,
        descricao=r.descricao,
        aliquota_pis_cumulativo=r.aliquota_pis_cumulativo,
        aliquota_cofins_cumulativo=r.aliquota_cofins_cumulativo,
        aliquota_pis_nao_cumulativo=r.aliquota_pis_nao_cumulativo,
        aliquota_cofins_nao_cumulativo=r.aliquota_cofins_nao_cumulativo,
        raw_text=r.pis_cofins_texto,
        trecho_relevante=r.trecho_relevante,
    )


async def consultar_cst(ncm: str) -> CSTResponse:
    """Ponto de entrada do serviço."""
    ncm_norm = normalizar_ncm(ncm)
    if not ncm_norm:
        raise ValueError("NCM inválido: precisa conter pelo menos um dígito.")

    # Cache hit
    if ncm_norm in _cache:
        logger.debug("Cache HIT para NCM %s", ncm_norm)
        return _cache[ncm_norm]

    async with _cache_lock:
        # Double-check por concorrência
        if ncm_norm in _cache:
            return _cache[ncm_norm]

        logger.info("Cache MISS para NCM %s — consultando Lefisc", ncm_norm)
        resultado = await scraper.consultar_ncm(ncm_norm)
        response = _resultado_para_response(ncm_norm, resultado)
        _cache[ncm_norm] = response
        return response


def limpar_cache() -> int:
    """Esvazia o cache e retorna quantas entradas foram removidas."""
    return _cache.clear()


def purgar_expirados() -> int:
    """Remove só as entradas expiradas. Retorna quantas foram removidas."""
    return _cache.purge_expired()
